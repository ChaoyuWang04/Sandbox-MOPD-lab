"""Pure grading contracts: no sandbox or execution certification."""
import unittest
import json
from pathlib import Path

from lab_runtime.swe_grading import grade_swe_tests


SMITH = "swesmith.profiles.python.PythonProfile.log_parser"
GYM = "swebench.harness.log_parsers.parse_log_pandas"


class SWEGradingTests(unittest.TestCase):
    def grade(self, observations, **kwargs):
        args = dict(source="smith", parser_identity=SMITH,
                    fail_to_pass=["fix"], pass_to_pass=["keep"],
                    observations=observations, protocol_complete=True,
                    collected_node_ids=list(observations) if isinstance(observations, dict)
                    else [node for node, _ in observations])
        args.update(kwargs)
        return grade_swe_tests(**args)

    def test_missing_inventory_invalid_even_with_complete_protocol(self):
        report = self.grade({"fix": "PASSED", "keep": "PASSED"}, collected_node_ids=None)
        self.assertEqual(report["outcome"], "invalid")
        self.assertIsNone(report["reward"])
        self.assertTrue(report["missing_inventory"])

    def test_observation_outside_inventory_invalid(self):
        report = self.grade({"fix": "PASSED", "keep": "PASSED"}, collected_node_ids=["keep"])
        self.assertEqual(report["outcome"], "invalid")
        self.assertEqual(report["uncollected_nodes"], ["fix"])

    def test_unreported_nonrequired_collected_node_invalidates_protocol(self):
        report = self.grade({"fix": "PASSED", "keep": "PASSED"},
                            collected_node_ids=["fix", "keep", "extra"])
        self.assertEqual(report["outcome"], "invalid")
        self.assertEqual(report["unreported_nodes"], ["extra"])

    def test_nonrequired_skipped_terminal_observation_does_not_fail_grade(self):
        report = self.grade({"fix": "PASSED", "keep": "PASSED", "extra": "SKIPPED"})
        self.assertEqual(report["outcome"], "passed")

    def test_missing_ptp_is_invalid_not_model_failure(self):
        report = self.grade({"fix": "PASSED"})
        self.assertEqual(report["outcome"], "invalid")
        self.assertIsNone(report["reward"])
        self.assertEqual(report["missing"], {"FAIL_TO_PASS": [], "PASS_TO_PASS": ["keep"]})

    def test_smith_official_xfail_passes_both_groups(self):
        self.assertEqual(self.grade({"fix": "XFAIL", "keep": "PASSED"})["reward"], 1)

    def test_gym_pinned_policy_and_status_enum_are_not_generic_pytest(self):
        for status, outcome in [("XFAIL", "passed"), ("SKIPPED", "failed"), ("XPASS", "failed")]:
            with self.subTest(status=status):
                self.assertEqual(self.grade({"fix": status, "keep": "PASSED"},
                                            source="gym", parser_identity=GYM)["outcome"], outcome)

    def test_unrequired_xpass_is_known_terminal_for_both_sources(self):
        for source, parser in [("smith", SMITH), ("gym", GYM)]:
            report = self.grade({"fix": "PASSED", "keep": "PASSED", "extra": "XPASS"},
                                source=source, parser_identity=parser)
            self.assertEqual(report["reward"], 1)
            self.assertEqual(report["passing_statuses"], ["PASSED", "XFAIL"])
            self.assertEqual(report["unknown_statuses"], {})

    def test_cloud_nop_record_replay_with_unrequired_xpass(self):
        directory = Path(__file__).resolve().parents[1] / "artifacts/m1/v2/controls/m1-v2-first-six-r2/attempt-02/trials/control/verifier"
        run_path = directory / "run.json"
        if not run_path.exists():
            self.skipTest("ignored cloud replay artifact unavailable")
        old = json.loads(run_path.read_text())
        artifact = directory / Path(old["artifact"]).name
        if not artifact.exists():
            self.skipTest("ignored pytest observation artifact unavailable")
        execution = json.loads(artifact.read_text())
        self.assertEqual(old["runtime_errors"], [])
        self.assertTrue(old["protocol_complete"])
        self.assertIn(["tests/test_regressions.py::test_issue484_comments_and_newlines", "XPASS"], execution["observations"])
        report = grade_swe_tests(source=old["source"], parser_identity=old["parser_identity"],
            fail_to_pass=old["required"]["FAIL_TO_PASS"], pass_to_pass=old["required"]["PASS_TO_PASS"],
            observations=execution["observations"], collected_node_ids=execution["collected_node_ids"],
            protocol_complete=old["protocol_complete"])
        self.assertEqual((report["outcome"], report["reward"]), ("failed", 0))
        self.assertEqual(report["unknown_statuses"], {})
        self.assertEqual(report["nonpassing"], old["nonpassing"])

    def test_known_failure_with_complete_protocol_is_zero(self):
        report = self.grade({"fix": "FAILED", "keep": "PASSED"})
        self.assertEqual((report["outcome"], report["reward"]), ("failed", 0))
        self.assertEqual(report["nonpassing"], {"fix": {"fix": ["FAILED"]}})

    def test_protocol_incomplete_or_unknown_status_is_invalid(self):
        for status, complete in [("PASSED", False), ("TIMEOUT", True), ("passed", True), (None, True)]:
            with self.subTest(status=status, complete=complete):
                self.assertEqual(self.grade({"fix": status, "keep": "PASSED"}, protocol_complete=complete)["outcome"], "invalid")

    def test_full_param_id_projects_to_native_key_without_prefix_matching(self):
        report = self.grade({"test.py::test[a b]": "PASSED"},
                            fail_to_pass=["test.py::test[a"], pass_to_pass=[])
        self.assertEqual(report["reward"], 1)
        self.assertEqual(report["identity_map"], {"test.py::test[a": ["test.py::test[a b]"]})
        self.assertEqual(self.grade({"fix_extra": "PASSED", "keep": "PASSED"})["outcome"], "invalid")

    def test_every_colliding_full_node_must_pass(self):
        report = self.grade([("test[a b]", "PASSED"), ("test[a c]", "FAILED")],
                            fail_to_pass=["test[a"], pass_to_pass=[])
        self.assertEqual(report["reward"], 0)
        self.assertEqual(report["collisions"], {"test[a": ["test[a b]", "test[a c]"]})

    def test_same_node_contradiction_invalid_not_last_write_wins(self):
        report = self.grade([("fix", "PASSED"), ("fix", "FAILED"), ("keep", "PASSED")])
        self.assertEqual(report["outcome"], "invalid")
        self.assertEqual(report["conflicts"], {"fix": ["FAILED", "PASSED"]})

    def test_collected_but_unreported_colliding_node_invalid(self):
        report = self.grade({"test[a b]": "PASSED"}, fail_to_pass=["test[a"],
                            pass_to_pass=[], collected_node_ids=["test[a b]", "test[a c]"])
        self.assertEqual(report["outcome"], "invalid")
        self.assertEqual(report["unreported_nodes"], ["test[a c]"])

    def test_unknown_parser_or_empty_ftp_rejected(self):
        for kwargs in [dict(parser_identity="guessed.parser"), dict(fail_to_pass=[])]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.grade({"fix": "PASSED", "keep": "PASSED"}, **kwargs)

    def test_report_deterministic_under_input_reordering(self):
        left = [("keep", "PASSED"), ("fix", "FAILED"), ("extra", "PASSED")]
        self.assertEqual(self.grade(left), self.grade(list(reversed(left))))


if __name__ == "__main__":
    unittest.main()
