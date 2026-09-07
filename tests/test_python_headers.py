import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from lab_runtime import home5090_run as runtime


class PythonHeadersTest(unittest.TestCase):
    def test_headers_must_match_prepared_manifest(self):
        self.assertTrue(hasattr(runtime, 'header_manifest'))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'Python.h').write_text('first')
            before = runtime.header_manifest(root, runtime.time.monotonic()+10)
            (root/'Python.h').write_text('changed')
            self.assertNotEqual(before, runtime.header_manifest(root, runtime.time.monotonic()+10))

    def test_header_symlinks_rejected(self):
        self.assertTrue(hasattr(runtime, 'header_manifest'))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'Python.h').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                runtime.header_manifest(root, runtime.time.monotonic()+10)

    def test_include_environment_preserves_isolation(self):
        self.assertTrue(hasattr(runtime, 'header_environment'))
        env = runtime.header_environment(Path('/fixed/headers'))
        self.assertEqual(env['CPATH'], '/fixed/headers/usr/include/python3.12:/fixed/headers/usr/include')
        self.assertNotIn('HF_TOKEN', env)
