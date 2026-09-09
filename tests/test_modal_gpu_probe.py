import ast
from pathlib import Path
import unittest


class ModalGpuProbeTests(unittest.TestCase):
    def test_launcher_is_single_l40s_no_retry_with_durable_volume(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/m2_modal_gpu_probe.py"
        tree = ast.parse(launcher.read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        function = next(node for node in calls
                        if isinstance(node.func, ast.Attribute) and node.func.attr == "function")
        values = {kw.arg: ast.literal_eval(kw.value) for kw in function.keywords
                  if kw.arg not in {"image", "volumes"}}
        self.assertEqual(values, {"gpu": "L40S", "cpu": 2, "memory": 8192,
                                  "max_containers": 1, "retries": 0,
                                  "timeout": 300, "startup_timeout": 300})
        volume = next(node for node in calls
                      if isinstance(node.func, ast.Attribute) and node.func.attr == "from_name")
        self.assertEqual(ast.literal_eval(volume.args[0]), "sandbox-mopd-lab-m2")
        self.assertEqual({kw.arg: ast.literal_eval(kw.value) for kw in volume.keywords},
                         {"create_if_missing": True, "version": 2})


if __name__ == "__main__":
    unittest.main()
