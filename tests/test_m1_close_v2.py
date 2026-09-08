import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class M1CloseV2Tests(unittest.TestCase):
    def module(self):
        from lab_runtime import m1_close_v2
        return m1_close_v2

    def inputs(self):
        config_path = ROOT / "configs/m1-close-v2.json"
        manifest_path = ROOT / "configs/m1-pool-v2-r2.json"
        return (json.loads(config_path.read_text()), json.loads(manifest_path.read_text()),
                hashlib.sha256(manifest_path.read_bytes()).hexdigest())

    def test_fixed_config_builds_exact_sealed_schedule(self):
        m = self.module()
        config, manifest, digest = self.inputs()
        plans = m.validate_and_plan(config, manifest, digest)
        self.assertEqual({key: len(value) for key, value in plans.items()},
                         {"tb-controls": 2, "train-screen": 80, "dev-baseline": 20})
        self.assertEqual({row["agent"] for row in plans["tb-controls"]}, {"nop", "oracle"})
        self.assertTrue(all(row["split"] == "train" and row["agent"] == "m1"
                            for row in plans["train-screen"]))
        self.assertTrue(all(row["split"] == "dev" and row["agent"] == "m1"
                            for row in plans["dev-baseline"]))
        self.assertFalse(any(row["split"] == "final" and row["agent"] == "m1"
                             for phase in plans.values() for row in phase))
        for phase in plans.values():
            self.assertEqual(len({row["attempt_id"] for row in phase}), len(phase))
            self.assertTrue(all(row["task_files_sha256"] for row in phase))

    def test_config_manifest_sampling_and_budget_drift_fail_closed(self):
        m = self.module()
        config, manifest, digest = self.inputs()
        mutations = []
        for path, value in ((["manifest_sha256"], "0" * 64),
                            (["phases", "train-screen", "group_size"], 3),
                            (["phases", "train-screen", "temperature"], 0.6),
                            (["phases", "dev-baseline", "task_ids", 0], "regex-log"),
                            (["limits", "max_total_creates"], 103),
                            (["limits", "max_consecutive_invalid"], 4),
                            (["limits", "daytona_concurrency"], 2)):
            broken = copy.deepcopy(config)
            node = broken
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            mutations.append(broken)
        for broken in mutations:
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                m.validate_and_plan(broken, manifest, digest)
        bad_manifest = copy.deepcopy(manifest)
        bad_manifest["records"][0]["split"] = "final"
        with self.assertRaises(ValueError):
            m.validate_and_plan(config, bad_manifest, digest)

    def test_plan_seeds_and_groups_are_deterministic(self):
        m = self.module()
        config, manifest, digest = self.inputs()
        left = m.validate_and_plan(config, manifest, digest)
        right = m.validate_and_plan(config, manifest, digest)
        self.assertEqual(left, right)
        screen = left["train-screen"]
        task_order = config["phases"]["train-screen"]["candidate_task_ids"]
        for task_index, task_id in enumerate(task_order):
            group = [row for row in screen if row["task_id"] == task_id]
            self.assertEqual([row["repetition_id"] for row in group], [0, 1, 2, 3])
            self.assertEqual([row["seed"] for row in group],
                             [930001 + task_index * 4 + offset for offset in range(4)])
            self.assertEqual({row["group_id"] for row in group}, {f"m1-close/{task_id}"})

    def test_ledger_admits_each_provider_action_once(self):
        m = self.module()
        config, manifest, digest = self.inputs()
        plan = m.validate_and_plan(config, manifest, digest)["tb-controls"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "campaign.json"
            identity = {"source_git_sha": "a" * 40, "config_sha256": "b" * 64}
            m.create_ledger(path, "tb-controls", identity, plan)
            with self.assertRaisesRegex(ValueError, "already exists"):
                m.create_ledger(path, "tb-controls", identity, plan)
            m.admit_attempt(path, identity, plan[0])
            m.record_attempt_event(path, identity, 0,
                                   {"status": "created", "sandbox_id": "s-1"})
            self.assertEqual(json.loads(path.read_text())["attempts"][0]["events"],
                             [{"status": "created", "sandbox_id": "s-1"}])
            with self.assertRaises(ValueError):
                m.admit_attempt(path, identity, plan[0])
            m.finish_attempt(path, identity, 0, {"state": "finished",
                "classification": "valid_reward", "reward": 0,
                "create_uncertain": False, "cleanup_confirmed_empty": True})
            m.admit_attempt(path, identity, plan[1])
            m.finish_attempt(path, identity, 1, {"state": "finished",
                "classification": "valid_reward", "reward": 1,
                "create_uncertain": False, "cleanup_confirmed_empty": True})
            m.finish_ledger(path, identity, "c" * 64)
            ledger = json.loads(path.read_text())
            self.assertEqual(ledger["provider_create_authorizations"], 2)
            self.assertEqual(len(ledger["attempts"]), 2)
            self.assertEqual(ledger["state"], "finished")
            with self.assertRaises(ValueError):
                m.admit_attempt(path, identity, plan[1])

    def test_task_tree_verification_is_exact_and_rejects_links(self):
        from lab_runtime.m1_close_run import verify_task_tree
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            task = root / "task-a"
            task.mkdir()
            (task / "instruction.md").write_text("hello\n")
            expected = hashlib.sha256((task / "instruction.md").read_bytes()).hexdigest()
            item = {"task_path": "ignored/task-a",
                    "task_files_sha256": {"instruction.md": expected}}
            self.assertEqual(verify_task_tree(item, root), task)
            (task / "instruction.md").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "identity"):
                verify_task_tree(item, root)
            (task / "instruction.md").unlink()
            (task / "instruction.md").symlink_to("missing")
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify_task_tree(item, root)

    def test_trial_config_uses_fixed_model_and_resource_profile(self):
        from lab_runtime.m1_close_run import make_trial_config
        item = {"agent": "m1", "temperature": 1.0, "seed": 1,
                "resource_profile": "self", "attempt_id": "screen/task/00"}
        profile = {"cpus": 1, "memory_mb": 1024, "storage_mb": 3072,
                   "network": False, "ttl_seconds": 300, "trial_seconds": 360}
        config = make_trial_config(Path("/tmp/task"), Path("/tmp/run"), item, profile,
                                   {"owner": "fixture"})
        self.assertEqual(config.agent.model_name, "Qwen/Qwen3-4B")
        self.assertEqual(config.environment.import_path,
                         "lab_runtime.daytona_pilot:PilotDaytonaEnvironment")
        self.assertEqual(config.environment.override_cpus, 1)
        self.assertTrue(config.environment.kwargs["network_block_all"])

    def test_model_failure_is_reward_zero_not_invalid(self):
        from lab_runtime.m1_close_run import _classification, _classify_and_normalize
        record = {"agent": "m1", "reward": 0, "exception_type": None,
                  "create_uncertain": False, "cleanup_confirmed_empty": True,
                  "private_files_absent_during_agent": True, "verifier_complete": True,
                  "usage_complete": True, "model_responses": 1}
        self.assertEqual(_classification(record), ("valid_reward", None))
        record["private_files_absent_during_agent"] = False
        self.assertEqual(_classification(record), ("invalid", "verifier_protocol_incomplete"))
        _classify_and_normalize(record)
        self.assertEqual(record["classification"], "invalid")
        self.assertIsNone(record["reward"])
        self.assertEqual(record["observed_reward"], 0)

    def test_cost_uses_the_selected_resource_profile(self):
        from lab_runtime.m1_close_run import _estimate_cost
        record = {"events": [{"status": "creating", "time": 100.0},
                              {"status": "created", "sandbox_id": "s-1"}],
                  "finished": 3700.0, "create_uncertain": False,
                  "cleanup_confirmed_empty": True}
        profile = {"cpus": 4, "memory_mb": 8192, "storage_mb": 10240,
                   "ttl_seconds": 3600}
        cost = _estimate_cost(record, profile)
        self.assertAlmostEqual(cost["sandbox_seconds"], 3600)
        self.assertAlmostEqual(cost["estimated_usd"], 0.33228, places=6)
        self.assertAlmostEqual(cost["ttl_max_estimate_usd"], 0.33228, places=6)

    def test_verifier_artifacts_are_closed_and_own_processes_stopped(self):
        from lab_runtime.m1_close_run import _verifier_complete
        record = {"reward": 1, "exception_type": None}
        swe = {"protocol_complete": True, "reward": 1, "runtime_errors": [],
               "owned_process_group_stopped": True}
        self.assertTrue(_verifier_complete("swe-gym", record, swe))
        swe["owned_process_group_stopped"] = False
        self.assertFalse(_verifier_complete("swe-gym", record, swe))
        self.assertTrue(_verifier_complete("self", record, {"passed": True}))
        self.assertFalse(_verifier_complete("self", record, {"passed": 1}))
        self.assertFalse(_verifier_complete("self", record,
                                            {"passed": True, "extra": "ignored"}))

    def test_self_model_never_gets_solution_visibility(self):
        import asyncio
        from types import SimpleNamespace
        from harbor.trial.hooks import TrialEvent
        from lab_runtime.controls_v2 import attach_absence
        hooks = {}
        commands = []
        class Environment:
            async def exec(self, command, **kwargs):
                commands.append(command)
                return SimpleNamespace(return_code=0)
        trial = SimpleNamespace(agent_environment=Environment(),
            add_hook=lambda event, hook: hooks.update({event: hook}))
        attach_absence(trial, "m1")
        async def exercise():
            await hooks[TrialEvent.AGENT_START](None)
            await hooks[TrialEvent.AGENT_END](None)
        asyncio.run(exercise())
        self.assertTrue(all("test ! -e /solution" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
