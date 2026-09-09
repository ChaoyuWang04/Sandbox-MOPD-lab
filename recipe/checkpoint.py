"""Fail-closed update, weight handoff, and checkpoint evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat


_SHA256 = re.compile(r"[0-9a-f]{64}")
_METRIC_KEYS = {
    "effective_loss_tokens", "advantage_abs_max",
    "pre_step_policy_grad_norm", "update_norm",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _process_identity(value: dict, name: str) -> dict:
    _require(isinstance(value, dict) and set(value) == {"pid", "started_at"},
             f"{name} process identity is incomplete")
    _require(type(value["pid"]) is int and value["pid"] > 0
             and isinstance(value["started_at"], str) and value["started_at"],
             f"{name} process identity is invalid")
    return dict(value)


def tensor_digest(tensors: dict[str, bytes]) -> str:
    """Hash exact named tensor bytes without accepting summaries or output text."""
    _require(isinstance(tensors, dict) and tensors,
             "named tensor content must be nonempty")
    digest = hashlib.sha256()
    for name in sorted(tensors):
        value = tensors[name]
        _require(isinstance(name, str) and name
                 and isinstance(value, (bytes, bytearray, memoryview)),
                 "tensor names and exact byte content are required")
        name_bytes = name.encode("utf-8")
        value_bytes = bytes(value)
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        digest.update(len(value_bytes).to_bytes(8, "big"))
        digest.update(value_bytes)
    return digest.hexdigest()


def _finite_positive(value, name: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
             f"{name} must be finite and positive")
    return float(value)


def build_update_evidence(*, prior_weight_version: str, next_global_step: int,
                          pre_base_tensors: dict[str, bytes],
                          pre_adapter_tensors: dict[str, bytes],
                          post_base_tensors: dict[str, bytes],
                          post_adapter_tensors: dict[str, bytes],
                          optimizer_parameter_names: list[str],
                          allowed_adapter_parameter_names: list[str],
                          trainer_process_identity: dict,
                          metrics: dict) -> dict:
    """Prove an adapter-only optimizer step changed exact tensor content."""
    _require(isinstance(prior_weight_version, str) and prior_weight_version,
             "prior weight version is required")
    _require(type(next_global_step) is int and next_global_step > 0,
             "next global step must be positive")
    _require(isinstance(metrics, dict) and set(metrics) == _METRIC_KEYS,
             "update metrics have unknown or missing fields")
    _require(type(metrics["effective_loss_tokens"]) is int
             and metrics["effective_loss_tokens"] > 0,
             "effective_loss_tokens must be positive")
    normalized_metrics = {
        "effective_loss_tokens": metrics["effective_loss_tokens"],
        "advantage_abs_max": _finite_positive(
            metrics["advantage_abs_max"], "advantage_abs_max"),
        "pre_step_policy_grad_norm": _finite_positive(
            metrics["pre_step_policy_grad_norm"], "pre_step_policy_grad_norm"),
        "update_norm": _finite_positive(metrics["update_norm"], "update_norm"),
    }
    _require(isinstance(allowed_adapter_parameter_names, list)
             and allowed_adapter_parameter_names
             and all(isinstance(name, str) and name
                     for name in allowed_adapter_parameter_names)
             and len(set(allowed_adapter_parameter_names))
             == len(allowed_adapter_parameter_names),
             "adapter parameter allowlist is invalid")
    allowed = sorted(allowed_adapter_parameter_names)
    _require(set(pre_base_tensors).isdisjoint(allowed)
             and set(post_base_tensors).isdisjoint(allowed),
             "base and adapter parameter identities overlap")
    _require(sorted(pre_adapter_tensors) == allowed
             and sorted(post_adapter_tensors) == allowed,
             "adapter tensor identities do not match the allowlist")
    _require(isinstance(optimizer_parameter_names, list)
             and sorted(optimizer_parameter_names) == allowed
             and len(optimizer_parameter_names) == len(allowed),
             "optimizer parameters must equal the adapter allowlist")
    _require(set(pre_base_tensors) == set(post_base_tensors),
             "base tensor identities changed")

    pre_base = tensor_digest(pre_base_tensors)
    post_base = tensor_digest(post_base_tensors)
    pre_adapter = tensor_digest(pre_adapter_tensors)
    post_adapter = tensor_digest(post_adapter_tensors)
    _require(pre_base == post_base, "base tensor content changed")
    _require(pre_adapter != post_adapter, "adapter tensor content did not change")
    next_version = f"policy-v{next_global_step}-{post_adapter[:16]}"
    return {
        "schema_version": 1,
        "prior_weight_version": prior_weight_version,
        "next_weight_version": next_version,
        "next_global_step": next_global_step,
        "trainer_process_identity": _process_identity(
            trainer_process_identity, "trainer"),
        "base_parameter_names": sorted(pre_base_tensors),
        "adapter_parameter_names": allowed,
        "optimizer_parameter_names": sorted(optimizer_parameter_names),
        "pre_base_sha256": pre_base,
        "post_base_sha256": post_base,
        "pre_adapter_sha256": pre_adapter,
        "post_adapter_sha256": post_adapter,
        "metrics": normalized_metrics,
    }


def verify_inference_load(evidence: dict, *, loaded_base_tensors: dict[str, bytes],
                          loaded_adapter_tensors: dict[str, bytes],
                          echoed_weight_version: str) -> dict:
    """Require inference-side tensor content; an echoed version is insufficient."""
    _require(echoed_weight_version == evidence.get("next_weight_version"),
             "inference echoed a stale weight version")
    _require(sorted(loaded_base_tensors) == evidence.get("base_parameter_names"),
             "loaded base tensor identities differ")
    _require(sorted(loaded_adapter_tensors) == evidence.get("adapter_parameter_names"),
             "loaded adapter tensor identities differ")
    base_digest = tensor_digest(loaded_base_tensors)
    adapter_digest = tensor_digest(loaded_adapter_tensors)
    _require(base_digest == evidence.get("post_base_sha256"),
             "loaded base tensor content differs")
    _require(adapter_digest == evidence.get("post_adapter_sha256"),
             "loaded adapter tensor content differs")
    return {
        "schema_version": 1,
        "weight_version": echoed_weight_version,
        "loaded_base_sha256": base_digest,
        "loaded_adapter_sha256": adapter_digest,
    }


def verify_next_rollout(evidence: dict, inference_receipt: dict, *,
                        rollout_weight_versions: list[str]) -> bool:
    expected = evidence.get("next_weight_version")
    _require(inference_receipt.get("weight_version") == expected
             and inference_receipt.get("loaded_base_sha256")
             == evidence.get("post_base_sha256")
             and inference_receipt.get("loaded_adapter_sha256")
             == evidence.get("post_adapter_sha256"),
             "inference load receipt does not bind updated tensor content")
    _require(isinstance(rollout_weight_versions, list) and rollout_weight_versions
             and all(version == expected for version in rollout_weight_versions),
             "next rollout weight versions are stale or incomplete")
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def write_checkpoint_manifest(manifest_path: Path, *, evidence: dict,
                              checkpoint_files: dict) -> dict:
    """Atomically publish a content-bound manifest without overwriting evidence."""
    target = Path(manifest_path)
    _require(target.is_absolute() and target.name == "manifest.json",
             "checkpoint manifest path must be an absolute manifest.json")
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    parent = target.parent
    if parent.exists():
        _require(parent.is_dir() and not parent.is_symlink(),
                 "checkpoint manifest parent is unsafe")
    else:
        parent.mkdir(mode=0o750)
    _require(isinstance(checkpoint_files, dict) and checkpoint_files,
             "checkpoint files must be registered")
    files = {}
    for logical_name in sorted(checkpoint_files):
        row = checkpoint_files[logical_name]
        _require(isinstance(logical_name, str) and logical_name
                 and "/" not in logical_name and logical_name not in {".", ".."}
                 and isinstance(row, dict) and set(row) == {"path", "sha256"}
                 and isinstance(row["sha256"], str)
                 and _SHA256.fullmatch(row["sha256"]),
                 "checkpoint file registration is invalid")
        path = Path(row["path"])
        _require(path.is_absolute() and path.is_file() and not path.is_symlink()
                 and stat.S_ISREG(path.stat().st_mode),
                 "checkpoint artifact must be a regular file")
        _require(_file_sha256(path) == row["sha256"],
                 "checkpoint artifact content digest mismatch")
        files[logical_name] = row["sha256"]
    payload = {
        "schema_version": 1,
        "phase": "complete",
        "next_weight_version": evidence.get("next_weight_version"),
        "post_base_sha256": evidence.get("post_base_sha256"),
        "post_adapter_sha256": evidence.get("post_adapter_sha256"),
        "files": files,
    }
    payload["manifest_sha256"] = _canonical_sha256(payload)
    temp = parent / ".manifest.json.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temp, flags, 0o640)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, ensure_ascii=True,
                      allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise
    return dict(payload)


def verify_fresh_reload(evidence: dict, *, manifest_path: Path,
                        reloaded_base_tensors: dict[str, bytes],
                        reloaded_adapter_tensors: dict[str, bytes],
                        process_identity: dict) -> dict:
    """Verify a different process reloaded checkpoint-bound tensor content."""
    identity = _process_identity(process_identity, "reload")
    trainer = _process_identity(evidence.get("trainer_process_identity"), "trainer")
    _require(identity != trainer, "fresh reload must run in a different process")
    path = Path(manifest_path)
    _require(path.is_file() and not path.is_symlink(),
             "checkpoint manifest is missing or unsafe")
    manifest = json.loads(path.read_text(encoding="ascii"))
    digest = manifest.pop("manifest_sha256", None)
    _require(isinstance(digest, str) and _SHA256.fullmatch(digest)
             and digest == _canonical_sha256(manifest),
             "checkpoint manifest content digest mismatch")
    _require(manifest.get("phase") == "complete"
             and manifest.get("next_weight_version") == evidence.get("next_weight_version")
             and manifest.get("post_base_sha256") == evidence.get("post_base_sha256")
             and manifest.get("post_adapter_sha256") == evidence.get("post_adapter_sha256"),
             "checkpoint manifest does not bind the completed update")
    base_digest = tensor_digest(reloaded_base_tensors)
    adapter_digest = tensor_digest(reloaded_adapter_tensors)
    _require(base_digest == evidence.get("post_base_sha256"),
             "reloaded base tensor content differs")
    _require(adapter_digest == evidence.get("post_adapter_sha256"),
             "reloaded adapter tensor content differs")
    return {
        "schema_version": 1,
        "manifest_sha256": digest,
        "weight_version": evidence["next_weight_version"],
        "reloaded_base_sha256": base_digest,
        "reloaded_adapter_sha256": adapter_digest,
        "process_identity": identity,
    }
