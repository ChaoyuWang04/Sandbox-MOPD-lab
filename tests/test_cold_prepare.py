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

    def test_outer_time_and_failure_are_published_without_touching_warm_state(self):
        from lab_runtime import cold_prepare as cold
        for fail, elapsed in ((False, 1199), (False, 1201), (True, 10)):
            with self.subTest(fail=fail, elapsed=elapsed), TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                index = base/'artifacts/m0/home5090'
                index.mkdir(parents=True)
                warm = index/'prepare-latest.json'
                warm.write_text('warm witness')

                def fake_prepare():
                    self.assertFalse(runner.VENV.exists())
                    self.assertFalse(runner.MODEL.exists())
                    self.assertFalse((runner.ROOT/'cache').exists())
                    own_index = runner.ROOT/'artifacts/m0/home5090'
                    own_index.mkdir(parents=True)
                    runner.write_json(own_index/'prepare-latest.json',
                                      {'success': not fail, 'cold_start': True})
                    if fail:
                        raise RuntimeError('test failure')

                with patch.object(runner, 'ROOT', base), patch.object(contract, 'ROOT', base), \
                     patch.object(contract, 'check_host'), patch.object(runner, 'prepare', fake_prepare), \
                     patch.object(cold.time, 'monotonic', side_effect=[0, elapsed]):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'test failure'):
                            cold.prepare_cold()
                    else:
                        cold.prepare_cold()
                result = runner.json.loads((index/'cold-prepare-latest.json').read_text())
                self.assertEqual(result['g4_candidate'], not fail and elapsed <= 1200)
                self.assertEqual(warm.read_text(), 'warm witness')
