import ast
from pathlib import Path
import unittest


class ModalStackPreflightTests(unittest.TestCase):
    def test_preflight_is_cpu_only_bounded_and_uses_official_image(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/m2_modal_stack_preflight.py"
        source = launcher.read_text()
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        function = next(node for node in calls
                        if isinstance(node.func, ast.Attribute) and node.func.attr == "function")
        values = {kw.arg: ast.literal_eval(kw.value) for kw in function.keywords
                  if kw.arg != "image"}
        self.assertEqual(values, {"cpu": 2, "memory": 8192, "max_containers": 1,
                                  "retries": 0, "timeout": 300, "startup_timeout": 300})
        self.assertNotIn("gpu=", source)
        self.assertIn("novaskyai/skyrl-train-ray-2.51.1-py3.12-cu12.8", source)
        self.assertIn("eddb418dd4c560db9d43ffde561f1c5e669c8990", source)
        self.assertIn("uv sync --frozen --extra fsdp --extra harbor --no-dev", source)
        self.assertIn("/root/SkyRL/.venv/bin/python", source)
        self.assertNotIn("uv_pip_install", source)


if __name__ == "__main__":
    unittest.main()
