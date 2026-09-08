import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from lab_runtime import task_sources as sources


class SourcesTests(unittest.TestCase):
    def test_resume_rejects_changed_source_identity(self):
        for key, value in [('revision', 'new'), ('url', 'https://example.test/changed')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                manifest = {'assets': [{'path': 'readme', 'revision': 'fixed', 'url': 'https://example.test/x'}]}
                sources.run_import(manifest, root, opener=lambda *a, **k: io.BytesIO(b'abc'))
                manifest['assets'][0][key] = value
                with self.assertRaises(sources.ImportFailure):
                    sources.run_import(manifest, root, opener=lambda *a, **k: self.fail('network'))

    def test_extract_destination_rejects_escape(self):
        for destination in ['/outside', '../outside', 'bad\\outside']:
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                self.archive(root / 'a', 'repo/task/file')
                payload = (root / 'a').read_bytes()
                manifest = {'assets': [{'path': 'archive', 'revision': 'fixed', 'url': 'https://example.test/x', 'extract': ['*'], 'destination': destination}]}
                with self.assertRaises(sources.ImportFailure):
                    sources.run_import(manifest, root, opener=lambda *a, **k: io.BytesIO(payload))

    def test_cli_disables_bytecode_before_runtime_import(self):
        import ast
        script = Path(sources.__file__).parents[1] / 'scripts/m1_import.py'
        tree = ast.parse(script.read_text())
        runtime_index = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.ImportFrom) and n.module == 'lab_runtime.task_sources')
        assignments = [n for n in tree.body[:runtime_index] if isinstance(n, ast.Assign)]
        self.assertTrue(any(isinstance(n.targets[0], ast.Attribute) and n.targets[0].attr == 'dont_write_bytecode' and isinstance(n.value, ast.Constant) and n.value.value is True for n in assignments))

    def test_download_and_verified_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve() / 'x'
            spec = {'url': 'https://example.test/x', 'size': 3,
                    'sha256': hashlib.sha256(b'abc').hexdigest()}
            result = sources.download(spec, target, 10, opener=lambda *a, **k: io.BytesIO(b'abc'), clock=lambda: 0)
            self.assertEqual(result['size'], 3)
            self.assertTrue(sources.download(spec, target, 10, opener=lambda *a, **k: self.fail('network'), clock=lambda: 0)['reused'])
            target.write_bytes(b'bad')
            with self.assertRaises(sources.ImportFailure):
                sources.download(spec, target, 10, clock=lambda: 0)
            self.assertEqual(target.read_bytes(), b'bad')

    def test_invalid_download_leaves_no_partial(self):
        for data in (b'ab', b'abcd', b'bad'):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp).resolve() / 'x'
                with self.assertRaises(sources.ImportFailure):
                    sources.download({'url': 'https://example.test/x', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}, target, 10, opener=lambda *a, **k: io.BytesIO(data), clock=lambda: 0)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_deadline_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve() / 'x'
            with self.assertRaises(sources.ImportFailure):
                sources.download({'url': 'https://example.test/x'}, target, 0, clock=lambda: 1)
            target.symlink_to(Path(tmp) / 'absent')
            with self.assertRaises(sources.ImportFailure):
                sources.download({'url': 'https://example.test/x'}, target, 10, clock=lambda: 0)

    def archive(self, path, name, kind=None):
        with tarfile.open(path, 'w:gz') as tar:
            info = tarfile.TarInfo(name)
            info.size = 3
            info.mode = 0o755
            if kind:
                info.type = kind
                info.linkname = '/tmp/escape'
            tar.addfile(info, io.BytesIO(b'abc'))

    def test_safe_extract_and_reject_unsafe_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            archive = root / 'a.tar.gz'
            self.archive(archive, 'repo/task/run.sh')
            records = sources.extract(archive, root / 'out', ['*'], 10, clock=lambda: 0)
            self.assertEqual((root / 'out/task/run.sh').read_bytes(), b'abc')
            self.assertTrue((root / 'out/task/run.sh').stat().st_mode & 0o100)
            self.assertEqual(len(records), 1)
            for name, kind in [('repo/../escape', None), ('/abs', None), ('repo/link', tarfile.SYMTYPE), ('repo/hard', tarfile.LNKTYPE), ('repo/dev', tarfile.CHRTYPE)]:
                self.archive(archive, name, kind)
                with self.assertRaises(sources.ImportFailure):
                    sources.extract(archive, root / 'other', ['*'], 10, clock=lambda: 0)

    def test_extract_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.archive(root / 'a', 'repo/task/file')
            with self.assertRaises(sources.ImportFailure):
                sources.extract(root / 'a', root / 'out', ['*'], 10, max_bytes=2, clock=lambda: 0)

    def test_import_persists_identity_across_failed_resume(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = {'assets': [{'path': 'readme', 'revision': 'fixed', 'url': 'https://example.test/x'}]}
            sources.run_import(manifest, root, opener=lambda *a, **k: io.BytesIO(b'abc'))
            status = root / 'artifacts/m1/v2/import-latest.json'
            manifest['assets'].insert(0, {'path': 'missing', 'revision': 'fixed', 'url': 'https://example.test/x'})
            def fail(*a, **k):
                raise OSError('signed-url-secret')
            with self.assertRaises(sources.ImportFailure):
                sources.run_import(manifest, root, opener=fail)
            result = json.loads(status.read_text())
            self.assertIn('readme', result['assets'])
            self.assertNotIn('signed-url-secret', status.read_text())
