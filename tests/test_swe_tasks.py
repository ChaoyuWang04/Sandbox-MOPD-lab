"""Local synthetic git repositories only; never execute benchmark source."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from lab_runtime.swe_grading import SMITH_PARSER


GOLD = 'diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n'
TEST = 'diff --git a/test_small.py b/test_small.py\nnew file mode 100644\n--- /dev/null\n+++ b/test_small.py\n@@ -0,0 +1,2 @@\n+from code import value\n+def test_bug(): assert value == 1\n'


def fixture(source='gym'):
    record = dict(instance_id='owner__repo-123', repo='owner/repo', base_commit='a'*40,
                  version='1', problem_statement='Fix the original issue.\n', patch=GOLD,
                  test_patch=TEST, FAIL_TO_PASS=['test_small.py::test_bug'], PASS_TO_PASS=[])
    profile = dict(instance_id=record['instance_id'], repo='owner/repo', base_commit='a'*40, version='1',
                   image_name='owner/image', image_digest='sha256:'+'b'*64,
                   test_command='pytest -q', parser_identity=SMITH_PARSER,
                   eval_commands=[], verify_reinstall_command='')
    if source == 'smith':
        record['image_name'] = profile['image_name']
    return record, profile


class TaskTests(unittest.TestCase):
    def render(self, source='gym'):
        from lab_runtime.swe_tasks import render_swe_task
        r, p = fixture(source)
        return render_swe_task(r, p, source, dict(agent_timeout_sec=300, verifier_timeout_sec=600))

    def test_private_packaging_and_determinism(self):
        result = self.render()
        self.assertEqual(result, self.render())
        files = result['files']
        self.assertEqual(files['instruction.md'], b'Fix the original issue.\n')
        self.assertEqual(files['solution/gold.patch'], GOLD.encode())
        self.assertNotIn(GOLD.encode(), files['tests/private.json'])
        self.assertNotIn(b'COPY', files['environment/Dockerfile'])
        self.assertIn(b'@sha256:', files['environment/Dockerfile'])
        self.assertNotIn(b'--reverse', files['solution/solve.sh'])
        self.assertIn(b'--reverse', self.render('smith')['files']['solution/solve.sh'])
        self.assertIn(b'--check', files['solution/solve.sh'])
        self.assertNotIn('tests/prepared.json', files)
        self.assertIn(b'git checkout --detach FETCH_HEAD', self.render('smith')['files']['environment/Dockerfile'])

    def test_optional_smith_instance_commit(self):
        from lab_runtime.swe_tasks import render_swe_task
        r, p = fixture('smith')
        p['instance_commit'] = 'c'*40
        result = render_swe_task(r, p, 'smith', {})
        self.assertIn(('git fetch origin ' + 'c'*40).encode(), result['files']['environment/Dockerfile'])
        p['instance_commit'] = 'unsafe;cmd'
        with self.assertRaises(ValueError):
            render_swe_task(r, p, 'smith', {})

    def test_identity_and_unsafe_patch_rejected(self):
        from lab_runtime.swe_tasks import render_swe_task
        r, p = fixture()
        for key, value in [('repo', 'other/repo'), ('base_commit', 'c'*40)]:
            with self.assertRaises(ValueError):
                render_swe_task(dict(r, **{key:value}), p, 'gym', {})
        for patch in [GOLD.replace('code.py', '../escape'), GOLD.replace('code.py', '.git/config')]:
            with self.assertRaises(ValueError):
                render_swe_task(dict(r, patch=patch), p, 'gym', {})

    def test_same_repo_smith_profile_swap_is_rejected(self):
        from lab_runtime.swe_tasks import render_swe_task
        r, p = fixture('smith')
        p['instance_commit'] = 'c'*40
        other = dict(p, instance_id='owner__repo-456', instance_commit='d'*40)
        with self.assertRaises(ValueError):
            render_swe_task(r, other, 'smith', {})
        render_swe_task(r, p, 'smith', {})
        for source in ('smith', 'gym'):
            r, p = fixture(source)
            del p['instance_id']
            with self.assertRaises(ValueError):
                render_swe_task(r, p, source, {})

    def test_materialization_and_harbor(self):
        from lab_runtime.swe_tasks import materialize_swe_task
        from harbor.models.task.task import Task
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()/'task'
            result = self.render()
            materialize_swe_task(path, result)
            materialize_swe_task(path, result)
            task = Task(task_dir=path)
            self.assertEqual(task.config.environment.memory_mb, 8192)
            (path/'foreign').write_text('x')
            with self.assertRaises(ValueError):
                materialize_swe_task(path, result)

    def test_safe_rename_patch_paths(self):
        from lab_runtime.swe_verify import patch_paths
        patch = 'diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n'
        self.assertEqual(patch_paths(patch), ['new.py', 'old.py'])
        with self.assertRaises(ValueError):
            patch_paths(patch.replace('rename to new.py', 'rename to ../escape'))

    def test_hash_mismatch_and_symlink_destination(self):
        from lab_runtime.swe_tasks import materialize_swe_task
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'link').symlink_to(root/'target')
            with self.assertRaises(ValueError):
                materialize_swe_task(root/'link', self.render())
            result = self.render()
            result['files']['instruction.md'] = b'changed'
            with self.assertRaises(ValueError):
                materialize_swe_task(root/'task', result)


class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = os.environ.get('PYTEST_TEST_PYTHON', os.environ.get('SWE_TEST_PYTHON', sys.executable))
        check = subprocess.run([cls.python, '-c', 'import pytest'], capture_output=True)
        if check.returncode:
            raise unittest.SkipTest('real pytest unavailable; set SWE_TEST_PYTHON to isolated test Python')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'repo'
        self.repo.mkdir()
        self.private = self.root/'private'
        self.private.mkdir()
        self.output = self.root/'output'
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo/'code.py').write_text('value = 0\n')
        self.git('add', 'code.py')
        self.git('commit', '-qm', 'fresh baseline')
        self.baseline = self.git('rev-parse', 'HEAD').strip()
        self.config = dict(source='smith', source_id='owner__repo-123',
            parser_identity=SMITH_PARSER, fail_to_pass=['test_small.py::test_bug'], pass_to_pass=[],
            gold_patch_paths=['code.py'], test_patch_paths=['test_small.py'],
            profile=dict(test_command=shlex.quote(self.python)+' -m pytest -q -p no:cacheprovider',
                         activation_command='', eval_commands=[], verify_reinstall_command=''), timeout=10)
        (self.private/'private.json').write_text(json.dumps(self.config))
        (self.private/'restore-tests.patch').write_text(TEST)
        (self.private/'test.patch').write_text(TEST)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def receipt(self, **updates):
        payload = dict(source_id=self.config['source_id'], baseline_commit=self.baseline)
        payload.update(updates)
        (self.private/'prepared.json').write_text(json.dumps(payload))

    def verify(self):
        from lab_runtime.swe_verify import verify_swe
        return verify_swe(self.private, self.output, cwd=self.repo)

    def test_missing_or_mismatched_preparation_is_invalid(self):
        self.assertEqual(self.verify()['outcome'], 'invalid')
        self.assertFalse((self.output/'reward.txt').exists())
        self.receipt(source_id='wrong')
        self.assertEqual(self.verify()['outcome'], 'invalid')

    def test_smith_restores_only_tests_and_preserves_committed_fix(self):
        self.receipt()
        (self.repo/'code.py').write_text('value = 1\n')
        self.git('add', 'code.py')
        self.git('commit', '-qm', 'agent fix')
        self.assertEqual(self.verify()['reward'], 1)
        self.assertEqual((self.repo/'code.py').read_text(), 'value = 1\n')

    def test_gym_new_test_reset_and_reinstall_fix_guard(self):
        self.receipt()
        self.config['source'] = 'gym'
        self.config['parser_identity'] = 'swebench.harness.log_parsers.parse_log_moto'
        self.config['profile']['verify_reinstall_command'] = 'git checkout HEAD -- code.py'
        (self.private/'private.json').write_text(json.dumps(self.config))
        (self.repo/'code.py').write_text('value = 1\n')
        self.assertEqual(self.verify()['outcome'], 'invalid')
        self.assertFalse((self.output/'reward.txt').exists())
        self.assertIn('implementation_changed', (self.output/'prepare.json').read_text())
        self.config['source'] = 'smith'
        self.config['profile']['verify_reinstall_command'] = ''
        (self.private/'private.json').write_text(json.dumps(self.config))

    def test_symlink_restore_refused(self):
        self.receipt()
        (self.repo/'test_small.py').symlink_to(self.root/'external')
        self.assertEqual(self.verify()['outcome'], 'invalid')

    def test_gym_resets_new_test_only_and_preserves_fix(self):
        self.receipt()
        self.config['source'] = 'gym'
        self.config['parser_identity'] = 'swebench.harness.log_parsers.parse_log_moto'
        self.config['profile']['eval_commands'] = ['printf activated > activation-marker']
        self.config['profile']['verify_reinstall_command'] = 'test -f activation-marker'
        (self.private/'private.json').write_text(json.dumps(self.config))
        (self.repo/'code.py').write_text('value = 1\n')
        (self.repo/'test_small.py').write_text('raise Exception("agent edit")\n')
        self.assertEqual(self.verify()['reward'], 1)
        self.assertEqual((self.repo/'code.py').read_text(), 'value = 1\n')

    def test_gym_eval_export_survives_into_test_command(self):
        self.receipt()
        self.config['source'] = 'gym'
        self.config['parser_identity'] = 'swebench.harness.log_parsers.parse_log_moto'
        self.config['profile']['eval_commands'] = ['export MOPD_SYNTHETIC_CHECK=present']
        self.config['profile']['test_command'] = 'test "$MOPD_SYNTHETIC_CHECK" = present; ' + self.config['profile']['test_command']
        (self.private/'private.json').write_text(json.dumps(self.config))
        (self.repo/'code.py').write_text('value = 1\n')
        self.assertEqual(self.verify()['reward'], 1)

    def test_nonroot_receipt_and_overlapping_restore_are_invalid(self):
        self.git('commit', '--allow-empty', '-qm', 'not root')
        self.receipt(baseline_commit=self.git('rev-parse', 'HEAD').strip())
        self.assertEqual(self.verify()['outcome'], 'invalid')
        self.receipt()
        (self.private/'restore-tests.patch').write_text(GOLD)
        self.assertEqual(self.verify()['outcome'], 'invalid')

    def test_install_failure_logs_without_reward(self):
        self.receipt()
        self.config['source'] = 'gym'
        self.config['profile']['verify_reinstall_command'] = 'echo synthetic-install-error; exit 2'
        (self.private/'private.json').write_text(json.dumps(self.config))
        self.assertEqual(self.verify()['outcome'], 'invalid')
        self.assertIn('synthetic-install-error', (self.output/'prepare.log').read_text())
        self.assertFalse((self.output/'reward.txt').exists())
