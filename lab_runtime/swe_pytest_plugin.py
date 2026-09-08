"""Injected pytest observer; copy as a uniquely named module, no installation.

Only pytest and Python >=3.8 standard library are needed in the source image.
This records execution, not a security boundary against hostile test processes.
"""
import json
import os
import sys
from pathlib import Path

import pytest


_state = None
_phases = {}


def _save():
    path = Path(os.environ["MOPD_SWE_ARTIFACT"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(_state, sort_keys=True), encoding="utf-8")
    os.replace(str(temporary), str(path))


def pytest_sessionstart(session):
    global _state, _phases
    _phases = {}
    _state = dict(schema_version=1, nonce=os.environ["MOPD_SWE_NONCE"],
                  session_started=True, session_finished=False,
                  python_executable=sys.executable, python_version=sys.version,
                  pytest_version=pytest.__version__, cwd=os.getcwd(),
                  collected_node_ids=None, observations=[], errors=[], exitstatus=None)
    artifact = Path(os.environ["MOPD_SWE_ARTIFACT"])
    try:
        with artifact.with_suffix(".started").open("x") as claim:
            claim.write(str(os.getpid()))
    except FileExistsError:
        artifact.with_suffix(".duplicate").touch()
        _state["errors"].append("multiple_sessions")
    _save()


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session):
    _state["collected_node_ids"] = [item.nodeid for item in session.items]
    if len(set(_state["collected_node_ids"])) != len(session.items):
        _state["errors"].append("duplicate_collection_node")
    _save()


def pytest_collectreport(report):
    if report.failed:
        _state["errors"].append("collection_error:" + report.nodeid)


def pytest_runtest_logreport(report):
    phases = _phases.setdefault(report.nodeid, {})
    if report.when in phases:
        _state["errors"].append("duplicate_phase:" + report.nodeid + ":" + report.when)
    xpass = (hasattr(report, "wasxfail") and report.passed) or (
        report.failed and str(report.longrepr).startswith("[XPASS(strict)]"))
    phases[report.when] = (report.outcome, hasattr(report, "wasxfail"), xpass)


def pytest_runtest_logfinish(nodeid, location):
    phases = _phases.pop(nodeid, {})
    if "setup" not in phases or "teardown" not in phases or (
            phases["setup"][0] == "passed" and "call" not in phases):
        _state["errors"].append("missing_phase:" + nodeid)
        return
    if any(value[2] for value in phases.values()):
        status = "XPASS"  # Deliberately unknown to grader: invalid, not reward zero.
    elif any(phases[phase][0] == "failed" for phase in ("setup", "teardown")):
        status = "ERROR"
    elif phases.get("call", (None,))[0] == "failed":
        status = "FAILED"
    elif any(value[0] == "skipped" and value[1] for value in phases.values()):
        status = "XFAIL"
    elif any(value[0] == "skipped" for value in phases.values()):
        status = "SKIPPED"
    elif phases.get("call", (None,))[0] == "passed":
        status = "PASSED"
    else:
        status = "UNKNOWN"
    _state["observations"].append([nodeid, status])


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    _state["session_finished"] = True
    _state["exitstatus"] = int(exitstatus)
    if _phases:
        _state["errors"].append("unfinished_nodes")
    _save()
