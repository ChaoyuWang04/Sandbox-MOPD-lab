import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from lab_runtime import home5090 as contract, home5090_run as runner


class ColdPrepareTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.cold_prepare'))

    def test_root_switch_scopes_all_assets_and_restores_on_error(self):
        from lab_runtime import cold_prepare as cold
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base/'cold-rebuilds'/('a'*32)
            with patch.object(runner, 'ROOT', base), patch.object(contract, 'ROOT', base):
                old_venv = runner.VENV
                with self.assertRaisesRegex(RuntimeError, 'test'):
                    with cold.private_roots(root):
                        self.assertEqual(runner.ROOT, root)
                        self.assertEqual(contract.ROOT, root)
                        self.assertTrue(runner.VENV.is_relative_to(root))
                        self.assertTrue(runner.MODEL.is_relative_to(root))
                        self.assertTrue(contract.isolated_env({})['HF_HOME'].startswith(str(root)))
                        raise RuntimeError('test')
                self.assertEqual(runner.ROOT, base)
                self.assertEqual(runner.VENV, old_venv)

    def test_root_switch_rejects_outside_or_nonunique_root(self):
        from lab_runtime import cold_prepare as cold
        for root in (Path('/tmp/escape'), runner.ROOT, runner.ROOT/'cold-rebuilds/reused'):
            with self.assertRaises(ValueError):
                with cold.private_roots(root):
                    pass
