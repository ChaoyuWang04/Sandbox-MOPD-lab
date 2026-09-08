"""Real pytest subprocess protocol checks on tiny temporary repositories."""
import json
import os
from pathlib import Path
import shlex
import sys
import subprocess
import signal
import tempfile
import unittest

from lab_runtime.swe_grading import SMITH_PARSER


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = os.environ.get("PYTEST_TEST_PYTHON", os.environ.get("SWE_TEST_PYTHON", sys.executable))
        check = subprocess.run([cls.python, "-c", "import pytest"], capture_output=True)
        if check.returncode:
            raise unittest.SkipTest("real pytest unavailable; set PYTEST_TEST_PYTHON to isolated test Python")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.output = self.root / "output"
        self.config = dict(source="smith", parser_identity=SMITH_PARSER,
                           fail_to_pass=["test_small.py::test_bug"], pass_to_pass=[])
        self.command = shlex.quote(self.python) + " -m pytest -q -p no:cacheprovider"

    def run_fixture(self, source, extra="", timeout=10):
        from lab_runtime.swe_test_runner import run_tests
        (self.repo / "test_small.py").write_text(source)
        return run_tests(self.command + extra, self.config, self.output,
                         cwd=self.repo, timeout=timeout)

    def assert_invalid(self, result):
        self.assertEqual(result["outcome"], "invalid", result)
        self.assertIsNone(result["reward"])
        self.assertFalse((self.output / "reward.txt").exists())
        self.assertFalse((self.output / "grade.json").exists())

    def test_nop_fails_gold_passes(self):
        result = self.run_fixture("def test_bug(): assert False\n")
        self.assertEqual(result["reward"], 0, result)
        self.assertEqual(result["returncode"], 1)
        result = self.run_fixture("def test_bug(): assert True\n")
        self.assertEqual(result["reward"], 1, result)
        self.assertEqual((self.output / "reward.txt").read_text(), "1\n")
        self.assertEqual(json.loads((self.output / "grade.json").read_text())["reward"], 1)

    def test_evidence_records_source_interpreter(self):
        result = self.run_fixture("def test_bug(): pass\n")
        artifact = json.loads(Path(result["artifact"]).read_text())
        self.assertEqual(Path(artifact["python_executable"]).absolute(), Path(self.python).absolute())
        self.assertEqual(Path(artifact["cwd"]).resolve(), self.repo.resolve())
        self.assertTrue(artifact["python_version"])
        self.assertTrue(artifact["pytest_version"])

    def test_teardown_failure_overrides_pass(self):
        result = self.run_fixture("import pytest\n@pytest.fixture\ndef resource():\n yield\n assert False\ndef test_bug(resource): pass\n")
        self.assertEqual(result["reward"], 0, result)
        self.assertEqual(result["nonpassing"]["test_small.py::test_bug"]["test_small.py::test_bug"], ["ERROR"])

    def test_xfail_passes_and_skip_fails(self):
        for marker, body, reward in [("xfail", "assert False", 1), ("skip", "pass", 0)]:
            with self.subTest(marker=marker):
                result = self.run_fixture("import pytest\n@pytest.mark." + marker + "\ndef test_bug(): " + body + "\n")
                self.assertEqual(result["reward"], reward, result)

    def test_xpass_is_invalid(self):
        for marker in ["xfail", "xfail(strict=True)"]:
            with self.subTest(marker=marker):
                self.assert_invalid(self.run_fixture("import pytest\n@pytest.mark." + marker + "\ndef test_bug(): pass\n"))

    def test_missing_ptp_and_deselected_required_invalid(self):
        self.config["pass_to_pass"] = ["test_small.py::test_keep"]
        self.assert_invalid(self.run_fixture("def test_bug(): pass\n"))
        self.assert_invalid(self.run_fixture("def test_bug(): pass\ndef test_keep(): pass\n", " -k bug"))

    def test_deselected_nonrequired_not_unreported(self):
        result = self.run_fixture("def test_bug(): pass\ndef test_unused(): assert False\n", " -k bug")
        self.assertEqual(result["reward"], 1, result)

    def test_collection_error_invalid(self):
        self.assert_invalid(self.run_fixture("raise RuntimeError('collection')\n"))

    def test_early_exit_invalid_and_removes_stale_reward(self):
        self.assertEqual(self.run_fixture("def test_bug(): pass\n")["reward"], 1)
        self.assert_invalid(self.run_fixture("import os\ndef test_bug(): os._exit(0)\n"))

    def test_system_exit_in_test_is_completed_failure(self):
        result = self.run_fixture("def test_bug(): raise SystemExit(0)\n")
        self.assertEqual(result["reward"], 0, result)

    def test_timeout_invalid(self):
        self.assert_invalid(self.run_fixture("import time\ndef test_bug(): time.sleep(30)\n", timeout=0.5))

    def test_success_reaps_background_child(self):
        result = self.run_fixture(
            "import subprocess,sys\nfrom pathlib import Path\n"
            "def test_bug():\n"
            " child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            " Path('child.pid').write_text(str(child.pid))\n")
        pid = int((self.repo / "child.pid").read_text())
        try:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=2).stdout.strip()
            self.assertTrue(not status or status.startswith("Z"), status)
            self.assertEqual(result["reward"], 1, result)
            self.assertTrue(result["owned_process_group_stopped"])
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_failfast_missing_node_invalid(self):
        self.assert_invalid(self.run_fixture("def test_bug(): assert False\ndef test_later(): pass\n", " -x"))

    def test_collision_requires_all_nodes_pass(self):
        self.config["fail_to_pass"] = ["test_small.py::test_bug[same"]
        source = "import pytest\n@pytest.mark.parametrize('value',[True,VALUE],ids=['same first','same second'])\ndef test_bug(value): assert value\n"
        for value, reward in [("False", 0), ("True", 1)]:
            result = self.run_fixture(source.replace("VALUE", value))
            self.assertEqual(result["reward"], reward, result)
            self.assertEqual(len(result["collisions"]["test_small.py::test_bug[same"]), 2)

    def test_successful_nonpytest_command_cannot_reuse_artifact(self):
        from lab_runtime.swe_test_runner import run_tests
        self.assertEqual(self.run_fixture("def test_bug(): pass\n")["reward"], 1)
        result = run_tests("true", self.config, self.output, cwd=self.repo, timeout=2)
        self.assert_invalid(result)

    def test_missing_reports_invalid(self):
        (self.repo / "conftest.py").write_text("def pytest_runtest_protocol(item, nextitem): return True\n")
        self.assert_invalid(self.run_fixture("def test_bug(): pass\n"))

    def test_nonce_mismatch_invalid(self):
        from lab_runtime.swe_test_runner import run_tests
        payload = dict(schema_version=1, nonce="stale", session_started=True,
                       session_finished=True, exitstatus=0, errors=[],
                       collected_node_ids=["test_small.py::test_bug"],
                       observations=[["test_small.py::test_bug", "PASSED"]])
        code = "import os;open(os.environ['MOPD_SWE_ARTIFACT'],'w').write(" + repr(json.dumps(payload)) + ")"
        command = shlex.quote(self.python) + " -c " + shlex.quote(code)
        self.assert_invalid(run_tests(command, self.config, self.output, cwd=self.repo, timeout=2))

    def test_multiple_pytest_sessions_invalid(self):
        # A later successful session must not erase an earlier failed session.
        from lab_runtime.swe_test_runner import run_tests
        (self.repo / "test_small.py").write_text("def test_bug(): pass\n")
        command = self.command + "; " + self.command
        self.assert_invalid(run_tests(command, self.config, self.output, cwd=self.repo, timeout=5))


if __name__ == "__main__":
    unittest.main()
