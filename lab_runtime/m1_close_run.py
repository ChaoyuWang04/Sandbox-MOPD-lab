"""Bounded serial execution for one frozen M1-close phase on home-5090."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

from . import home5090_run as runtime
from .controls_v2 import attach_absence, cleanup as cleanup_sandboxes
from .home5090 import ROOT, SETTINGS, VENV, check_host, isolated_env
from .m1_close_v2 import (admit_attempt, create_ledger, finish_attempt, finish_ledger,
                          record_attempt_event, validate_and_plan)
from .m1_pilot_v2 import _trace_fields, creation_uncertain
from .m1_report import select_overfit16_v2, validate_tb_representative
from .m1_run import COST_RATES, provider_credentials, read_credentials, scrub_provider_errors
from .swe_hooks import attach_swe_hooks


SOURCE = Path(__file__).resolve().parents[1]
CONFIG_PATH = SOURCE / "configs/m1-close-v2.json"
MANIFEST_PATH = SOURCE / "configs/m1-pool-v2-r2.json"
TASK_ROOT = ROOT / "data/m1/v2/tasks-r2"
PHASE_SECONDS = {"tb-controls": 3900, "train-screen": 21600, "dev-baseline": 21600}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_task_tree(item, task_root=TASK_ROOT):
    task = Path(task_root) / Path(item["task_path"]).name
    if not task.is_dir() or task.is_symlink():
        raise ValueError("registered task directory is missing")
    files = {}
    for path in task.rglob("*"):
        if path.is_symlink():
            raise ValueError("task tree contains a symlink")
        if path.is_file():
            files[path.relative_to(task).as_posix()] = digest(path)
    if files != item["task_files_sha256"]:
        raise ValueError("task tree identity mismatch")
    return task


def make_trial_config(task, run, item, profile, labels):
    from harbor.models.trial.config import TrialConfig
    if item["agent"] == "m1":
        agent = {"import_path": "lab_runtime.m1_agent:M1Agent", "model_name": SETTINGS["model"],
                 "kwargs": {"temperature": item["temperature"], "seed": item["seed"]}}
    else:
        agent = {"name": item["agent"]}
    adapter = ("lab_runtime.daytona_pilot:PilotDaytonaEnvironment"
               if item["resource_profile"] == "self"
               else "lab_runtime.daytona_v2:ControlsDaytonaEnvironment")
    return TrialConfig(task={"path": task}, agent=agent, trials_dir=run / "trials",
        trial_name=item["attempt_id"].replace("/", "--"), environment={
            "type": "daytona", "import_path": adapter, "delete": True,
            "cpu_enforcement_policy": "request", "memory_enforcement_policy": "request",
            "override_cpus": profile["cpus"], "override_memory_mb": profile["memory_mb"],
            "override_storage_mb": profile["storage_mb"], "override_gpus": 0,
            "kwargs": {"auto_snapshot": False, "auto_labels": False, "labels": labels,
                       "network_block_all": not profile["network"],
                       "auto_stop_interval_mins": 1, "auto_delete_interval_mins": 0,
                       "expose_sandbox_id": True}})


def _classification(record):
    if record.get("create_uncertain"):
        return "invalid", "provider_create_uncertain"
    if record.get("cleanup_confirmed_empty") is not True:
        return "invalid", "sandbox_cleanup_uncertain"
    if record.get("exception_type") is not None:
        return "invalid", "verifier_runtime_error"
    if (record.get("private_files_absent_during_agent") is not True
            or record.get("verifier_complete") is not True):
        return "invalid", "verifier_protocol_incomplete"
    if type(record.get("reward")) not in (int, float) or record["reward"] not in (0, 1):
        return "invalid", "reward_missing"
    if record["agent"] == "m1" and (record.get("usage_complete") is not True
                                     or record.get("model_responses", 0) < 1):
        return "invalid", "model_trace_incomplete"
    return "valid_reward", None


def _classify_and_normalize(record):
    """Keep diagnostic reward observations out of the formal invalid outcome."""
    classification, reason = _classification(record)
    if classification == "invalid" and type(record.get("reward")) in (int, float):
        record["observed_reward"] = record["reward"]
        record["reward"] = None
    record.update(classification=classification, invalid_reason=reason)
    return classification, reason


def _estimate_cost(record, profile):
    creation = [event for event in record.get("events", [])
                if event.get("status") in ("creating", "created")]
    created = [event for event in creation if event.get("status") == "created"
               and event.get("sandbox_id")]
    starts = [event.get("time") for event in creation if event.get("status") == "creating"
              and type(event.get("time")) in (int, float)]
    known = (not record.get("create_uncertain")
             and record.get("cleanup_confirmed_empty") is True
             and len(created) == 1 and len(starts) == 1
             and type(record.get("finished")) in (int, float))
    rate = (profile["cpus"] * COST_RATES["cpu_per_vcpu_hour"]
            + (profile["memory_mb"] / 1024) * COST_RATES["ram_per_gib_hour"]
            + (profile["storage_mb"] / 1024) * COST_RATES["disk_per_gib_hour"]) / 3600
    seconds = max(0, record["finished"] - starts[0]) if known else None
    return {"estimated_usd": seconds * rate if known else None,
            "sandbox_seconds": seconds,
            "ttl_max_estimate_usd": profile["ttl_seconds"] * rate,
            "billing_verified": False}


def _verifier_complete(source, record, grading):
    base = (record.get("exception_type") is None
            and type(record.get("reward")) in (int, float) and record["reward"] in (0, 1))
    if source in ("swe-smith", "swe-gym"):
        return (base and isinstance(grading, dict)
                and grading.get("protocol_complete") is True
                and grading.get("reward") == record["reward"]
                and not grading.get("runtime_errors")
                and grading.get("owned_process_group_stopped") is True)
    if source == "self":
        return (base and isinstance(grading, dict) and set(grading) == {"passed"}
                and type(grading["passed"]) is bool
                and grading["passed"] == bool(record["reward"]))
    return base


def _source_hooks(trial, run, task, item):
    if item["source"] in ("swe-smith", "swe-gym"):
        private = json.loads((task / "tests/private.json").read_text())
        return attach_swe_hooks(trial, run / "private" / item["attempt_id"].replace("/", "--"),
            private["source"], private["profile"], private["gold_patch_paths"], item["agent"])
    return attach_absence(trial, item["agent"])


async def execute_phase(run, phase, config, manifest, plans, ledger_path, identity, deadline, secrets):
    from daytona import ListSandboxesQuery
    from harbor.environments.daytona.environment import DaytonaClientManager
    from harbor.trial.trial import Trial
    from . import daytona_pilot, daytona_v2

    manager = await DaytonaClientManager.get_instance()
    client = await manager.get_client()
    phase_labels = {"m1_close_campaign": config["campaign"], "m1_close_phase": phase}
    summary = {"schema_version": 1, "campaign": config["campaign"], "phase": phase,
               "source_git_sha": identity["source_git_sha"],
               "config_sha256": identity["config_sha256"],
               "manifest_sha256": config["manifest_sha256"], "records": [],
               "model_revision": SETTINGS["revision"], "final_model_results_exposed": False,
               "stopping_reason": "preflight"}
    runtime.write_json(run / "summary.json", summary)
    consecutive_invalid = 0
    try:
        existing = [sandbox.id async for sandbox in client.list(
            ListSandboxesQuery(labels=phase_labels), request_timeout=20)]
        if existing:
            raise RuntimeError("M1-close objects already exist; refusing ownership guess")
        for index, item in enumerate(plans):
            task = verify_task_tree(item)
            profile = config["resource_profiles"][item["resource_profile"]]
            labels = {**phase_labels, "m1_close_attempt": str(index)}
            admit_attempt(ledger_path, identity, item)
            record = {**item, "index": index, "state": "active", "events": [],
                      "exception_type": None, "reward": None, "create_uncertain": False,
                      "cleanup_confirmed_empty": False, "verifier_complete": False,
                      "private_files_absent_during_agent": False}

            def emit(event):
                record["events"].append(event)
                record_attempt_event(ledger_path, identity, index, event)

            adapter = daytona_pilot if item["resource_profile"] == "self" else daytona_v2
            adapter.EVENT_SINK = emit
            trial = None
            evidence = {}
            grading = None
            pending = None
            try:
                trial = await Trial.create(make_trial_config(task, run, item, profile, labels))
                evidence = _source_hooks(trial, run, task, item)
                async with asyncio.timeout(profile["trial_seconds"]):
                    result = await trial.run()
                record["exception_type"] = getattr(
                    getattr(result, "exception_info", None), "exception_type", None)
                rewards = getattr(getattr(result, "verifier_result", None), "rewards", None)
                record["reward"] = rewards.get("reward") if isinstance(rewards, dict) else None
            except BaseException as exc:
                record["exception_type"] = type(exc).__name__
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    pending = exc
            finally:
                adapter.EVENT_SINK = None
                creation = [event for event in record["events"] if event.get("status") in
                            ("creating", "created", "rejected", "uncertain")]
                created = {event["sandbox_id"] for event in creation if event.get("sandbox_id")}
                record["create_uncertain"] = creation_uncertain(creation)
                cleanup_events = []
                try:
                    await cleanup_sandboxes(client, labels, created, cleanup_events.append,
                        timeout_seconds=min(120, max(.01, deadline - time.monotonic() - 30)))
                    record["cleanup_confirmed_empty"] = True
                except BaseException as exc:
                    record["cleanup_error_type"] = type(exc).__name__
                    if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                        pending = pending or exc
                record["cleanup_events"] = cleanup_events
                record["private_files_absent_during_agent"] = evidence.get("phase") == "verification_ready"
                trial_name = item["attempt_id"].replace("/", "--")
                verifier_dir = run / "trials" / trial_name / "verifier"
                if item["source"] in ("swe-smith", "swe-gym") and (verifier_dir / "run.json").is_file():
                    grading = json.loads((verifier_dir / "run.json").read_text())
                    record["grading"] = grading
                elif item["source"] == "self" and (verifier_dir / "result.json").is_file():
                    grading = json.loads((verifier_dir / "result.json").read_text())
                    record["grading"] = grading
                record["verifier_complete"] = _verifier_complete(
                    item["source"], record, grading)
                if item["agent"] == "m1":
                    record.update(_trace_fields(run, item["attempt_id"].replace("/", "--")))
                _classify_and_normalize(record)
                record.update(state="finished", finished=time.time())
                record["cleanup_empty"] = record["cleanup_confirmed_empty"]
                record["cost"] = _estimate_cost(record, profile)
                summary["records"].append(record)
                runtime.write_json(run / "summary.json", summary)
                scrub_provider_errors(run, secrets)
            if pending is not None:
                raise pending
            if record["create_uncertain"] or not record["cleanup_confirmed_empty"]:
                summary["stopping_reason"] = "creation_or_cleanup_uncertain"
                runtime.write_json(run / "summary.json", summary)
                break
            finish_attempt(ledger_path, identity, index, {key: record[key] for key in
                ("state", "classification", "reward", "create_uncertain",
                 "cleanup_confirmed_empty", "invalid_reason")})
            consecutive_invalid = (consecutive_invalid + 1
                                   if record["classification"] == "invalid" else 0)
            if consecutive_invalid >= config["limits"]["max_consecutive_invalid"]:
                summary["stopping_reason"] = "three_consecutive_invalid_attempts"
                runtime.write_json(run / "summary.json", summary)
                break
        else:
            summary["stopping_reason"] = "planned_attempts_terminal"
        if phase == "train-screen" and len(summary["records"]) == len(plans):
            selection = select_overfit16_v2(manifest, summary["records"], {
                "schema_version": 1,
                "candidate_task_ids": config["phases"][phase]["candidate_task_ids"],
                "group_size": 4, "temperature": 1.0, "top_p": 1.0, "seed": 930001,
                "select_per_skill": {"A": 8, "B": 8}})
            summary["selection"] = selection
        if phase == "tb-controls" and len(summary["records"]) == 2:
            summary["representative"] = validate_tb_representative(
                manifest, config["phases"][phase]["task_id"], summary["records"])
        summary["phase_terminal"] = (summary["stopping_reason"] == "planned_attempts_terminal"
            and len(summary["records"]) == len(plans)
            and all(row["classification"] in {"valid_reward", "invalid"}
                    and row["cleanup_confirmed_empty"] for row in summary["records"]))
        if phase == "tb-controls":
            summary["phase_accepted"] = summary["phase_terminal"] and "representative" in summary
        elif phase == "train-screen":
            summary["phase_accepted"] = (summary["phase_terminal"]
                and summary.get("selection", {}).get("selection_ready") is True)
        else:
            summary["phase_accepted"] = (summary["phase_terminal"]
                and all(row["classification"] == "valid_reward" for row in summary["records"]))
        runtime.write_json(run / "summary.json", summary)
        if summary["phase_terminal"]:
            finish_ledger(ledger_path, identity, digest(run / "summary.json"))
        return summary
    finally:
        daytona_pilot.EVENT_SINK = None
        daytona_v2.EVENT_SINK = None
        try:
            await client.close()
        finally:
            manager._client = None


def main(phase):
    check_host(sys.argv, platform.system(), platform.machine())
    if phase not in PHASE_SECONDS:
        raise ValueError("unknown fixed M1-close phase")
    with runtime.run_context("m1-close-" + phase, PHASE_SECONDS[phase], artifact_stage="m1") as (
            run, state, deadline, log):
        config = json.loads(CONFIG_PATH.read_text())
        manifest = json.loads(MANIFEST_PATH.read_text())
        plans = validate_and_plan(config, manifest, digest(MANIFEST_PATH))[phase]
        source_git_sha = runtime.source_identity(deadline, log)
        identity = {"source_git_sha": source_git_sha, "config_sha256": digest(CONFIG_PATH)}
        ledger_path = ROOT / "artifacts/m1/v2/close" / phase / "campaign.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        create_ledger(ledger_path, phase, identity, plans)
        credentials = read_credentials(ROOT / "secrets/daytona.env")
        with provider_credentials(credentials):
            if phase == "tb-controls":
                summary = asyncio.run(execute_phase(run, phase, config, manifest, plans,
                                                     ledger_path, identity, deadline, credentials))
            else:
                with runtime.managed_server(run, state, deadline, log,
                                            allow_previous_source=True):
                    summary = asyncio.run(execute_phase(run, phase, config, manifest, plans,
                                                         ledger_path, identity, deadline, credentials))
        state["summary_path"] = str(run / "summary.json")
        state["batch_completed"] = summary["phase_accepted"]


def bootstrap(phase):
    check_host(sys.argv, platform.system(), platform.machine())
    env = isolated_env(os.environ)
    executable = VENV / "bin/python"
    if not executable.is_file():
        raise ValueError("prepared M1 Python environment is missing")
    if Path(sys.prefix) != VENV:
        env["M1_CLOSE_PHASE"] = phase
        script = SOURCE / {"tb-controls": "scripts/m1_close_tb.py",
                           "train-screen": "scripts/m1_close_screen.py",
                           "dev-baseline": "scripts/m1_close_dev.py"}[phase]
        os.execve(str(executable), [str(executable), "-B", str(script)], env)
        raise RuntimeError("execve unexpectedly returned")
    os.environ.clear()
    os.environ.update(env)
    main(phase)
