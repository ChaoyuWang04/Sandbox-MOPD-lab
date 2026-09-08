import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lab_runtime import catalog_run as c


class CatalogRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.wheel = self.root / 'data/m1/v2/sources/tools/pyarrow.whl'
        self.wheel.parent.mkdir(parents=True)
        self.wheel.write_bytes(b'wheel')
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'assets': [{'path': 'tools/pyarrow.whl',
            'sha256': hashlib.sha256(b'wheel').hexdigest(), 'size': 5}]}))

    def run_catalog(self, runner, **kwargs):
        return c.run(self.root, self.manifest, runner=runner, **kwargs)

    def test_hash_before_any_subprocess(self):
        self.wheel.write_bytes(b'wrong')
        calls = []
        result = self.run_catalog(lambda *a, **k: calls.append(a))
        self.assertEqual(result['error'], 'wheel_identity')
        self.assertEqual(calls, [])

    def test_fixed_offline_commands_clean_env_shared_timeout(self):
        calls = []
        def runner(argv, **kw):
            calls.append((argv, kw))
            if 'venv' in argv:
                envdir = Path(argv[-1])
                (envdir / 'bin').mkdir(parents=True)
                (envdir / 'bin/python').write_bytes(b'python')
                (envdir / 'pyvenv.cfg').write_text('home = test')
            if len(calls) == 3:
                c.catalog(self.root, self.manifest, reader=self.reader)
        with patch.dict('os.environ', {'SECRET_TOKEN': 'private', 'PYTHONPATH': 'bad'}):
            result = self.run_catalog(runner)
        self.assertEqual(result['phase'], 'complete')
        self.assertIn('--no-python-downloads', calls[0][0])
        self.assertIn('--no-index', calls[1][0])
        self.assertIn('--no-deps', calls[1][0])
        self.assertIn('-B', calls[2][0])
        for argv, kw in calls:
            self.assertEqual(kw['env']['CUDA_VISIBLE_DEVICES'], '')
            self.assertNotIn('SECRET_TOKEN', kw['env'])
            self.assertNotIn('PYTHONPATH', kw['env'])
            self.assertLessEqual(kw['timeout'], 1200)
            self.assertNotIn('shell', kw)
        self.assertLessEqual(calls[-1][1]['timeout'], calls[0][1]['timeout'])

    def reader(self, source, manifest, output):
        output.mkdir()
        (output / 'index.jsonl').write_text('{}\n')
        return {'rows': 1, 'bytes': 3, 'sources': {}}

    def test_completed_reuse_checks_artifacts_and_no_overwrite(self):
        first = c.catalog(self.root, self.manifest, reader=self.reader)
        self.assertEqual(c.catalog(self.root, self.manifest, reader=None), first)
        output = Path(first['output_path'])
        (output / 'index.jsonl').write_text('changed')
        with self.assertRaises(ValueError):
            c.catalog(self.root, self.manifest, reader=self.reader)
        self.assertEqual((output / 'index.jsonl').read_text(), 'changed')

    def test_timeout_sanitized(self):
        def runner(*a, **k):
            raise subprocess.TimeoutExpired('secret', 1, output='secret')
        result = self.run_catalog(runner)
        self.assertEqual(result['error'], 'timeout')
        self.assertNotIn('secret', json.dumps(result))

    def test_unexpected_error_sanitized(self):
        def runner(*a, **k):
            raise RuntimeError('SECRET')
        result = self.run_catalog(runner)
        self.assertEqual(result['error'], 'operation_failed')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_unproven_existing_output_is_conflict(self):
        _, output, _ = c.identity(self.root, self.manifest)
        output.mkdir(parents=True)
        (output / 'owned').write_text('keep')
        result = self.run_catalog(lambda *a, **kw: self.fail('must not install'))
        self.assertEqual(result['error'], 'output_conflict')
        self.assertEqual((output / 'owned').read_text(), 'keep')

    def test_lock_busy_does_not_touch_status(self):
        import fcntl
        _, _, status = c.identity(self.root, self.manifest)
        status.parent.mkdir(parents=True)
        status.write_text('keep')
        with (status.parent / 'catalog.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_catalog(lambda *a, **kw: self.fail('must not install'))
        self.assertEqual(result['error'], 'catalog_busy')
        self.assertEqual(status.read_text(), 'keep')

    def test_unknown_existing_environment_rejected(self):
        envdir = self.root / ('envs/m1-catalog-' + hashlib.sha256(b'wheel').hexdigest()[:12])
        (envdir / 'bin').mkdir(parents=True)
        (envdir / 'bin/python').write_text('unowned')
        result = self.run_catalog(lambda *a, **kw: self.fail('must not mutate unknown env'))
        self.assertEqual(result['error'], 'environment_conflict')

    def test_environment_symlink_rejected(self):
        production = self.root / 'production'
        production.mkdir()
        envdir = self.root / ('envs/m1-catalog-' + hashlib.sha256(b'wheel').hexdigest()[:12])
        envdir.parent.mkdir()
        envdir.symlink_to(production)
        result = self.run_catalog(lambda *a, **kw: self.fail('must not install'))
        self.assertEqual(result['error'], 'symlink')

    def test_save_does_not_follow_fixed_temp_symlink(self):
        target = self.root / 'status.json'
        victim = self.root / 'victim'
        victim.write_text('keep')
        target.with_suffix('.tmp').symlink_to(victim)
        c.save(target, {'safe': True})
        self.assertEqual(victim.read_text(), 'keep')

    def test_save_rejects_symlink_parent(self):
        (self.root / 'link').symlink_to(self.root)
        with self.assertRaises(ValueError):
            c.save(self.root / 'link/status.json', {})
