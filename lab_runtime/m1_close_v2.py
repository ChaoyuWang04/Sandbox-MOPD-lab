"""Pure planning and exactly-once ledger for the frozen M1 close campaign."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .home5090 import SETTINGS
from .task_sources import atomic_json


_SHA256 = re.compile(r"[0-9a-f]{64}")
_PHASES = ("tb-controls", "train-screen", "dev-baseline")
_TOP_KEYS = {"schema_version", "campaign", "manifest", "manifest_sha256", "model",
             "phases", "resource_profiles", "limits"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _manifest_index(manifest):
    _require(isinstance(manifest, dict) and isinstance(manifest.get("records"), list),
             "manifest records required")
    rows = {}
    for row in manifest["records"]:
        _require(isinstance(row, dict) and isinstance(row.get("id"), str)
                 and row["id"] not in rows, "manifest task identity invalid")
        _require(row.get("split") in {"train", "dev", "final"}, "manifest split invalid")
        hashes = row.get("task_files_sha256")
        _require(isinstance(hashes, dict) and hashes
                 and all(isinstance(name, str) and isinstance(value, str)
                         and _SHA256.fullmatch(value) for name, value in hashes.items()),
                 "manifest task hashes invalid")
        rows[row["id"]] = row
    return rows


def _base_plan(row, phase, agent, attempt_id, profile):
    return {"phase": phase, "attempt_id": attempt_id, "task_id": row["id"],
            "task_path": row["task_path"], "task_files_sha256": row["task_files_sha256"],
            "source": row["source"], "split": row["split"],
            "primary_skill": row["primary_skill"], "agent": agent,
            "resource_profile": profile}


def validate_and_plan(config, manifest, manifest_sha256):
    """Validate the entire frozen campaign and return deterministic phase plans."""
    _require(isinstance(config, dict) and set(config) == _TOP_KEYS, "M1 close config fields invalid")
    _require(config.get("schema_version") == 1 and config.get("campaign") == "m1-close-v2",
             "M1 close identity invalid")
    _require(config.get("manifest") == "configs/m1-pool-v2-r2.json"
             and config.get("manifest_sha256") == manifest_sha256
             and _SHA256.fullmatch(str(manifest_sha256)), "manifest identity mismatch")
    _require(config.get("model") == {"id": SETTINGS["model"], "revision": SETTINGS["revision"],
             "max_model_len": 16384, "max_output_tokens": 4096, "max_turns": 10},
             "model contract mismatch")
    profiles = config.get("resource_profiles")
    _require(profiles == {
        "self": {"cpus": 1, "memory_mb": 1024, "storage_mb": 3072,
                 "network": False, "ttl_seconds": 300, "trial_seconds": 360},
        "swe": {"cpus": 4, "memory_mb": 8192, "storage_mb": 10240,
                "network": True, "ttl_seconds": 3600, "trial_seconds": 3600},
        "terminal-bench": {"cpus": 1, "memory_mb": 2048, "storage_mb": 10240,
                           "network": True, "ttl_seconds": 3600, "trial_seconds": 1800}},
        "resource profiles changed")
    _require(config.get("limits") == {"daytona_concurrency": 1, "max_total_creates": 102,
             "max_consecutive_invalid": 3, "max_wall_seconds": 21600,
             "daytona_reserve_usd": 15.0},
             "campaign limits changed")
    phases = config.get("phases")
    _require(isinstance(phases, dict) and set(phases) == set(_PHASES), "campaign phases invalid")
    rows = _manifest_index(manifest)

    tb = phases["tb-controls"]
    _require(tb == {"task_id": "regex-log", "agents": ["nop", "oracle"]},
             "Terminal-Bench control changed")
    tb_row = rows.get(tb["task_id"])
    _require(tb_row is not None and tb_row["source"] == "terminal-bench"
             and tb_row["split"] == "final", "Terminal-Bench control identity invalid")
    plans = {phase: [] for phase in _PHASES}
    for agent in tb["agents"]:
        item = _base_plan(tb_row, "tb-controls", agent, f"tb-controls/{tb_row['id']}/{agent}",
                          "terminal-bench")
        item.update(repetition_id=0, group_id=None, seed=None, temperature=None, top_p=None)
        plans["tb-controls"].append(item)

    screen = phases["train-screen"]
    _require(isinstance(screen, dict) and set(screen) == {"candidate_task_ids", "group_size",
             "temperature", "top_p", "seed", "select_per_skill"}, "screen fields invalid")
    candidates = screen["candidate_task_ids"]
    _require(isinstance(candidates, list) and len(candidates) == 20
             and len(candidates) == len(set(candidates)), "screen candidates invalid")
    _require(screen == {**screen, "group_size": 4, "temperature": 1.0, "top_p": 1.0,
             "seed": 930001, "select_per_skill": {"A": 8, "B": 8}},
             "screen sampling changed")
    _require(sum(rows[task_id]["primary_skill"] == "A" for task_id in candidates) == 10
             and sum(rows[task_id]["primary_skill"] == "B" for task_id in candidates) == 10,
             "screen must contain A10+B10")
    for task_index, task_id in enumerate(candidates):
        row = rows.get(task_id)
        _require(row is not None and row["split"] == "train" and row["source"] == "self",
                 "screen candidates must be self-authored train tasks")
        for repetition in range(4):
            item = _base_plan(row, "train-screen", "m1",
                f"train-screen/{task_id}/{repetition:02d}", "self")
            item.update(repetition_id=repetition, group_id=f"m1-close/{task_id}",
                        seed=screen["seed"] + task_index * 4 + repetition,
                        temperature=1.0, top_p=1.0)
            plans["train-screen"].append(item)

    dev = phases["dev-baseline"]
    _require(isinstance(dev, dict) and set(dev) == {"task_ids", "repetitions", "temperature",
             "top_p", "seed"}, "dev fields invalid")
    task_ids = dev["task_ids"]
    _require(isinstance(task_ids, list) and len(task_ids) == 20
             and len(task_ids) == len(set(task_ids)), "dev tasks invalid")
    _require(set(task_ids) == {row["id"] for row in rows.values() if row["split"] == "dev"},
             "dev baseline must cover the exact dev20")
    _require(dev["repetitions"] == 1 and dev["temperature"] == .6
             and dev["top_p"] == 1.0 and dev["seed"] == 940001, "dev sampling changed")
    for index, task_id in enumerate(task_ids):
        row = rows[task_id]
        profile = "self" if row["source"] == "self" else "swe"
        item = _base_plan(row, "dev-baseline", "m1", f"dev-baseline/{task_id}/00", profile)
        item.update(repetition_id=0, group_id=None, seed=dev["seed"] + index,
                    temperature=.6, top_p=1.0)
        plans["dev-baseline"].append(item)
    _require(sum(len(items) for items in plans.values()) == config["limits"]["max_total_creates"],
             "campaign create cap does not equal the exact schedule")
    return plans


def _read_ledger(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        raise ValueError("campaign ledger missing or unreadable") from None
    return value


def _fsync_parent(path):
    descriptor = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _persist_ledger(path, value):
    atomic_json(path, value)
    _fsync_parent(path)


def create_ledger(path, phase, identity, plan):
    path = Path(path)
    _require(phase in _PHASES and isinstance(plan, list) and plan, "ledger plan invalid")
    _require(set(identity) == {"source_git_sha", "config_sha256"}
             and re.fullmatch(r"[0-9a-f]{40}", identity["source_git_sha"])
             and _SHA256.fullmatch(identity["config_sha256"]), "ledger identity invalid")
    value = {"schema_version": 1, "campaign": "m1-close-v2", "phase": phase,
             "identity": identity, "state": "active", "plan": plan, "attempts": [],
             "provider_create_authorizations": 0, "max_provider_creates": len(plan)}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("campaign ledger already exists; retries require a new identity") from None
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_parent(path)
    return value


def admit_attempt(path, identity, item):
    path = Path(path)
    ledger = _read_ledger(path)
    index = len(ledger.get("attempts", []))
    _require(ledger.get("identity") == identity and ledger.get("state") == "active"
             and index < ledger.get("max_provider_creates", -1)
             and ledger["plan"][index] == item
             and all(row.get("state") == "finished" for row in ledger["attempts"]),
             "attempt is not the next exactly-once provider action")
    record = {"index": index, "attempt_id": item["attempt_id"], "task_id": item["task_id"],
              "state": "admitted"}
    ledger["attempts"].append(record)
    ledger["provider_create_authorizations"] += 1
    _persist_ledger(path, ledger)
    return record


def record_attempt_event(path, identity, index, event):
    path = Path(path)
    ledger = _read_ledger(path)
    _require(ledger.get("identity") == identity and ledger.get("state") == "active"
             and type(index) is int and index == len(ledger.get("attempts", [])) - 1
             and ledger["attempts"][index].get("state") == "admitted"
             and isinstance(event, dict) and isinstance(event.get("status"), str),
             "provider event identity mismatch")
    events = list(ledger["attempts"][index].get("events", []))
    events.append(event)
    ledger["attempts"][index]["events"] = events
    _persist_ledger(path, ledger)
    return event


def finish_attempt(path, identity, index, result):
    path = Path(path)
    ledger = _read_ledger(path)
    _require(ledger.get("identity") == identity and ledger.get("state") == "active"
             and type(index) is int and index == len(ledger.get("attempts", [])) - 1
             and ledger["attempts"][index].get("state") == "admitted",
             "attempt finalization identity mismatch")
    _require(isinstance(result, dict) and result.get("state") == "finished"
             and result.get("classification") in {"valid_reward", "invalid"}
             and result.get("create_uncertain") is False
             and result.get("cleanup_confirmed_empty") is True,
             "attempt cannot be finalized without a certain terminal cleanup")
    ledger["attempts"][index].update(result)
    _persist_ledger(path, ledger)
    return ledger["attempts"][index]


def finish_ledger(path, identity, summary_sha256):
    path = Path(path)
    ledger = _read_ledger(path)
    _require(ledger.get("identity") == identity and ledger.get("state") == "active"
             and len(ledger.get("attempts", [])) == len(ledger.get("plan", []))
             and all(row.get("state") == "finished" for row in ledger["attempts"])
             and isinstance(summary_sha256, str) and _SHA256.fullmatch(summary_sha256),
             "campaign ledger cannot be finalized")
    ledger["state"] = "finished"
    ledger["summary_sha256"] = summary_sha256
    _persist_ledger(path, ledger)
    return ledger
