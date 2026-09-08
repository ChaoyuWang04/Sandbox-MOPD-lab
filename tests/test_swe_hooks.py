import asyncio
import base64
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'testbed'
        self.root.mkdir()
        self.git('init')
        (self.root / 'code.py').write_text('broken\n')
        (self.root / 'test.py').write_text('healthy test\n')
        self.git('add', 'code.py', 'test.py')
        self.git('commit', '-m', 'healthy')
        self.healthy = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'test.py').unlink()
        self.git('add', 'test.py')
        self.git('commit', '-m', 'remove tests')
        self.head = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'cache').write_text('untracked')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], cwd=self.root, stderr=subprocess.DEVNULL).decode()

    def prepare(self, **kw):
        from lab_runtime.swe_workspace import prepare_workspace
        return prepare_workspace(str(self.root), self.head, 'smith', 'fixture', ['code.py'], disposable=True, **kw)

    def test_fresh_root_preserves_bytes_and_private_restore(self):
        result = self.prepare()
        self.assertEqual(self.git('rev-list', '--all', '--count').strip(), '1')
        self.assertEqual(self.git('remote'), '')
        self.assertEqual(self.git('symbolic-ref', 'HEAD').strip(), 'refs/heads/workspace')
        self.assertEqual((self.root / 'code.py').read_text(), 'broken\n')
        self.assertEqual((self.root / 'cache').read_text(), 'untracked')
        self.assertNotIn('cache', self.git('ls-files'))
        self.assertIn('healthy test', base64.b64decode(result['restore_patch_b64']).decode())
        self.assertNotEqual(result['baseline_commit'], self.head)
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('cat-file', '-e', self.healthy)

    def test_wrong_head_refuses_before_removal(self):
        from lab_runtime.swe_workspace import prepare_workspace
        with self.assertRaises(ValueError):
            prepare_workspace(str(self.root), '0' * 40, 'smith', 'fixture', [], disposable=True)
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.head)

    def test_symlink_refuses(self):
        metadata = self.root / '.git'
        metadata.rename(self.root / 'metadata')
        metadata.symlink_to('metadata', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.root / 'metadata').is_dir())

    def test_overlap_refuses(self):
        from lab_runtime.swe_workspace import prepare_workspace
        with self.assertRaises(ValueError):
            prepare_workspace(str(self.root), self.head, 'smith', 'fixture', ['test.py'], disposable=True)
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.head)

    def test_not_disposable_refuses(self):
        from lab_runtime.swe_workspace import prepare_workspace
        with self.assertRaises(ValueError):
            prepare_workspace(str(self.root), self.head, 'gym', 'fixture', [])
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.head)

    def test_arbitrary_repository_name_refused(self):
        from lab_runtime.swe_workspace import prepare_workspace
        other = self.root.with_name('lab-repository')
        self.root.rename(other)
        with self.assertRaises(ValueError):
            prepare_workspace(str(other), self.head, 'gym', 'fixture', [], disposable=True)
        self.assertTrue((other / '.git').is_dir())

    def test_gym_does_not_restore_parent(self):
        from lab_runtime.swe_workspace import prepare_workspace
        receipt = prepare_workspace(str(self.root), self.head, 'gym', 'fixture', [], disposable=True)
        self.assertEqual(receipt['restore_patch_b64'], '')

    def test_hook_absence_failure_blocks_verifier(self):
        from harbor.trial.hooks import TrialEvent
        from lab_runtime.swe_hooks import attach_swe_hooks
        hooks = {}
        class Environment:
            async def exec(self, **kwargs):
                return SimpleNamespace(return_code=1, stdout='', stderr='private payload present')
        trial = SimpleNamespace(agent_environment=Environment(), add_hook=lambda event, hook: hooks.update({event: hook}))
        evidence = attach_swe_hooks(trial, Path(self.tmp.name) / 'private', 'gym', {'instance_id': 'fixture', 'base_commit': self.head}, [], 'nop')
        async def exercise():
            with self.assertRaises(RuntimeError):
                await hooks[TrialEvent.AGENT_START](None)
            with self.assertRaises(RuntimeError):
                await hooks[TrialEvent.VERIFICATION_START](None)
        asyncio.run(exercise())
        self.assertEqual(evidence['phase'], 'preparing')

    def test_hook_exec_outer_timeout(self):
        from harbor.trial.hooks import TrialEvent
        from lab_runtime.swe_hooks import attach_swe_hooks
        hooks = {}
        class Environment:
            async def exec(self, **kwargs):
                await asyncio.sleep(10)
        trial = SimpleNamespace(agent_environment=Environment(), add_hook=lambda event, hook: hooks.update({event: hook}))
        attach_swe_hooks(trial, Path(self.tmp.name) / 'private', 'gym', {'instance_id': 'fixture', 'base_commit': self.head}, [], 'nop', hook_timeout=.01)
        async def exercise():
            with self.assertRaises(asyncio.TimeoutError):
                await hooks[TrialEvent.AGENT_START](None)
        asyncio.run(exercise())

    def test_model_agent_uses_same_private_absence_boundary(self):
        from harbor.trial.hooks import TrialEvent
        from lab_runtime.swe_hooks import attach_swe_hooks
        hooks = {}
        commands = []
        class Environment:
            async def exec(self, command, **kwargs):
                commands.append(command)
                return SimpleNamespace(return_code=1, stdout='', stderr='fixture stop')
        trial = SimpleNamespace(agent_environment=Environment(),
            add_hook=lambda event, hook: hooks.update({event: hook}))
        attach_swe_hooks(trial, Path(self.tmp.name) / 'private', 'gym',
            {'instance_id': 'fixture', 'base_commit': self.head}, [], 'm1')
        async def exercise():
            with self.assertRaises(RuntimeError):
                await hooks[TrialEvent.AGENT_START](None)
        asyncio.run(exercise())
        self.assertIn('test ! -e /solution', commands[0])

    def test_real_harbor_registration_and_phase_upload(self):
        from harbor.trial.trial import Trial
        from harbor.trial.hooks import TrialEvent
        from lab_runtime.swe_hooks import attach_swe_hooks
        outer = self
        class Environment:
            uploads = []
            async def exec(self, command, timeout_sec=None, **kwargs):
                # Only the workspace script executes locally, against our fixture.
                if command.startswith('python3 -I'):
                    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout_sec)
                    return SimpleNamespace(return_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
                return SimpleNamespace(return_code=0, stdout='', stderr='')
            async def upload_file(self, source_path, target_path):
                self.uploads.append((target_path, Path(source_path).read_bytes()))
        class LocalTrial(Trial):
            async def _run(self):
                pass
            async def _recover_outputs(self):
                pass
        trial = LocalTrial.__new__(LocalTrial)
        trial._hooks = defaultdict(list)
        trial.agent_environment = Environment()
        evidence = attach_swe_hooks(trial, Path(self.tmp.name) / 'private', 'smith', {'instance_id': 'fixture', 'instance_commit': self.head}, ['code.py'], 'nop', workspace_root=str(self.root), disposable=True)
        async def exercise():
            with self.assertRaises(RuntimeError):
                await trial._hooks[TrialEvent.VERIFICATION_START][0](None)
            await trial._hooks[TrialEvent.AGENT_START][0](None)
            self.assertFalse(trial.agent_environment.uploads)
            await trial._hooks[TrialEvent.AGENT_END][0](None)
            await trial._hooks[TrialEvent.VERIFICATION_START][0](None)
        asyncio.run(exercise())
        self.assertEqual(evidence['phase'], 'verification_ready')
        self.assertEqual({p for p, _ in trial.agent_environment.uploads}, {'/tests/prepared.json', '/tests/restore-tests.patch'})


if __name__ == '__main__':
    unittest.main()
