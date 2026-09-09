"""Harbor trial to SkyRL step-wise generator bridge with injectable boundaries."""

from __future__ import annotations

import asyncio
from collections import Counter
import copy

from .tito import build_stepwise_batch


_INPUT_KEYS = {
    "prompts", "trajectory_ids", "sampling_params", "env_classes",
    "env_extras", "batch_metadata",
}
_SAMPLING_KEYS = {"temperature", "top_p", "max_tokens", "seed"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identity(value) -> tuple[str, int]:
    _require(isinstance(value, dict) and set(value) == {"instance_id", "repetition_id"},
             "trajectory ID must contain exactly instance_id and repetition_id")
    instance = value["instance_id"]
    repetition = value["repetition_id"]
    _require(isinstance(instance, str) and instance
             and type(repetition) is int and repetition >= 0,
             "invalid trajectory identity")
    return instance, repetition


class HarborSkyRLBridge:
    """Collect exact chat-completion tokens while a Harbor adapter owns trials."""

    def __init__(self, *, inference, trials, expected_proxy_url: str,
                 expected_model_name: str, template_sha256: str,
                 expected_weight_version: str, group_size: int,
                 max_model_len: int, max_observation_tokens: int,
                 trajectory_id_factory=None):
        _require(getattr(inference, "proxy_url", None) == expected_proxy_url,
                 "inference endpoint identity mismatch")
        _require(getattr(inference, "model_name", None) == expected_model_name,
                 "inference model identity mismatch")
        _require(type(group_size) is int and group_size >= 2,
                 "group_size must be at least two")
        self.inference = inference
        self.trials = trials
        self.expected_model_name = expected_model_name
        self.template_sha256 = template_sha256
        self.expected_weight_version = expected_weight_version
        self.group_size = group_size
        self.max_model_len = max_model_len
        self.max_observation_tokens = max_observation_tokens
        self.trajectory_id_factory = trajectory_id_factory or (lambda value: dict(value))
        self.last_audit = None

    def _validate_input(self, input_batch: dict) -> list[tuple[str, int]]:
        _require(isinstance(input_batch, dict) and set(input_batch) == _INPUT_KEYS,
                 "generator input has unknown or missing fields")
        prompts = input_batch["prompts"]
        trajectory_ids = input_batch["trajectory_ids"]
        size = len(prompts) if isinstance(prompts, list) else -1
        _require(size > 0 and isinstance(trajectory_ids, list)
                 and len(trajectory_ids) == size,
                 "prompt and trajectory counts must match")
        for field in ("env_classes", "env_extras"):
            _require(isinstance(input_batch[field], list)
                     and len(input_batch[field]) == size,
                     f"{field} must align with prompts")
        _require(all(item == "harbor" for item in input_batch["env_classes"]),
                 "only the registered Harbor environment is supported")
        metadata = input_batch["batch_metadata"]
        _require(isinstance(metadata, dict)
                 and set(metadata) == {"global_step", "training_phase"}
                 and type(metadata["global_step"]) is int
                 and metadata["global_step"] >= 0
                 and metadata["training_phase"] == "train",
                 "unregistered batch metadata")
        sampling = input_batch["sampling_params"]
        _require(isinstance(sampling, dict) and set(sampling) == _SAMPLING_KEYS,
                 "sampling parameters have unknown or missing fields")
        _require(sampling["temperature"] == 1.0 and sampling["top_p"] == 1.0
                 and sampling["max_tokens"] == 4096
                 and type(sampling["seed"]) is int and sampling["seed"] >= 0,
                 "sampling parameters do not match M2 bringup")

        identities = [_identity(value) for value in trajectory_ids]
        _require(len(identities) == len(set(identities)),
                 "duplicate trajectory identity")
        counts = Counter(instance for instance, _ in identities)
        _require(all(count == self.group_size for count in counts.values()),
                 "every prompt group must have the registered sample count")
        closed = set()
        previous = None
        groups = {}
        for index, ((instance, repetition), prompt) in enumerate(zip(identities, prompts)):
            if previous is not None and instance != previous:
                closed.add(previous)
            _require(instance not in closed, "prompt groups must be contiguous")
            groups.setdefault(instance, {"prompt": prompt, "repetitions": []})
            _require(groups[instance]["prompt"] == prompt,
                     "all repetitions in a group must use the same prompt")
            groups[instance]["repetitions"].append(repetition)
            extra = input_batch["env_extras"][index]
            _require(isinstance(extra, dict) and extra == {"task_id": instance},
                     "environment task identity mismatch")
            previous = instance
        _require(all(sorted(group["repetitions"]) == list(range(self.group_size))
                     for group in groups.values()),
                 "group repetition IDs must be contiguous from zero")
        return identities

    async def _inference_request(self, body: dict, audit_record: dict) -> dict:
        """Wait for an owned in-flight request before propagating cancellation."""
        worker = asyncio.create_task(
            self.inference.chat_completion({"json": copy.deepcopy(body)}))
        try:
            return await asyncio.shield(worker)
        except BaseException:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                response = worker.result()
                usage = response.get("usage") if isinstance(response, dict) else None
                _require(isinstance(usage, dict)
                         and type(usage.get("prompt_tokens")) is int
                         and usage["prompt_tokens"] >= 0
                         and type(usage.get("completion_tokens")) is int
                         and usage["completion_tokens"] >= 0,
                         "complete late inference usage is required")
                audit_record["late_usage"] = dict(usage)
                audit_record["inflight_status"] = "finished_after_cancel"
            except BaseException as late_error:
                audit_record["inflight_status"] = "failed_usage_unknown"
                audit_record["inflight_error_type"] = type(late_error).__name__
            raise

    async def _one(self, prompt, trajectory_id: dict, sampling: dict,
                   audit_record: dict) -> dict:
        steps = []
        usage = []
        audit_record["usage"] = usage

        async def generate(messages, *, observation_token_count: int):
            _require(isinstance(messages, list) and messages,
                     "Harbor must provide a nonempty chat history")
            body = {
                "model": self.expected_model_name,
                "messages": copy.deepcopy(messages),
                "temperature": sampling["temperature"],
                "top_p": sampling["top_p"],
                "max_tokens": sampling["max_tokens"],
                "seed": sampling["seed"] + trajectory_id["repetition_id"],
                "return_token_ids": True,
                "logprobs": True,
                "top_logprobs": 0,
            }
            response = await self._inference_request(body, audit_record)
            _require(isinstance(response, dict)
                     and response.get("model") == self.expected_model_name
                     and response.get("weight_version") == self.expected_weight_version,
                     "inference response identity mismatch")
            choices = response.get("choices")
            _require(isinstance(choices, list) and len(choices) == 1
                     and isinstance(choices[0], dict),
                     "exactly one inference choice is required")
            choice = choices[0]
            prompt_ids = choice.get("prompt_token_ids")
            response_ids = choice.get("token_ids")
            logprob_rows = (choice.get("logprobs") or {}).get("content")
            _require(isinstance(prompt_ids, list) and isinstance(response_ids, list)
                     and response_ids and isinstance(logprob_rows, list)
                     and len(logprob_rows) == len(response_ids)
                     and all(isinstance(row, dict) and set(row) >= {"logprob"}
                             for row in logprob_rows),
                     "token IDs and per-token logprobs are required")
            finish_reason = choice.get("finish_reason")
            _require(finish_reason in {"tool_calls", "stop", "length"},
                     "unregistered inference finish reason")
            usage_row = response.get("usage")
            _require(isinstance(usage_row, dict)
                     and type(usage_row.get("prompt_tokens")) is int
                     and usage_row["prompt_tokens"] >= 0
                     and type(usage_row.get("completion_tokens")) is int
                     and usage_row["completion_tokens"] >= 0,
                     "complete inference usage is required")
            usage.append(dict(usage_row))
            steps.append({
                "prompt_token_ids": list(prompt_ids),
                "response_token_ids": list(response_ids),
                "rollout_logprobs": [row["logprob"] for row in logprob_rows],
                "response_position_ids": list(range(len(prompt_ids),
                                                    len(prompt_ids) + len(response_ids))),
                "stop_reason": "tool_call" if finish_reason == "tool_calls" else finish_reason,
                "observation_token_count": observation_token_count,
                "context_truncated": False,
                "dropped_prompt_tokens": 0,
                "weight_version": response["weight_version"],
            })
            return copy.deepcopy(choice.get("message"))

        result = None
        cleanup_confirmed = False
        try:
            result = await self.trials.run(
                prompt=prompt, trajectory_id=trajectory_id, generate=generate)
        finally:
            cleanup_confirmed = await self.trials.reconcile(trajectory_id)
            audit_record["cleanup_confirmed"] = cleanup_confirmed
        _require(isinstance(result, dict)
                 and set(result) == {"reward", "invalid_reason", "terminal_reason"},
                 "trial result has unknown or missing fields")
        invalid_reason = result["invalid_reason"]
        if cleanup_confirmed is not True:
            invalid_reason = "cleanup_unconfirmed"
        if not steps:
            invalid_reason = invalid_reason or "no_model_steps"
        if invalid_reason is not None:
            _require(isinstance(invalid_reason, str) and invalid_reason,
                     "invalid_reason must be a nonempty string")
            return {"trajectory_id": dict(trajectory_id), "invalid_reason": invalid_reason,
                    "steps": steps, "usage": usage}
        _require(type(result["reward"]) in (int, float) and result["reward"] in (0, 1),
                 "valid trial reward must be exactly 0 or 1")
        _require(result["terminal_reason"] in {"stop", "eos", "length",
                                               "round_budget", "output_budget"},
                 "unregistered trial terminal reason")
        steps[-1]["stop_reason"] = result["terminal_reason"]
        for step in steps[:-1]:
            _require(step["stop_reason"] == "tool_call",
                     "nonterminal model turn must end in a tool call")
        return {"trajectory_id": dict(trajectory_id), "reward": result["reward"],
                "steps": steps, "usage": usage, "invalid_reason": None}

    async def generate(self, input_batch: dict) -> dict:
        identities = self._validate_input(input_batch)
        trajectory_records = []
        self.last_audit = {
            "phase": "running",
            "batch_metadata": copy.deepcopy(input_batch["batch_metadata"]),
            "trajectory_records": trajectory_records,
        }
        results = []
        for prompt, identity in zip(input_batch["prompts"], identities):
            trajectory_id = {"instance_id": identity[0], "repetition_id": identity[1]}
            audit_record = {"trajectory_id": dict(trajectory_id),
                            "cleanup_confirmed": False}
            trajectory_records.append(audit_record)
            results.append(await self._one(prompt, trajectory_id,
                                           input_batch["sampling_params"], audit_record))

        invalid_instances = {result["trajectory_id"]["instance_id"]
                             for result in results if result["invalid_reason"] is not None}
        valid = [result for result in results
                 if result["trajectory_id"]["instance_id"] not in invalid_instances]
        invalid_records = []
        for result in results:
            instance = result["trajectory_id"]["instance_id"]
            if instance not in invalid_instances:
                continue
            reason = result["invalid_reason"]
            if reason is None:
                reason = "group_member_invalid"
            invalid_records.append({"trajectory_id": result["trajectory_id"],
                                    "invalid_reason": reason})
        if not valid:
            self.last_audit = {
                "phase": "invalid_exclusion",
                "batch_metadata": copy.deepcopy(input_batch["batch_metadata"]),
                "valid_groups": 0,
                "invalid_records": copy.deepcopy(invalid_records),
                "trajectory_records": copy.deepcopy(trajectory_records),
            }
        _require(valid, "no valid trajectory groups remain after invalid exclusion")
        batch = build_stepwise_batch(
            [{"trajectory_id": result["trajectory_id"], "reward": result["reward"],
              "steps": result["steps"]} for result in valid],
            template_sha256=self.template_sha256,
            expected_weight_version=self.expected_weight_version,
            max_model_len=self.max_model_len,
            max_observation_tokens=self.max_observation_tokens)
        group_rewards = {}
        for result in valid:
            group_rewards.setdefault(result["trajectory_id"]["instance_id"], []).append(
                result["reward"])
        mixed = sum(set(rewards) == {0, 1} for rewards in group_rewards.values())
        batch["rollout_metrics"] = {
            "m2/valid_trajectories": len(valid),
            "m2/invalid_trajectories": len(results) - len(valid),
            "m2/valid_groups": len(group_rewards),
            "m2/mixed_valid_groups": mixed,
            "m2/prompt_tokens": sum(row["prompt_tokens"] for result in valid
                                    for row in result["usage"]),
            "m2/completion_tokens": sum(row["completion_tokens"] for result in valid
                                        for row in result["usage"]),
        }
        self.last_audit = copy.deepcopy(batch)
        self.last_audit["phase"] = "complete"
        self.last_audit["batch_metadata"] = copy.deepcopy(input_batch["batch_metadata"])
        self.last_audit["valid_groups"] = len(group_rewards)
        self.last_audit["invalid_records"] = copy.deepcopy(invalid_records)
        self.last_audit["trajectory_records"] = copy.deepcopy(trajectory_records)
        official_keys = (
            "prompt_token_ids", "response_ids", "rewards", "loss_masks",
            "stop_reasons", "rollout_metrics", "rollout_logprobs",
            "trajectory_ids", "rollout_expert_indices", "is_last_step",
        )
        output = {key: batch[key] for key in official_keys}
        output["trajectory_ids"] = [self.trajectory_id_factory(value)
                                    for value in batch["trajectory_ids"]]
        return output
