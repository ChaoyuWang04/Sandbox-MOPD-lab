"""Fail-closed validation for registered M2 training inputs and outcomes."""

from __future__ import annotations

import hashlib
import json
import math
import re


_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MUTABLE_IDENTITIES = {"", "main", "master", "latest", "head"}
_MODES = {"m2-bringup", "m2-overfit16", "m2-scale40", "m2-scale80"}
_RUNTIME_KEYS = ("python", "cuda", "driver", "torch", "vllm", "ray", "transformers",
                 "peft", "flash_attn", "flashinfer_python", "daytona")
_STACK_KEYS = {"schema_version", "sources", "model", "runtime", "target", "entrypoint",
               "execution_ready", "unverified"}
_RUN_KEYS = {"schema_version", "mode", "pool_manifest", "pool_manifest_sha256",
             "stack_lock", "stack_lock_sha256", "start_checkpoint_sha256", "task_ids",
             "sampling", "trajectory", "placement", "model", "trainer", "provider",
             "limits", "required_evidence"}
_GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_INVALID_REASONS = {
    "provider_auth", "provider_create_rejected", "provider_create_uncertain",
    "sandbox_setup_failure", "sandbox_timeout", "sandbox_cleanup_uncertain",
    "agent_timeout", "model_transport", "model_trace_incomplete",
    "verifier_timeout", "verifier_protocol_incomplete", "verifier_runtime_error",
    "reward_missing", "task_identity_mismatch", "trajectory_contract_failure",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _object(value, name: str) -> dict:
    _require(isinstance(value, dict), f"{name} must be an object")
    return value


def _text(value, name: str) -> str:
    _require(isinstance(value, str) and value.strip() == value and bool(value),
             f"{name} must be nonempty text")
    _require(value.lower() not in _MUTABLE_IDENTITIES, f"{name} must be immutable")
    return value


def _integer(value, name: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    _require(type(value) is int and value >= minimum, f"{name} must be an integer >= {minimum}")
    if maximum is not None:
        _require(value <= maximum, f"{name} must be <= {maximum}")
    return value


def validate_stack_lock(lock: dict) -> dict:
    """Require immutable source, model and runtime identities."""
    lock = _object(lock, "stack lock")
    _require(set(lock) == _STACK_KEYS, "stack lock has unknown or missing fields")
    _require(lock.get("schema_version") == 1, "unsupported stack lock schema")
    sources = _object(lock.get("sources"), "sources")
    skyrl = _object(sources.get("skyrl"), "SkyRL source")
    harbor = _object(sources.get("harbor"), "Harbor source")
    _require(set(sources) == {"skyrl", "harbor"}, "sources has unknown or missing fields")
    _require(set(skyrl) == {"repository", "version", "commit", "pyproject_sha256", "uv_lock_sha256"},
             "SkyRL source has unknown or missing fields")
    _require(set(harbor) == {"repository", "version", "commit"},
             "Harbor source has unknown or missing fields")
    _text(skyrl.get("repository"), "SkyRL repository")
    _text(skyrl.get("version"), "SkyRL version")
    commit = skyrl.get("commit")
    _require(isinstance(commit, str) and _SHA1.fullmatch(commit) is not None,
             "SkyRL commit must be an immutable 40-character hexadecimal commit")
    for key in ("pyproject_sha256", "uv_lock_sha256"):
        digest = skyrl.get(key)
        _require(isinstance(digest, str) and _SHA256.fullmatch(digest) is not None,
                 f"SkyRL {key} must be an immutable SHA256")
    _text(harbor.get("repository"), "Harbor repository")
    _text(harbor.get("version"), "Harbor version")
    harbor_commit = harbor.get("commit")
    _require(isinstance(harbor_commit, str) and _SHA1.fullmatch(harbor_commit) is not None,
             "Harbor commit must be an immutable 40-character hexadecimal commit")

    model = _object(lock.get("model"), "model")
    _require(set(model) == {"id", "revision", "model_manifest_sha256"},
             "model has unknown or missing fields")
    _text(model.get("id"), "model ID")
    revision = model.get("revision")
    _require(isinstance(revision, str) and _SHA1.fullmatch(revision) is not None,
             "model revision must be an immutable 40-character hexadecimal commit")
    _require(isinstance(model.get("model_manifest_sha256"), str)
             and _SHA256.fullmatch(model["model_manifest_sha256"]) is not None,
             "model manifest must have an immutable SHA256")

    runtime = _object(lock.get("runtime"), "runtime")
    _require(set(runtime) == set(_RUNTIME_KEYS), "runtime has unknown or missing fields")
    for key in _RUNTIME_KEYS:
        _text(runtime.get(key), f"runtime {key}")
    target = _object(lock.get("target"), "target")
    _require(set(target) == {"host", "gpu_name", "gpu_uuid", "gpu_memory_mib", "data_root"},
             "target has unknown or missing fields")
    _require(target.get("host") == "5090home", "M2 target host must be 5090home")
    _require(target.get("gpu_name") == "NVIDIA GeForce RTX 5090", "unexpected target GPU")
    _require(isinstance(target.get("gpu_uuid"), str)
             and _GPU_UUID.fullmatch(target["gpu_uuid"]) is not None, "invalid target GPU UUID")
    _integer(target.get("gpu_memory_mib"), "target GPU memory", minimum=30_000, maximum=40_000)
    _require(target.get("data_root") == "/home/samwang/data/sandbox-rl-MOPD-lab",
             "unexpected M2 data root")
    _require(lock.get("entrypoint") == "examples.train_integrations.harbor.entrypoints.main_harbor",
             "unexpected SkyRL Harbor entrypoint")
    _require(type(lock.get("execution_ready")) is bool, "execution_ready must be boolean")
    unverified = lock.get("unverified")
    _require(isinstance(unverified, list) and len(unverified) == len(set(unverified))
             and all(isinstance(value, str) and value for value in unverified),
             "unverified must be a unique text list")
    _require(lock["execution_ready"] == (not unverified),
             "execution_ready and unverified are inconsistent")
    return lock


def _manifest_train_ids(manifest: dict) -> set[str]:
    manifest = _object(manifest, "pool manifest")
    records = manifest.get("records")
    _require(isinstance(records, list), "pool manifest records must be a list")
    seen: set[str] = set()
    train: set[str] = set()
    for record in records:
        _require(isinstance(record, dict), "pool manifest record must be an object")
        task_id = _text(record.get("id"), "task ID")
        _require(task_id not in seen, f"duplicate manifest task ID: {task_id}")
        seen.add(task_id)
        _require(record.get("split") in {"train", "dev", "final"},
                 f"unregistered split for task: {task_id}")
        if record["split"] == "train":
            train.add(task_id)
    return train


def _validate_task_ids(task_ids, train_ids: set[str], name: str) -> list[str]:
    _require(isinstance(task_ids, list) and bool(task_ids), f"{name} must be a nonempty list")
    _require(all(isinstance(task_id, str) and task_id for task_id in task_ids),
             f"{name} must contain nonempty strings")
    _require(len(task_ids) == len(set(task_ids)), f"{name} contains duplicate task IDs")
    unknown = set(task_ids) - train_ids
    _require(not unknown, f"{name} contains unregistered or non-training tasks: {sorted(unknown)}")
    return task_ids


def validate_run_config(config: dict, manifest: dict, stack_lock: dict, *,
                        manifest_sha256: str, stack_lock_sha256: str,
                        previous_config=None) -> dict:
    """Validate one immutable, bounded M2 run configuration against its pool."""
    config = _object(config, "run config")
    _require(set(config) == _RUN_KEYS, "run config has unknown or missing fields")
    _require(config.get("schema_version") == 1, "unsupported run config schema")
    _require(config.get("mode") in _MODES, "unsupported M2 mode")
    for key in ("pool_manifest_sha256", "stack_lock_sha256"):
        value = config.get(key)
        _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None,
                 f"{key} must be a SHA256")
    _require(config["pool_manifest"] == "configs/m1-pool-v2-r2.json",
             "unexpected pool manifest path")
    _require(config["stack_lock"] == "configs/m2-stack-lock.json",
             "unexpected stack lock path")
    _require(config["pool_manifest_sha256"] == manifest_sha256,
             "pool manifest identity mismatch")
    _require(config["stack_lock_sha256"] == stack_lock_sha256,
             "stack lock identity mismatch")
    validate_stack_lock(stack_lock)
    start_checkpoint = config.get("start_checkpoint_sha256")
    _require(isinstance(start_checkpoint, str) and _SHA256.fullmatch(start_checkpoint) is not None,
             "start_checkpoint_sha256 must be an immutable SHA256")

    train_ids = _manifest_train_ids(manifest)
    task_ids = _validate_task_ids(config.get("task_ids"), train_ids, "task_ids")
    expected_counts = {"m2-overfit16": 16, "m2-scale40": 40, "m2-scale80": 80}
    if config["mode"] == "m2-bringup":
        _require(2 <= len(task_ids) <= 4, "m2-bringup task count must be in [2, 4]")
    else:
        _require(len(task_ids) == expected_counts[config["mode"]],
                 f"{config['mode']} task count must be {expected_counts[config['mode']]}")
    previous_modes = {"m2-overfit16": "m2-bringup", "m2-scale40": "m2-overfit16",
                      "m2-scale80": "m2-scale40"}
    if config["mode"] in previous_modes:
        _require(isinstance(previous_config, dict), "registered predecessor config is required")
        _require(previous_config.get("mode") == previous_modes[config["mode"]],
                 "predecessor mode mismatch")
        previous = _validate_task_ids(previous_config.get("task_ids"), train_ids,
                                      "predecessor task_ids")
        predecessor_mode = previous_config["mode"]
        if predecessor_mode == "m2-bringup":
            _require(2 <= len(previous) <= 4, "predecessor task count must be in [2, 4]")
        else:
            _require(len(previous) == expected_counts[predecessor_mode],
                     f"predecessor task count must be {expected_counts[predecessor_mode]}")
        _require(len(previous) < len(task_ids) and task_ids[:len(previous)] == previous,
                 "task_ids must strictly extend predecessor without reordering")
    else:
        _require(previous_config is None, "bringup must not have a predecessor config")

    sampling = _object(config.get("sampling"), "sampling")
    _require(set(sampling) == {"group_size", "temperature", "top_p", "seed"},
             "sampling has unknown or missing fields")
    group_size = _integer(sampling.get("group_size"), "group_size", minimum=2, maximum=16)
    temperature = sampling.get("temperature")
    _require(type(temperature) in (int, float) and math.isfinite(temperature)
             and 0 < temperature <= 2, "temperature must be finite and in (0, 2]")
    top_p = sampling.get("top_p")
    _require(type(top_p) in (int, float) and math.isfinite(top_p)
             and 0 < top_p <= 1, "top_p must be finite and in (0, 1]")
    _integer(sampling.get("seed"), "seed", minimum=0)

    trajectory = _object(config.get("trajectory"), "trajectory")
    _require(set(trajectory) == {"api", "step_wise_trajectories",
                                 "merge_stepwise_output", "return_token_ids",
                                 "return_token_logprobs", "template_sha256"},
             "trajectory has unknown or missing fields")
    _require(trajectory.get("api") == "chat/completions",
             "M2 uses the registered chat/completions step-wise API")
    _require(trajectory.get("step_wise_trajectories") is True,
             "chat/completions TITO requires step-wise trajectories")
    _require(trajectory.get("merge_stepwise_output") is False,
             "step-wise prefix merging remains disabled until separately verified")
    _require(trajectory.get("return_token_ids") is True,
             "strict M2 TITO requires returned token IDs")
    _require(trajectory.get("return_token_logprobs") is True,
             "strict M2 TITO requires returned token logprobs")
    template = trajectory.get("template_sha256")
    _require(isinstance(template, str) and _SHA256.fullmatch(template) is not None,
             "template_sha256 must be an immutable SHA256")

    placement = _object(config.get("placement"), "placement")
    expected_placement = {
        "exclusive_gpu": True,
        "colocate_all": True,
        "run_engines_locally": True,
        "vllm_sleep_wake": True,
        "policy": "lora_fsdp",
        "reference": "disabled",
        "critic": "disabled",
        "reward_model": "disabled",
    }
    _require(placement == expected_placement,
             "single-GPU M2 placement must match the registered colocated topology")

    model = _object(config.get("model"), "model")
    _require(model == {"max_model_len": 16384,
                       "max_generated_tokens_per_trajectory": 4096,
                       "max_turns": 10, "enable_thinking": False},
             "model runtime must match the registered contract")
    trainer = _object(config.get("trainer"), "trainer")
    _require(trainer == {"strategy": "fsdp2", "lora_rank": 32, "lora_alpha": 64,
                         "target_modules": "all-linear", "learning_rate": 0.00001,
                         "weight_decay": 0.0, "max_grad_norm": 1.0,
                         "advantage_estimator": "grpo", "grpo_norm_by_std": False,
                         "use_kl_loss": False, "cpu_offload": True,
                         "micro_train_batch_size_per_gpu": 1,
                         "micro_forward_batch_size_per_gpu": 1},
             "trainer must match the registered single-GPU LoRA contract")
    provider = _object(config.get("provider"), "provider")
    _require(provider == {"type": "daytona",
                          "credential_file": "/home/samwang/data/sandbox-rl-MOPD-lab/secrets/daytona.env",
                          "credential_file_mode": "0600", "cpus": 1,
                          "memory_mb": 1024, "storage_mb": 3072,
                          "network": False, "ttl_seconds": 300},
             "provider must match the registered private Daytona contract")

    limits = _object(config.get("limits"), "limits")
    _require(set(limits) == {"max_updates", "max_generated_tokens", "max_training_tokens",
                             "max_wall_seconds", "max_created_sandboxes",
                             "daytona_concurrency"},
             "limits has unknown or missing fields")
    _integer(limits.get("max_updates"), "max_updates", maximum=1_000)
    _integer(limits.get("max_generated_tokens"), "max_generated_tokens", maximum=50_000_000)
    _integer(limits.get("max_training_tokens"), "max_training_tokens", maximum=50_000_000)
    _integer(limits.get("max_created_sandboxes"), "max_created_sandboxes", maximum=4_096)
    _integer(limits.get("max_wall_seconds"), "max_wall_seconds", maximum=86_400)
    _integer(limits.get("daytona_concurrency"), "daytona_concurrency", maximum=8)
    required_creations = len(task_ids) * group_size * limits["max_updates"]
    _require(limits["max_created_sandboxes"] == required_creations,
             "max_created_sandboxes must equal the registered task sample count")
    required_tokens = required_creations * model["max_generated_tokens_per_trajectory"]
    _require(limits["max_generated_tokens"] == required_tokens
             and limits["max_training_tokens"] == required_tokens,
             "token limits must equal the registered worst-case trajectory budget")
    if config["mode"] == "m2-bringup":
        _require(limits == {"max_updates": 1, "max_generated_tokens": 32768,
                            "max_training_tokens": 32768, "max_wall_seconds": 5400,
                            "max_created_sandboxes": 8, "daytona_concurrency": 4},
                 "M2 bringup limits must match the approved exact batch")
    evidence = _object(config.get("required_evidence"), "required_evidence")
    _require(evidence == {"mixed_valid_group_count_min": 1,
                          "valid_samples_per_mixed_group_min": 2,
                          "effective_loss_tokens_min": 1,
                          "nonzero_advantage_required": True,
                          "nonzero_pre_step_policy_grad_norm_required": True,
                          "inference_content_receipt_required": True,
                          "next_rollout_weight_binding_required": True,
                          "fresh_process_reload_required": True},
             "required evidence must match the registered M2-A correctness contract")
    return config


def classify_verifier(record: dict) -> dict:
    """Keep infrastructure-invalid outcomes distinct from valid binary rewards."""
    record = _object(record, "verifier record")
    reward = record.get("reward")
    reason = record.get("invalid_reason")
    if reason is not None:
        _require(isinstance(reason, str) and reason.strip() == reason and bool(reason),
                 "invalid outcome requires a nonempty reason")
        _require(reason in _INVALID_REASONS, "invalid reason is not a registered infrastructure category")
        _require(reward is None, "invalid outcome cannot carry a numeric reward")
        return {"classification": "invalid", "reward": None, "invalid_reason": reason}
    _require(reward is not None, "missing reward requires an invalid reason")
    _require(type(reward) in (int, float) and math.isfinite(reward) and reward in (0, 1),
             "valid reward must be numeric binary 0 or 1")
    return {"classification": "valid_reward", "reward": int(reward)}


def canonical_sha256(value) -> str:
    """Digest canonical JSON, rejecting non-JSON and non-finite values."""
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonical JSON") from exc
    return hashlib.sha256(payload).hexdigest()
