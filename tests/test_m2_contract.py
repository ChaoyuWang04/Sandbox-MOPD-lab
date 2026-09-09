import copy
import unittest

from lab_runtime import m2_contract


class M2ContractTests(unittest.TestCase):
    def manifest(self):
        records = [
            {"id": "a-train", "split": "train", "primary_skill": "A", "task_path": "tasks/a"},
            {"id": "b-train", "split": "train", "primary_skill": "B", "task_path": "tasks/b"},
            {"id": "a-dev", "split": "dev", "primary_skill": "A", "task_path": "tasks/c"},
            {"id": "a-final", "split": "final", "primary_skill": "A", "task_path": "tasks/d"},
        ]
        records.extend({"id": f"extra-{index:02d}", "split": "train",
                        "primary_skill": "A" if index % 2 == 0 else "B",
                        "task_path": f"tasks/extra-{index:02d}"} for index in range(78))
        return {"records": records}

    def stack_lock(self):
        return {
            "schema_version": 1,
            "sources": {
                "skyrl": {"repository": "https://github.com/NovaSky-AI/SkyRL.git",
                           "version": "0.2.0", "commit": "a" * 40,
                           "pyproject_sha256": "b" * 64, "uv_lock_sha256": "c" * 64},
                "harbor": {"repository": "https://github.com/laude-institute/harbor.git",
                           "version": "0.4.0", "commit": "d" * 40},
            },
            "model": {"id": "Qwen/Qwen3-4B", "revision": "e" * 40,
                      "model_manifest_sha256": "f" * 64},
            "runtime": {"python": "3.12.3", "cuda": "12.8", "driver": "595.71.05",
                        "torch": "2.10.0+cu128", "vllm": "0.19.0", "ray": "2.51.1",
                        "transformers": "5.3.0", "peft": "0.18.1",
                        "flash_attn": "2.8.3", "flashinfer_python": "0.6.6",
                        "daytona": "0.161.0"},
            "target": {"training_platform_order": ["daytona", "modal"],
                       "home5090_training": False, "gpu_count": 1,
                       "persistence_required": True},
            "entrypoint": "examples.train_integrations.harbor.entrypoints.main_harbor",
            "execution_ready": False,
            "unverified": ["strict_stepwise_bridge_not_implemented"],
        }

    def run_config(self, task_ids=None, mode="m2-bringup"):
        task_ids = task_ids or ["a-train", "b-train"]
        config = {
            "schema_version": 1, "mode": mode,
            "pool_manifest": "configs/m1-pool-v2-r2.json", "pool_manifest_sha256": "d" * 64,
            "stack_lock": "configs/m2-stack-lock.json", "stack_lock_sha256": "e" * 64,
            "start_checkpoint_sha256": "f" * 64,
            "task_ids": task_ids,
            "sampling": {"group_size": 4, "temperature": 1.0, "top_p": 1.0, "seed": 920001},
            "trajectory": {"api": "chat/completions", "step_wise_trajectories": True,
                           "merge_stepwise_output": False, "return_token_ids": True,
                           "return_token_logprobs": True, "template_sha256": "1" * 64},
            "training_backend": {"type": "daytona", "gpu_count": 1,
                                 "gpu_types": ["RTX-5090", "RTX-4090"], "spot": False,
                                 "persistence": "volume"},
            "placement": {"gpu_isolation": "dedicated_cloud_sandbox", "colocate_all": True,
                          "run_engines_locally": True, "vllm_sleep_wake": True,
                          "policy": "lora_fsdp", "reference": "disabled",
                          "critic": "disabled", "reward_model": "disabled"},
            "model": {"max_model_len": 16384, "max_generated_tokens_per_trajectory": 4096,
                      "max_turns": 10, "enable_thinking": False},
            "trainer": {"strategy": "fsdp2", "lora_rank": 32, "lora_alpha": 64,
                        "target_modules": "all-linear", "learning_rate": 0.00001,
                        "weight_decay": 0.0, "max_grad_norm": 1.0,
                        "advantage_estimator": "grpo", "grpo_norm_by_std": False,
                        "use_kl_loss": False, "cpu_offload": True,
                        "micro_train_batch_size_per_gpu": 1,
                        "micro_forward_batch_size_per_gpu": 1},
            "provider": {"type": "daytona",
                         "credential_source": "injected_secret_env",
                         "credential_env": "DAYTONA_API_KEY", "cpus": 1, "memory_mb": 1024,
                         "storage_mb": 3072, "network": False, "ttl_seconds": 300},
            "limits": {"max_updates": 1, "max_generated_tokens": 32768,
                       "max_training_tokens": 32768, "max_wall_seconds": 5400,
                       "max_created_sandboxes": 8, "daytona_concurrency": 4},
            "required_evidence": {"mixed_valid_group_count_min": 1,
                                  "valid_samples_per_mixed_group_min": 2,
                                  "effective_loss_tokens_min": 1,
                                  "nonzero_advantage_required": True,
                                  "nonzero_pre_step_policy_grad_norm_required": True,
                                  "inference_content_receipt_required": True,
                                  "next_rollout_weight_binding_required": True,
                                  "fresh_process_reload_required": True},
        }
        if mode != "m2-bringup":
            attempts = len(task_ids) * config["sampling"]["group_size"]
            config["limits"].update(max_created_sandboxes=attempts,
                max_generated_tokens=attempts * 4096,
                max_training_tokens=attempts * 4096)
        return config

    def validate(self, config=None, manifest=None, stack_lock=None, **kwargs):
        config = config or self.run_config()
        return m2_contract.validate_run_config(
            config, manifest or self.manifest(), stack_lock or self.stack_lock(),
            manifest_sha256=config["pool_manifest_sha256"],
            stack_lock_sha256=config["stack_lock_sha256"], **kwargs)

    def test_stack_lock_requires_complete_identities_and_readiness_consistency(self):
        lock = self.stack_lock()
        self.assertEqual(m2_contract.validate_stack_lock(lock), lock)
        paths = (("sources", "skyrl", "commit"), ("sources", "skyrl", "uv_lock_sha256"),
                 ("sources", "harbor", "commit"), ("model", "revision"),
                 ("model", "model_manifest_sha256"), ("runtime", "torch"),
                 ("runtime", "peft"), ("runtime", "daytona"),
                 ("target", "training_platform_order"), ("target", "home5090_training"),
                 (None, "entrypoint"), (None, "execution_ready"),
                 (None, "unverified"))
        for path in paths:
            broken = copy.deepcopy(lock)
            node = broken
            keys = path[1:] if path[0] is None else path
            for key in keys[:-1]:
                node = node[key]
            node.pop(keys[-1])
            with self.subTest(path=path), self.assertRaises(ValueError):
                m2_contract.validate_stack_lock(broken)
        inconsistent = copy.deepcopy(lock)
        inconsistent["execution_ready"] = True
        with self.assertRaisesRegex(ValueError, "unverified"):
            m2_contract.validate_stack_lock(inconsistent)

    def test_training_config_is_closed_and_content_bound(self):
        config = self.run_config()
        self.assertEqual(self.validate(config), config)
        for section, key in ((None, "trainer"), (None, "required_evidence"),
                             ("trainer", "weight_decay"), ("provider", "network")):
            broken = copy.deepcopy(config)
            if section is None:
                broken.pop(key)
            elif key == "network":
                broken[section][key] = True
            else:
                broken[section].pop(key)
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                self.validate(broken)
        for section in ("sampling", "trajectory"):
            broken = copy.deepcopy(config)
            broken[section]["silent_noop"] = True
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.validate(broken)
        with self.assertRaisesRegex(ValueError, "manifest.*identity"):
            m2_contract.validate_run_config(config, self.manifest(), self.stack_lock(),
                manifest_sha256="0" * 64, stack_lock_sha256=config["stack_lock_sha256"])
        with self.assertRaisesRegex(ValueError, "stack lock.*identity"):
            m2_contract.validate_run_config(config, self.manifest(), self.stack_lock(),
                manifest_sha256=config["pool_manifest_sha256"], stack_lock_sha256="0" * 64)

    def test_training_config_accepts_only_registered_train_tasks_and_splits(self):
        self.validate()
        for bad in (["a-dev"], ["a-final"], ["missing"], ["a-train", "a-train"]):
            with self.subTest(task_ids=bad), self.assertRaises(ValueError):
                self.validate(self.run_config(bad))
        manifest = self.manifest()
        manifest["records"][2]["split"] = "develop"
        with self.assertRaisesRegex(ValueError, "split"):
            self.validate(manifest=manifest)

    def test_numeric_reward_is_strictly_binary_and_invalid_is_not_zero(self):
        for value in (0, 1, 0.0, 1.0):
            self.assertEqual(m2_contract.classify_verifier({"reward": value, "invalid_reason": None}),
                             {"classification": "valid_reward", "reward": int(value)})
        for value in (-1, 0.5, 2, True, "0", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m2_contract.classify_verifier({"reward": value, "invalid_reason": None})
        self.assertEqual(m2_contract.classify_verifier(
            {"reward": None, "invalid_reason": "verifier_timeout"}),
            {"classification": "invalid", "reward": None, "invalid_reason": "verifier_timeout"})
        with self.assertRaises(ValueError):
            m2_contract.classify_verifier({"reward": 0, "invalid_reason": "timeout"})
        with self.assertRaisesRegex(ValueError, "registered"):
            m2_contract.classify_verifier({"reward": None, "invalid_reason": "model_answer_wrong"})

    def test_limits_are_positive_and_hard_bounded(self):
        config = self.run_config()
        self.validate(config)
        for key, value in (("max_updates", 0), ("max_updates", 2),
                           ("max_generated_tokens", 32769),
                           ("max_training_tokens", 32769),
                           ("max_wall_seconds", 86401), ("daytona_concurrency", 9),
                           ("max_created_sandboxes", 7), ("max_created_sandboxes", 9)):
            broken = copy.deepcopy(config)
            broken["limits"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.validate(broken)

    def test_cloud_training_backend_and_strict_tito_are_explicit(self):
        config = self.run_config()
        mutations = (("training_backend", "type", "home5090"),
                     ("training_backend", "spot", True),
                     ("placement", "gpu_isolation", "shared_host"),
                     ("placement", "colocate_all", False),
                     ("placement", "reference", "enabled"), ("trajectory", "api", "completions"),
                     ("trajectory", "return_token_ids", False),
                     ("trajectory", "return_token_logprobs", False))
        for section, key, value in mutations:
            broken = copy.deepcopy(config)
            broken[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                self.validate(broken)

    def test_scale_requires_registered_predecessor(self):
        train = [row["id"] for row in self.manifest()["records"] if row["split"] == "train"]
        previous = self.run_config(train[:16], mode="m2-overfit16")
        current = self.run_config(train[:40], mode="m2-scale40")
        self.assertEqual(self.validate(current, previous_config=previous)["task_ids"], current["task_ids"])
        with self.assertRaisesRegex(ValueError, "predecessor"):
            self.validate(current)
        for bad in (train[1:17], train[:40]):
            prior = self.run_config(bad, mode="m2-overfit16")
            with self.subTest(previous=bad), self.assertRaises(ValueError):
                self.validate(current, previous_config=prior)
        short = self.run_config(train[:2], mode="m2-overfit16")
        with self.assertRaisesRegex(ValueError, "predecessor task count"):
            self.validate(current, previous_config=short)

    def test_each_mode_has_exact_task_cardinality(self):
        train = [row["id"] for row in self.manifest()["records"] if row["split"] == "train"]
        with self.assertRaisesRegex(ValueError, "task count"):
            self.validate(self.run_config(train[:1]))
        for mode, size in (("m2-overfit16", 16), ("m2-scale40", 40), ("m2-scale80", 80)):
            bad = self.run_config(train[:size - 1], mode=mode)
            predecessor_mode = {"m2-overfit16": "m2-bringup", "m2-scale40": "m2-overfit16",
                                "m2-scale80": "m2-scale40"}[mode]
            predecessor_size = {"m2-overfit16": 2, "m2-scale40": 16, "m2-scale80": 40}[mode]
            predecessor = self.run_config(train[:predecessor_size], mode=predecessor_mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "task count"):
                self.validate(bad, previous_config=predecessor)

    def test_canonical_digest_is_order_independent_and_rejects_non_json(self):
        self.assertEqual(m2_contract.canonical_sha256({"b": [2, 1], "a": 3}),
                         m2_contract.canonical_sha256({"a": 3, "b": [2, 1]}))
        with self.assertRaises(ValueError):
            m2_contract.canonical_sha256({"bad": {1, 2}})


if __name__ == "__main__":
    unittest.main()
