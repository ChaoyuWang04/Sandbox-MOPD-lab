import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


def tensors(*, adapter=b"adapter-before", base=b"base-fixed"):
    return ({"model.weight": base}, {"lora_A.weight": adapter})


class M2CheckpointTests(unittest.TestCase):
    def valid_update(self):
        from recipe.checkpoint import build_update_evidence

        pre_base, pre_adapter = tensors()
        post_base, post_adapter = tensors(adapter=b"adapter-after")
        return build_update_evidence(
            prior_weight_version="policy-v0",
            next_global_step=1,
            pre_base_tensors=pre_base,
            pre_adapter_tensors=pre_adapter,
            post_base_tensors=post_base,
            post_adapter_tensors=post_adapter,
            optimizer_parameter_names=["lora_A.weight"],
            allowed_adapter_parameter_names=["lora_A.weight"],
            trainer_process_identity={"pid": 1234,
                                      "started_at": "2026-09-09T00:00:00Z"},
            metrics={"effective_loss_tokens": 8, "advantage_abs_max": 0.75,
                     "pre_step_policy_grad_norm": 0.25, "update_norm": 0.125},
        )

    def test_nonzero_update_binds_base_adapter_optimizer_and_metrics(self):
        evidence = self.valid_update()
        self.assertEqual(evidence["prior_weight_version"], "policy-v0")
        self.assertTrue(evidence["next_weight_version"].startswith("policy-v1-"))
        self.assertEqual(evidence["pre_base_sha256"], evidence["post_base_sha256"])
        self.assertNotEqual(evidence["pre_adapter_sha256"],
                            evidence["post_adapter_sha256"])
        self.assertEqual(evidence["optimizer_parameter_names"], ["lora_A.weight"])

    def test_zero_nonfinite_or_wrong_optimizer_fails_closed(self):
        from recipe.checkpoint import build_update_evidence

        pre_base, pre_adapter = tensors()
        post_base, post_adapter = tensors(adapter=b"adapter-after")
        base = dict(
            prior_weight_version="policy-v0", next_global_step=1,
            pre_base_tensors=pre_base, pre_adapter_tensors=pre_adapter,
            post_base_tensors=post_base, post_adapter_tensors=post_adapter,
            optimizer_parameter_names=["lora_A.weight"],
            allowed_adapter_parameter_names=["lora_A.weight"],
            trainer_process_identity={"pid": 1234,
                                      "started_at": "2026-09-09T00:00:00Z"},
            metrics={"effective_loss_tokens": 8, "advantage_abs_max": 0.75,
                     "pre_step_policy_grad_norm": 0.25, "update_norm": 0.125})
        mutations = [
            ("metrics", "effective_loss_tokens", 0),
            ("metrics", "advantage_abs_max", 0.0),
            ("metrics", "pre_step_policy_grad_norm", float("nan")),
            ("metrics", "update_norm", 0.0),
        ]
        for outer, inner, value in mutations:
            kwargs = copy.deepcopy(base)
            kwargs[outer][inner] = value
            with self.subTest(inner=inner), self.assertRaises(ValueError):
                build_update_evidence(**kwargs)
        kwargs = copy.deepcopy(base)
        kwargs["optimizer_parameter_names"] = ["model.weight"]
        with self.assertRaisesRegex(ValueError, "optimizer"):
            build_update_evidence(**kwargs)

    def test_base_mutation_or_unchanged_adapter_cannot_pass(self):
        from recipe.checkpoint import build_update_evidence

        pre_base, pre_adapter = tensors()
        common = dict(
            prior_weight_version="policy-v0", next_global_step=1,
            pre_base_tensors=pre_base, pre_adapter_tensors=pre_adapter,
            optimizer_parameter_names=["lora_A.weight"],
            allowed_adapter_parameter_names=["lora_A.weight"],
            trainer_process_identity={"pid": 1234,
                                      "started_at": "2026-09-09T00:00:00Z"},
            metrics={"effective_loss_tokens": 8, "advantage_abs_max": 0.75,
                     "pre_step_policy_grad_norm": 0.25, "update_norm": 0.125})
        with self.assertRaisesRegex(ValueError, "base"):
            build_update_evidence(
                **common,
                post_base_tensors={"model.weight": b"mutated"},
                post_adapter_tensors={"lora_A.weight": b"adapter-after"})
        with self.assertRaisesRegex(ValueError, "adapter"):
            build_update_evidence(
                **common, post_base_tensors=pre_base,
                post_adapter_tensors=pre_adapter)
        overlap = copy.deepcopy(common)
        overlap["pre_base_tensors"]["lora_A.weight"] = b"overlap"
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_update_evidence(
                **overlap, post_base_tensors=overlap["pre_base_tensors"],
                post_adapter_tensors={"lora_A.weight": b"adapter-after"})

    def test_inference_receipt_requires_loaded_tensor_content_and_next_binding(self):
        from recipe.checkpoint import verify_inference_load, verify_next_rollout

        evidence = self.valid_update()
        base, adapter = tensors(adapter=b"adapter-after")
        receipt = verify_inference_load(
            evidence, loaded_base_tensors=base, loaded_adapter_tensors=adapter,
            echoed_weight_version=evidence["next_weight_version"])
        self.assertEqual(receipt["loaded_adapter_sha256"],
                         evidence["post_adapter_sha256"])
        self.assertTrue(verify_next_rollout(
            evidence, receipt,
            rollout_weight_versions=[evidence["next_weight_version"]] * 4))

        stale_base, stale_adapter = tensors()
        with self.assertRaisesRegex(ValueError, "loaded adapter"):
            verify_inference_load(
                evidence, loaded_base_tensors=stale_base,
                loaded_adapter_tensors=stale_adapter,
                echoed_weight_version=evidence["next_weight_version"])
        with self.assertRaisesRegex(ValueError, "rollout weight"):
            verify_next_rollout(evidence, receipt,
                                rollout_weight_versions=["policy-v0"])

    def test_atomic_manifest_and_fresh_process_reload_receipt(self):
        from recipe.checkpoint import (
            verify_fresh_reload,
            write_checkpoint_manifest,
        )

        evidence = self.valid_update()
        base, adapter = tensors(adapter=b"adapter-after")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "adapter.safetensors"
            artifact.write_bytes(b"saved-adapter")
            artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = root / "checkpoint" / "manifest.json"
            result = write_checkpoint_manifest(
                manifest, evidence=evidence,
                checkpoint_files={"adapter.safetensors": {
                    "path": str(artifact), "sha256": artifact_sha}})
            self.assertEqual(result["phase"], "complete")
            self.assertTrue(manifest.is_file())
            self.assertFalse((manifest.parent / ".manifest.json.tmp").exists())
            on_disk = json.loads(manifest.read_text())
            self.assertEqual(on_disk["manifest_sha256"], result["manifest_sha256"])

            reload_receipt = verify_fresh_reload(
                evidence, manifest_path=manifest,
                reloaded_base_tensors=base, reloaded_adapter_tensors=adapter,
                process_identity={"pid": 4321, "started_at": "2026-09-09T00:00:00Z"})
            self.assertEqual(reload_receipt["process_identity"]["pid"], 4321)
            self.assertEqual(reload_receipt["reloaded_adapter_sha256"],
                             evidence["post_adapter_sha256"])

            with self.assertRaises(FileExistsError):
                write_checkpoint_manifest(
                    manifest, evidence=evidence,
                    checkpoint_files={"adapter.safetensors": {
                        "path": str(artifact), "sha256": artifact_sha}})


if __name__ == "__main__":
    unittest.main()
