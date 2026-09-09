"""One-shot Modal wrapper for the locked Qwen3-4B capacity worker."""

import json
from pathlib import Path

import modal


SKYRL_COMMIT = "eddb418dd4c560db9d43ffde561f1c5e669c8990"
MODEL_ID = "Qwen/Qwen3-4B"
MODEL_REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"
app = modal.App("sandbox-mopd-lab-m2-capacity-canary")
volume = modal.Volume.from_name("sandbox-mopd-lab-m2", create_if_missing=False, version=2)
image = (
    modal.Image.from_registry("novaskyai/skyrl-train-ray-2.51.1-py3.12-cu12.8")
    .apt_install("git")
    .run_commands(
        "git clone https://github.com/NovaSky-AI/SkyRL.git /root/SkyRL",
        f"cd /root/SkyRL && git checkout --detach {SKYRL_COMMIT}",
        "cd /root/SkyRL && uv sync --frozen --extra fsdp --extra harbor --no-dev",
    )
    .add_local_file(str(Path(__file__).with_name("m2_modal_capacity_worker.py")),
                    "/root/m2_modal_capacity_worker.py", copy=True)
    .env({"HF_HOME": "/vol/cache/huggingface", "XDG_CACHE_HOME": "/vol/cache"})
)


@app.function(image=image, gpu="L40S", cpu=8, memory=65536, max_containers=1,
              retries=0, timeout=1800, startup_timeout=600, volumes={"/vol": volume})
def capacity_canary() -> str:
    import os
    import subprocess

    root = Path("/vol/canaries/modal-capacity-v1")
    root.mkdir(parents=True, exist_ok=True)
    authorization = root / "authorization.json"
    with authorization.open("x", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "max_runs": 1, "gpu_type": "L40S",
                   "timeout_seconds": 1800, "model_id": MODEL_ID,
                   "model_revision": MODEL_REVISION, "skyrl_commit": SKYRL_COMMIT},
                  handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    volume.commit()

    completed = subprocess.run(
        ["/root/SkyRL/.venv/bin/python", "/root/m2_modal_capacity_worker.py"],
        text=True, capture_output=True, timeout=1680, check=False)
    if completed.returncode != 0:
        receipt = {"schema_version": 1, "phase": "failed", "provider": "modal",
                   "error_type": "WorkerProcessError", "returncode": completed.returncode}
        result = root / "result.json"
        if not result.exists():
            with result.open("x", encoding="utf-8") as handle:
                json.dump(receipt, handle, sort_keys=True)
                handle.write("\n")
        volume.commit()
        return json.dumps(receipt, sort_keys=True)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("capacity worker returned no receipt")
    receipt = json.loads(lines[-1])
    volume.commit()
    return json.dumps(receipt, sort_keys=True)


@app.local_entrypoint()
def main():
    receipt = json.loads(capacity_canary.remote())
    print(json.dumps(receipt, sort_keys=True))
    if receipt["phase"] != "complete":
        raise RuntimeError(f"Modal capacity canary ended in {receipt['phase']}")
