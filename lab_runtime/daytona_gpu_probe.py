"""One-shot Daytona GPU capability probe; no model download or training."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import sys
import time
import uuid

from daytona import (CreateSandboxFromImageParams, Daytona, DaytonaNotFoundError,
                     GpuType, ListSandboxesQuery, Resources, VolumeMount)

from .home5090 import ROOT
from .m1_run import provider_credentials, read_credentials


CAMPAIGN = "m2-gpu-probe-v1"
VOLUME_NAME = "sandbox-mopd-m2-v1"
MOUNT_PATH = "/home/daytona/lab"
IMAGE = "pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime"
ARTIFACT_ROOT = ROOT / "artifacts/m2/daytona-gpu-probe-v1"
SECRET_PATH = ROOT / "secrets/daytona.env"
_OUTPUT_KEYS = {"torch", "cuda_runtime", "cuda_available", "device_count", "device_name",
                "volume_writable", "volume_bytes_free", "memory_bytes"}


def build_params(volume_id: str, run_id: str) -> CreateSandboxFromImageParams:
    return CreateSandboxFromImageParams(
        name=f"sandbox-mopd-m2-probe-{run_id[-12:]}",
        image=IMAGE,
        labels={"project": "sandbox-rl-mopd", "campaign": CAMPAIGN, "run_id": run_id},
        public=False,
        ephemeral=True,
        spot=False,
        ttl_minutes=10,
        auto_delete_interval=0,
        volumes=[VolumeMount(volume_id=volume_id, mount_path=MOUNT_PATH)],
        resources=Resources(gpu=1, gpu_type=[GpuType.RTX_5090, GpuType.RTX_4090]),
    )


def authorize_once(root: Path, run_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "authorization.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "campaign": CAMPAIGN, "run_id": run_id,
                       "max_creations": 1, "gpu_types": ["RTX-5090", "RTX-4090"],
                       "ttl_minutes": 10},
                      handle, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise FileExistsError("Daytona GPU probe already authorized; automatic retry is forbidden") from exc
    return path


def validate_output(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != _OUTPUT_KEYS:
        raise ValueError("GPU probe output has unknown or missing fields")
    if value["cuda_available"] is not True or value["device_count"] != 1:
        raise ValueError("Daytona sandbox did not expose exactly one usable CUDA GPU")
    if (not isinstance(value["device_name"], str)
            or not any(gpu in value["device_name"] for gpu in ("5090", "4090"))):
        raise ValueError("Daytona sandbox did not receive a registered RTX 5090/4090")
    if value["volume_writable"] is not True:
        raise ValueError("Daytona volume is not writable")
    for key in ("volume_bytes_free", "memory_bytes"):
        if type(value[key]) is not int or value[key] <= 0:
            raise ValueError(f"invalid {key}")
    for key in ("torch", "cuda_runtime"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"invalid {key}")
    return value


def _probe_command() -> str:
    code = (
        "import json,os,torch; p='/home/daytona/lab/probe-v1'; "
        "open(p,'w').write('ok'); s=os.statvfs('/home/daytona/lab'); "
        "m=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES'); "
        "print(json.dumps({'torch':torch.__version__,'cuda_runtime':torch.version.cuda or '',"
        "'cuda_available':torch.cuda.is_available(),'device_count':torch.cuda.device_count(),"
        "'device_name':torch.cuda.get_device_name(0) if torch.cuda.is_available() else '',"
        "'volume_writable':open(p).read()=='ok','volume_bytes_free':s.f_bavail*s.f_frsize,"
        "'memory_bytes':m},sort_keys=True))"
    )
    return f"python -c \"{code}\""


def execute_probe(client, run_id: str) -> dict:
    receipt = {"schema_version": 1, "campaign": CAMPAIGN, "run_id": run_id,
               "phase": "starting", "sandbox_id": None, "volume_id": None,
               "cleanup_confirmed": False}
    try:
        volume = client.volume.get(VOLUME_NAME, create=True)
        deadline = time.monotonic() + 120
        while getattr(volume, "state", None) != "ready":
            if time.monotonic() >= deadline:
                raise TimeoutError("Daytona volume readiness timeout")
            time.sleep(1)
            volume = client.volume.get(VOLUME_NAME)
        receipt["volume_id"] = volume.id
        try:
            sandbox = client.create(build_params(volume.id, run_id), timeout=180)
        except Exception as exc:
            receipt.update(phase="provider_rejected", error_type=type(exc).__name__)
            return receipt
        receipt["sandbox_id"] = sandbox.id
        try:
            result = sandbox.process.exec(_probe_command(), timeout=120)
            if result.exit_code != 0:
                raise RuntimeError("GPU probe command returned nonzero")
            receipt["runtime"] = validate_output(result.result.strip())
            receipt["phase"] = "runtime_validated"
        except Exception as exc:
            receipt.update(phase="runtime_invalid", error_type=type(exc).__name__)
        try:
            client.delete(sandbox, timeout=60, wait=True)
            try:
                client.get(sandbox.id, request_timeout=20)
            except DaytonaNotFoundError:
                remaining = list(client.list(ListSandboxesQuery(id=sandbox.id), request_timeout=20))
                receipt["cleanup_confirmed"] = not remaining
            else:
                receipt["cleanup_confirmed"] = False
        except Exception as exc:
            receipt.update(phase="cleanup_uncertain", cleanup_error_type=type(exc).__name__)
            return receipt
        if not receipt["cleanup_confirmed"]:
            receipt["phase"] = "cleanup_uncertain"
        elif receipt["phase"] == "runtime_validated":
            receipt["phase"] = "complete"
        return receipt
    except Exception as exc:
        receipt.update(phase="preflight_failed", error_type=type(exc).__name__)
        return receipt


def cleanup_rejected_create(client, run_id: str) -> dict:
    """Resolve an uncertain rejected create without ever issuing another create."""
    labels = {"project": "sandbox-rl-mopd", "campaign": CAMPAIGN, "run_id": run_id}
    receipt = {"schema_version": 1, "campaign": CAMPAIGN, "run_id": run_id,
               "phase": "cleanup_uncertain", "cleanup_confirmed": False,
               "matched_sandbox_ids": []}
    try:
        matches = list(client.list(ListSandboxesQuery(labels=labels), request_timeout=20))
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        return receipt
    if len(matches) > 1:
        receipt["reason"] = "multiple_label_matches"
        return receipt
    if matches:
        sandbox = matches[0]
        if getattr(sandbox, "labels", None) != labels:
            receipt["reason"] = "label_mismatch"
            return receipt
        receipt["matched_sandbox_ids"] = [sandbox.id]
        try:
            client.delete(sandbox, timeout=60, wait=True)
        except Exception as exc:
            receipt["error_type"] = type(exc).__name__
            return receipt
    try:
        remaining = list(client.list(ListSandboxesQuery(labels=labels), request_timeout=20))
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        return receipt
    if remaining:
        receipt["reason"] = "owned_sandbox_still_present"
        return receipt
    receipt.update(phase="cleanup_confirmed_no_retry", cleanup_confirmed=True)
    return receipt


def _load_rejection_for_cleanup(root: Path) -> str | None:
    authorization_path = root / "authorization.json"
    result_path = root / "result.json"
    if not authorization_path.exists() and not result_path.exists():
        return None
    if not authorization_path.is_file() or not result_path.is_file():
        raise RuntimeError("incomplete Daytona GPU probe ledger")
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    run_id = authorization.get("run_id")
    if (authorization.get("campaign") != CAMPAIGN or not isinstance(run_id, str)
            or result.get("campaign") != CAMPAIGN or result.get("run_id") != run_id
            or result.get("phase") != "provider_rejected"
            or result.get("cleanup_confirmed") is not False):
        raise RuntimeError("existing Daytona GPU probe is not an eligible rejected-create ledger")
    return run_id


def main() -> None:
    if len(sys.argv) != 1 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("fixed Daytona GPU probe must run on home-5090 through hlab")
    rejected_run_id = _load_rejection_for_cleanup(ARTIFACT_ROOT)
    credentials = read_credentials(SECRET_PATH)
    if rejected_run_id is not None:
        with provider_credentials(credentials):
            receipt = cleanup_rejected_create(Daytona(), rejected_run_id)
        cleanup_path = ARTIFACT_ROOT / "cleanup-after-rejection.json"
        with cleanup_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
        print(json.dumps(receipt, sort_keys=True))
        if receipt["phase"] != "cleanup_confirmed_no_retry":
            raise RuntimeError(f"Daytona rejected-create cleanup ended in {receipt['phase']}")
        return
    run_id = f"probe-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    authorize_once(ARTIFACT_ROOT, run_id)
    with provider_credentials(credentials):
        receipt = execute_probe(Daytona(), run_id)
    result_path = ARTIFACT_ROOT / "result.json"
    with result_path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.write("\n")
    print(json.dumps(receipt, sort_keys=True))
    if receipt["phase"] != "complete":
        raise RuntimeError(f"Daytona GPU probe ended in {receipt['phase']}")


if __name__ == "__main__":
    main()
