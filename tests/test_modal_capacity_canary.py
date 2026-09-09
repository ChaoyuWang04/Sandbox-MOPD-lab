import ast
from pathlib import Path
import unittest


class ModalCapacityCanaryTests(unittest.TestCase):
    def test_fixed_single_l40s_canary_exercises_required_sequence(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/m2_modal_capacity_canary.py"
        source = launcher.read_text()
        worker = (launcher.parent / "m2_modal_capacity_worker.py").read_text()
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        function = next(node for node in calls
                        if isinstance(node.func, ast.Attribute) and node.func.attr == "function")
        literal_values = {kw.arg: ast.literal_eval(kw.value) for kw in function.keywords
                          if kw.arg not in {"image", "volumes"}}
        self.assertEqual(literal_values, {
            "gpu": "L40S", "cpu": 8, "memory": 65536, "max_containers": 1,
            "retries": 0, "timeout": 1800, "startup_timeout": 600,
        })
        self.assertIn("eddb418dd4c560db9d43ffde561f1c5e669c8990", source)
        self.assertIn("novaskyai/skyrl-train-ray-2.51.1-py3.12-cu12.8", source)
        self.assertIn("uv sync --frozen --extra fsdp --extra harbor --no-dev", source)
        self.assertIn("/root/SkyRL/.venv/bin/python", source)
        self.assertIn("Qwen/Qwen3-4B", worker)
        self.assertIn("1cfa9a7208912126459214e8b04321603b3df60c", worker)
        self.assertIn("LLM(", worker)
        self.assertIn("llm.sleep(level=1)", worker)
        self.assertIn("loss.backward()", worker)
        self.assertIn("optimizer.step()", worker)
        self.assertIn("llm.wake_up()", worker)
        self.assertGreaterEqual(worker.count("llm.generate("), 2)
        self.assertNotIn("Trial.create", worker)
        self.assertNotIn("Daytona", source + worker)


if __name__ == "__main__":
    unittest.main()
