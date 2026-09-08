"""Bounded trusted-command launcher and full-node SWE grading (stdlib only).

Call with a frozen source profile's original shell command, including source
activation, and grading config: source/parser_identity/fail_to_pass/pass_to_pass.
Run this under the controller Python; pytest uses the source command's Python.
Copy this module, swe_grading.py and swe_pytest_plugin.py together into /tests
for image injection. Output must be verifier-owned and outside the source tree.
This launcher does not restore tests, apply patches, or enforce isolation.
"""
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

from .swe_grading import grade_swe_tests


def _group_running(pgid):
    """Read only our group; zombies are exited processes awaiting their parent."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    if Path("/proc/self/stat").exists():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except FileNotFoundError:
                continue
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                return True
        return False
    # macOS development fallback; Linux source images need no ps installation.
    rows = subprocess.run(["/bin/ps", "-axo", "pgid=,stat="], capture_output=True,
                          text=True, check=True, timeout=0.5).stdout.splitlines()
    return any(int(parts[0]) == pgid and not parts[1].startswith(("Z", "X"))
               for parts in (row.split() for row in rows) if len(parts) == 2)


def _stop_owned_group(process):
    """Bound cleanup of this new session only, including background children.

    Children deliberately escaping via setsid are outside this launcher's
    scope; provider/container teardown must enforce that separate boundary.
    """
    try:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)
        deadline = time.monotonic() + 1
        while _group_running(process.pid):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


def run_tests(command, config, output_dir, *, cwd, timeout):
    """Run one trusted bash command and return diagnostics with nullable reward.

    ``run.json`` always records runtime/grading diagnostics; ``grade.json`` and
    ``reward.txt`` exist only for valid grading. Never accept agent shell input.
    Concurrent writers must use distinct output directories. POSIX is required.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a nonempty trusted shell command")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("reward.txt", "grade.json"):
        (output / filename).unlink(missing_ok=True)
    nonce = uuid.uuid4().hex
    artifact = output / ("pytest-" + nonce + ".json")
    returncode = None
    owned_group_stopped = None
    runtime_errors = []
    with tempfile.TemporaryDirectory(prefix="swe-plugin-", dir=str(output)) as injected:
        module = "_mopd_swe_pytest_" + nonce
        shutil.copyfile(Path(__file__).with_name("swe_pytest_plugin.py"),
                        Path(injected) / (module + ".py"))
        env = os.environ.copy()
        env["PYTHONPATH"] = injected + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTEST_PLUGINS"] = ",".join(filter(None, [env.get("PYTEST_PLUGINS"), module]))
        env["MOPD_SWE_ARTIFACT"] = str(artifact)
        env["MOPD_SWE_NONCE"] = nonce
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with (output / "pytest.log").open("wb") as log:
            process = None
            try:
                process = subprocess.Popen(["/bin/bash", "-c", command], cwd=cwd,
                                           env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    runtime_errors.append("timeout")
            except OSError as exc:
                runtime_errors.append("launch_error:" + type(exc).__name__)
            finally:
                if process is not None:
                    owned_group_stopped = _stop_owned_group(process)
                    returncode = process.returncode
                    if not owned_group_stopped:
                        runtime_errors.append("owned_process_group_cleanup_uncertain")
    evidence = {}
    try:
        evidence = json.loads(artifact.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            evidence = {}
            runtime_errors.append("malformed_artifact")
    except (OSError, ValueError):
        runtime_errors.append("missing_or_malformed_artifact")
    if artifact.with_suffix(".duplicate").exists():
        runtime_errors.append("multiple_sessions")
    complete = (not runtime_errors and evidence.get("schema_version") == 1
                and evidence.get("nonce") == nonce
                and evidence.get("session_started") is True
                and evidence.get("session_finished") is True
                and returncode in (0, 1) and evidence.get("exitstatus") == returncode
                and not evidence.get("errors")
                and isinstance(evidence.get("collected_node_ids"), list)
                and isinstance(evidence.get("observations"), list))
    try:
        result = grade_swe_tests(
            source=config["source"], parser_identity=config["parser_identity"],
            fail_to_pass=config["fail_to_pass"], pass_to_pass=config["pass_to_pass"],
            observations=evidence.get("observations", []) if complete else [],
            collected_node_ids=evidence.get("collected_node_ids") if complete else None,
            protocol_complete=complete)
    except (ValueError, TypeError, KeyError) as exc:
        runtime_errors.append("grading_error:" + type(exc).__name__)
        result = {"outcome": "invalid", "reward": None, "protocol_complete": False}
    result.update(command=command, returncode=returncode, nonce=nonce,
                  owned_process_group_stopped=owned_group_stopped,
                  artifact=str(artifact), runtime_errors=runtime_errors,
                  protocol_errors=evidence.get("errors", []))
    (output / "run.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    if result["reward"] is not None:
        (output / "grade.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        (output / "reward.txt").write_text(str(result["reward"]) + "\n")
    return result
