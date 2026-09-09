"""Dependency-light exact token-in/token-out records for SkyRL step-wise training."""

from __future__ import annotations

import hashlib
import json
import math
import re


_SHA256 = re.compile(r"[0-9a-f]{64}")
_INTERMEDIATE_STOPS = {"tool_call"}
_TERMINAL_STOPS = {"stop", "eos", "length", "round_budget", "output_budget"}
_STEP_KEYS = {
    "prompt_token_ids", "response_token_ids", "rollout_logprobs",
    "response_position_ids", "stop_reason", "observation_token_count",
    "context_truncated", "dropped_prompt_tokens", "weight_version",
}
_BATCH_KEYS = {
    "schema_version", "template_sha256", "prompt_token_ids", "response_ids",
    "rewards", "loss_masks", "stop_reasons", "rollout_metrics",
    "rollout_logprobs", "trajectory_ids", "rollout_expert_indices",
    "is_last_step", "response_position_ids", "weight_versions",
    "context_truncation", "observation_token_counts", "step_receipt_sha256",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _tokens(value, name: str, *, nonempty: bool = True) -> list[int]:
    _require(isinstance(value, list), f"{name} must be a list")
    _require(not nonempty or bool(value), f"{name} must be nonempty")
    _require(all(type(token) is int and token >= 0 for token in value),
             f"{name} must contain nonnegative integer token IDs")
    return list(value)


def _finite_logprobs(value, expected: int) -> list[float]:
    _require(isinstance(value, list) and len(value) == expected,
             "one rollout logprob is required per response token")
    _require(all(type(item) in (int, float) and math.isfinite(item) for item in value),
             "rollout logprobs must be finite numbers")
    return [float(item) for item in value]


def _trajectory_id(value) -> dict:
    _require(isinstance(value, dict) and set(value) == {"instance_id", "repetition_id"},
             "trajectory_id must contain exactly instance_id and repetition_id")
    _require(isinstance(value["instance_id"], str) and value["instance_id"],
             "trajectory instance_id must be a nonempty string")
    _require(type(value["repetition_id"]) is int and value["repetition_id"] >= 0,
             "trajectory repetition_id must be a nonnegative integer")
    return dict(value)


def _receipt(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _step_payload(batch: dict, index: int) -> dict:
    return {
        "template_sha256": batch["template_sha256"],
        "prompt_token_ids": batch["prompt_token_ids"][index],
        "response_ids": batch["response_ids"][index],
        "rewards": batch["rewards"][index],
        "loss_masks": batch["loss_masks"][index],
        "stop_reason": batch["stop_reasons"][index],
        "rollout_logprobs": batch["rollout_logprobs"][index],
        "trajectory_id": batch["trajectory_ids"][index],
        "is_last_step": batch["is_last_step"][index],
        "response_position_ids": batch["response_position_ids"][index],
        "weight_version": batch["weight_versions"][index],
        "context_truncation": batch["context_truncation"][index],
        "observation_token_count": batch["observation_token_counts"][index],
    }


def build_stepwise_batch(trajectories: list[dict], *, template_sha256: str,
                         expected_weight_version: str, max_model_len: int,
                         max_observation_tokens: int) -> dict:
    """Build exact per-turn samples without accepting text or re-tokenization."""
    _require(isinstance(trajectories, list) and trajectories,
             "trajectories must be a nonempty list")
    _require(isinstance(template_sha256, str) and _SHA256.fullmatch(template_sha256),
             "template_sha256 must be immutable")
    _require(isinstance(expected_weight_version, str) and expected_weight_version,
             "expected_weight_version must be nonempty")
    _require(type(max_model_len) is int and max_model_len > 0,
             "max_model_len must be positive")
    _require(type(max_observation_tokens) is int and max_observation_tokens >= 0,
             "max_observation_tokens must be nonnegative")

    batch = {
        "schema_version": 1,
        "template_sha256": template_sha256,
        "prompt_token_ids": [], "response_ids": [], "rewards": [],
        "loss_masks": [], "stop_reasons": [], "rollout_metrics": None,
        "rollout_logprobs": [], "trajectory_ids": [],
        "rollout_expert_indices": None, "is_last_step": [],
        "response_position_ids": [], "weight_versions": [],
        "context_truncation": [], "observation_token_counts": [],
        "step_receipt_sha256": [],
    }
    seen_ids = set()
    for trajectory in trajectories:
        _require(isinstance(trajectory, dict)
                 and set(trajectory) == {"trajectory_id", "reward", "steps"},
                 "trajectory has unknown or missing fields")
        trajectory_id = _trajectory_id(trajectory["trajectory_id"])
        identity = (trajectory_id["instance_id"], trajectory_id["repetition_id"])
        _require(identity not in seen_ids, "duplicate trajectory identity")
        seen_ids.add(identity)
        reward = trajectory["reward"]
        _require(type(reward) in (int, float) and reward in (0, 1),
                 "trajectory reward must be exactly 0 or 1")
        steps = trajectory["steps"]
        _require(isinstance(steps, list) and steps, "trajectory steps must be nonempty")
        for step_index, step in enumerate(steps):
            _require(isinstance(step, dict) and set(step) == _STEP_KEYS,
                     "step has unknown or missing fields; text re-tokenization is forbidden")
            prompt = _tokens(step["prompt_token_ids"], "prompt_token_ids")
            response = _tokens(step["response_token_ids"], "response_token_ids")
            _require(len(prompt) + len(response) <= max_model_len,
                     "step exceeds max_model_len")
            logprobs = _finite_logprobs(step["rollout_logprobs"], len(response))
            positions = _tokens(step["response_position_ids"],
                                "response_position_ids")
            _require(positions == list(range(len(prompt), len(prompt) + len(response))),
                     "response positions do not align with exact prompt and response tokens")
            observation_count = step["observation_token_count"]
            _require(type(observation_count) is int
                     and 0 <= observation_count <= max_observation_tokens,
                     "tool observation context exceeds the registered bound")
            truncated = step["context_truncated"]
            dropped = step["dropped_prompt_tokens"]
            _require(type(truncated) is bool and type(dropped) is int and dropped >= 0,
                     "invalid context truncation evidence")
            _require((truncated and dropped > 0) or (not truncated and dropped == 0),
                     "context truncation flag and dropped-token count disagree")
            _require(step["weight_version"] == expected_weight_version,
                     "stale or unexpected inference weight version")
            is_last = step_index == len(steps) - 1
            allowed_stops = _TERMINAL_STOPS if is_last else _INTERMEDIATE_STOPS
            _require(step["stop_reason"] in allowed_stops,
                     "stop reason does not match the step boundary")

            token_rewards = [0.0] * len(response)
            if is_last:
                token_rewards[-1] = float(reward)
            batch["prompt_token_ids"].append(prompt)
            batch["response_ids"].append(response)
            batch["rewards"].append(token_rewards)
            batch["loss_masks"].append([1] * len(response))
            batch["stop_reasons"].append(step["stop_reason"])
            batch["rollout_logprobs"].append(logprobs)
            batch["trajectory_ids"].append(dict(trajectory_id))
            batch["is_last_step"].append(is_last)
            batch["response_position_ids"].append(positions)
            batch["weight_versions"].append(step["weight_version"])
            batch["context_truncation"].append(
                {"truncated": truncated, "dropped_prompt_tokens": dropped})
            batch["observation_token_counts"].append(observation_count)
            batch["step_receipt_sha256"].append(
                _receipt(_step_payload(batch, len(batch["response_ids"]) - 1)))

    validate_stepwise_batch(
        batch, expected_template_sha256=template_sha256,
        expected_weight_version=expected_weight_version,
        max_model_len=max_model_len,
        max_observation_tokens=max_observation_tokens)
    return batch


def validate_stepwise_batch(batch: dict, *, expected_template_sha256: str,
                            expected_weight_version: str, max_model_len: int,
                            max_observation_tokens: int) -> bool:
    """Fail closed on any token, mask, logprob, boundary, or identity drift."""
    _require(isinstance(batch, dict) and set(batch) == _BATCH_KEYS,
             "step-wise batch has unknown or missing fields")
    _require(batch["schema_version"] == 1, "unsupported step-wise schema")
    _require(batch["template_sha256"] == expected_template_sha256
             and isinstance(expected_template_sha256, str)
             and _SHA256.fullmatch(expected_template_sha256),
             "chat template identity mismatch")
    fields = (
        "prompt_token_ids", "response_ids", "rewards", "loss_masks",
        "stop_reasons", "rollout_logprobs", "trajectory_ids", "is_last_step",
        "response_position_ids", "weight_versions", "context_truncation",
        "observation_token_counts", "step_receipt_sha256",
    )
    size = len(batch["response_ids"]) if isinstance(batch["response_ids"], list) else -1
    _require(size > 0 and all(isinstance(batch[field], list)
                             and len(batch[field]) == size for field in fields),
             "all step-wise fields must have the same nonzero length")
    _require(batch["rollout_metrics"] is None
             and batch["rollout_expert_indices"] is None,
             "unregistered optional output fields must remain null")

    closed = set()
    previous = None
    for index in range(size):
        prompt = _tokens(batch["prompt_token_ids"][index], "prompt_token_ids")
        response = _tokens(batch["response_ids"][index], "response_ids")
        _require(len(prompt) + len(response) <= max_model_len,
                 "step exceeds max_model_len")
        _finite_logprobs(batch["rollout_logprobs"][index], len(response))
        _require(batch["loss_masks"][index] == [1] * len(response),
                 "step-wise assistant response masks must be all ones")
        positions = _tokens(batch["response_position_ids"][index],
                            "response_position_ids")
        _require(positions == list(range(len(prompt), len(prompt) + len(response))),
                 "response positions do not align")
        _require(batch["weight_versions"][index] == expected_weight_version,
                 "stale or unexpected inference weight version")
        observation_count = batch["observation_token_counts"][index]
        _require(type(observation_count) is int
                 and 0 <= observation_count <= max_observation_tokens,
                 "tool observation context exceeds the registered bound")
        truncation = batch["context_truncation"][index]
        _require(isinstance(truncation, dict)
                 and set(truncation) == {"truncated", "dropped_prompt_tokens"}
                 and type(truncation["truncated"]) is bool
                 and type(truncation["dropped_prompt_tokens"]) is int
                 and truncation["dropped_prompt_tokens"] >= 0,
                 "invalid context truncation evidence")
        _require((truncation["truncated"] and truncation["dropped_prompt_tokens"] > 0)
                 or (not truncation["truncated"]
                     and truncation["dropped_prompt_tokens"] == 0),
                 "context truncation flag and dropped-token count disagree")
        identity_dict = _trajectory_id(batch["trajectory_ids"][index])
        identity = (identity_dict["instance_id"], identity_dict["repetition_id"])
        if previous is not None and identity != previous:
            closed.add(previous)
            _require(batch["is_last_step"][index - 1] is True,
                     "trajectory boundary is missing its terminal marker")
        _require(identity not in closed, "trajectory steps must be contiguous")
        next_same = index + 1 < size and batch["trajectory_ids"][index + 1] == identity_dict
        _require(type(batch["is_last_step"][index]) is bool
                 and batch["is_last_step"][index] is (not next_same),
                 "is_last_step does not match contiguous trajectory boundaries")
        allowed_stops = _TERMINAL_STOPS if batch["is_last_step"][index] else _INTERMEDIATE_STOPS
        _require(batch["stop_reasons"][index] in allowed_stops,
                 "stop reason does not match the step boundary")
        token_rewards = batch["rewards"][index]
        _require(isinstance(token_rewards, list) and len(token_rewards) == len(response)
                 and all(type(value) in (int, float) and value in (0, 1)
                         for value in token_rewards),
                 "token rewards must be aligned binary values")
        if batch["is_last_step"][index]:
            _require(all(value == 0 for value in token_rewards[:-1]),
                     "only the terminal response token may carry reward")
        else:
            _require(all(value == 0 for value in token_rewards),
                     "intermediate steps must have zero token rewards")
        receipt = batch["step_receipt_sha256"][index]
        _require(isinstance(receipt, str) and _SHA256.fullmatch(receipt)
                 and receipt == _receipt(_step_payload(batch, index)),
                 "step content receipt mismatch")
        previous = identity
    _require(batch["is_last_step"][-1] is True,
             "final sample must close its trajectory")
    return True
