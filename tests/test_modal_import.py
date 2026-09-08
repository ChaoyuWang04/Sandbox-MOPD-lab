import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab_runtime import modal_import as m


class ModalImportTests(unittest.TestCase):
    def test_trusted_volume_root_requires_exact_mount_identity(self):
        root = Path('/vol')
        expected = Path('/__modal/volumes/vo-example')
        with patch.object(Path, 'is_symlink', side_effect=lambda: False):
            self.assertEqual(m.resolve_volume_root(Path('/regular'), 'vo-example'), Path('/regular'))
        with patch.object(Path, 'is_symlink', return_value=True), \
             patch.object(Path, 'readlink', return_value=expected), \
             patch.object(Path, 'resolve', return_value=expected), \
             patch.object(m, 'safe_path', side_effect=lambda p: p) as checked:
            self.assertEqual(m.resolve_volume_root(root, 'vo-example'), expected)
            checked.assert_called_once_with(expected)
            with self.assertRaises(ValueError):
                m.resolve_volume_root(Path('/other'), 'vo-example')
            with self.assertRaises(ValueError):
                m.resolve_volume_root(root, 'vo-wrong')

    def test_volume_root_rejects_indirect_or_unsafe_target(self):
        expected = Path('/__modal/volumes/vo-example')
        with patch.object(Path, 'is_symlink', return_value=True), \
             patch.object(Path, 'readlink', return_value=expected), \
             patch.object(Path, 'resolve', return_value=Path('/elsewhere')):
            with self.assertRaises(ValueError):
                m.resolve_volume_root(Path('/vol'), 'vo-example')
        with patch.object(Path, 'is_symlink', return_value=True), \
             patch.object(Path, 'readlink', return_value=expected), \
             patch.object(Path, 'resolve', return_value=expected):
            with self.assertRaises(ValueError):
                m.resolve_volume_root(Path('/vol'), 'vo-example')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.wheel = self.root / 'data/m1/v2/sources/tools/pyarrow.whl'
        self.wheel.parent.mkdir(parents=True)
        self.wheel.write_bytes(b'wheel')
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'assets': [{'path': 'tools/pyarrow.whl',
            'size': 5, 'sha256': hashlib.sha256(b'wheel').hexdigest()}]}))
        self.calls = []
        self.commits = []

    def reader(self, source, manifest, output):
        output.mkdir()
        (output / 'index.jsonl').write_text('{}\n')
        return {'rows': 1}

    def run_job(self, **kwargs):
        return m.job(self.root, self.manifest, lambda: self.commits.append(1),
                     runner=lambda *a, **k: self.calls.append((a, k)),
                     importer=lambda *a, **k: {}, reader=self.reader, **kwargs)

    def test_complete_reuse_and_tamper_rejection(self):
        result = self.run_job()
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(len(self.commits), 2)
        self.assertEqual(self.run_job(), result)
        self.assertEqual(len(self.calls), 1)
        (Path(result['output_path']) / 'index.jsonl').write_text('changed')
        self.assertEqual(self.run_job()['error'], 'output_conflict')

    def test_wheel_hash_and_offline_install(self):
        self.run_job()
        argv = self.calls[0][0][0]
        self.assertIn('--no-index', argv)
        self.assertIn('--no-deps', argv)
        self.assertLessEqual(self.calls[0][1]['timeout'], 1150)
        self.wheel.write_bytes(b'wrong')
        # A different manifest identity avoids the completed catalog reuse.
        self.manifest.write_text(self.manifest.read_text() + '\n')
        self.assertEqual(self.run_job()['error'], 'wheel_identity')
        self.assertEqual(len(self.calls), 1)

    def test_failure_is_sanitized_and_committed(self):
        def broken(*a, **k):
            self.assertIsInstance(a[0], dict)
            raise RuntimeError('secret credential')
        result = m.job(self.root, self.manifest, lambda: self.commits.append(1), importer=broken)
        self.assertEqual(result['error'], 'operation_failed')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(len(self.commits), 1)

    def test_unknown_output_is_not_overwritten(self):
        sha = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        output = self.root / f'data/m1/v2/catalog-{sha[:16]}'
        output.mkdir()
        self.assertEqual(self.run_job()['error'], 'output_conflict')
        self.assertEqual(self.calls, [])

    def test_real_importer_decoded_manifest_without_network(self):
        self.manifest.write_text('{"assets": []}')
        result = m.job(self.root, self.manifest, lambda: self.commits.append(1))
        self.assertEqual(result['error'], 'wheel_identity')
        imported = json.loads((self.root / 'artifacts/m1/v2/import-latest.json').read_text())
        self.assertEqual(imported['state'], 'complete')

    def test_launcher_limits_and_mount_allowlist(self):
        launcher = Path(__file__).resolve().parents[1] / 'scripts/m1_import_modal.py'
        tree = ast.parse(launcher.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        function = next(n for n in calls if n.func.attr == 'function')
        values = {kw.arg: ast.literal_eval(kw.value) for kw in function.keywords
                  if kw.arg not in {'image', 'volumes'}}
        self.assertNotIn('ephemeral_disk', values)
        self.assertEqual(values, dict(cpu=(1, 1), memory=(1024, 2048),
                         max_containers=1, retries=0, timeout=1200, startup_timeout=600))
        mounts = [ast.literal_eval(n.args[1]) for n in calls
                  if n.func.attr in {'add_local_dir', 'add_local_file'}]
        self.assertEqual(set(mounts), {'/root/lab_runtime', '/root/configs', '/root/environments/home5090/uv.lock'})
        volume = next(n for n in calls if n.func.attr == 'from_name')
        self.assertEqual({k.arg: ast.literal_eval(k.value) for k in volume.keywords},
                         {'create_if_missing': True, 'version': 2})
