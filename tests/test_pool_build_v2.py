import hashlib
import unittest
import tempfile
from pathlib import Path
from lab_runtime.pool_build_v2 import pin_tb_image, read_tree, publish_json, build_pool


class PoolBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_pin_changes_only_image(self):
        raw = b'# canary\n[environment]\ndocker_image = "owner/image:tag" # keep\n[agent]\ntimeout_sec = 7200\n'
        digest = 'sha256:' + 'a' * 64
        assert pin_tb_image(raw, digest) == raw.replace(b'owner/image:tag', ('owner/image:tag@' + digest).encode())

    def test_pin_rejects_missing_or_unsafe(self):
        with self.assertRaises(ValueError):
            pin_tb_image(b'[environment]\n', 'sha256:' + 'a' * 64)
        with self.assertRaises(ValueError):
            pin_tb_image(b'[environment]\ndocker_image="x"', 'bad')

    def test_tree_checks_hash_and_symlink(self):
        (self.root / 'a').write_bytes(b'x')
        with self.assertRaises(ValueError):
            read_tree(self.root, {'a': '0' * 64})
        assert read_tree(self.root, {'a': hashlib.sha256(b'x').hexdigest()}) == {'a': b'x'}
        (self.root / 'b').symlink_to(self.root / 'a')
        with self.assertRaises(ValueError):
            read_tree(self.root)

    def test_missing_tree_and_input(self):
        with self.assertRaises(ValueError):
            read_tree(self.root / 'absent')
        with self.assertRaises(FileNotFoundError):
            build_pool(self.root)

    def test_manifest_exclusive_publication(self):
        path = self.root / 'result.json'
        publish_json(path, {'value': 1})
        publish_json(path, {'value': 1})
        with self.assertRaises(ValueError):
            publish_json(path, {'value': 2})

    def test_manifest_refuses_symlink_file_and_parent(self):
        actual = self.root / 'actual.json'
        publish_json(actual, {'value': 1})
        link = self.root / 'linked.json'
        link.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'symlink destination'):
            publish_json(link, {'value': 1})
        outside = self.root / 'outside'
        outside.mkdir()
        parent = self.root / 'provenance'
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink destination'):
            publish_json(parent / 'new.json', {'value': 1})
        assert not (outside / 'new.json').exists()

    def test_registered_input_mismatch_precedes_outputs(self):
        (self.root / 'input').write_bytes(b'wrong')
        publish_json(self.root / 'configs/m1-build-inputs-v2.json', {
            'input_sha256': {'input': '0' * 64}})
        with self.assertRaisesRegex(ValueError, 'registered input hash mismatch'):
            build_pool(self.root)
        assert not (self.root / 'data').exists()

    def test_real_input_pool_if_present(self):
        from pathlib import Path
        import json
        root = Path(__file__).resolve().parents[1]
        manifest = root / 'configs/m1-pool-v2.json'
        if not manifest.exists() or not (root / 'data/m1/v2/tasks').exists():
            self.skipTest('ignored frozen source artifacts absent in clean checkout')
        obj = json.loads(manifest.read_text())
        from lab_runtime.pool_manifest_v2 import validate_manifest
        assert validate_manifest(obj['records'], json.loads((root / 'configs/m1-pool-manifest.json').read_text()))['total'] == 200
        for r in obj['records']:
            read_tree(root / r['task_path'], r['task_files_sha256'])
        assert sum(len(r['task_files_sha256']) for r in obj['records'] if r['frozen_legacy']) == 440
