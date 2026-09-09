"""One-shot Modal L40S capability probe; no model download or training."""

import json
from pathlib import Path

import modal


app = modal.App("sandbox-mopd-lab-m2-gpu-probe")
volume = modal.Volume.from_name("sandbox-mopd-lab-m2", create_if_missing=True, version=2)
image = modal.Image.from_registry("pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime")


@app.function(image=image, gpu="L40S", cpu=2, memory=8192,
              max_containers=1, retries=0, timeout=300, startup_timeout=300,
              volumes={"/vol": volume})
def gpu_probe():
    import os
    import platform
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("Modal probe requires exactly one usable CUDA GPU")
    device_name = torch.cuda.get_device_name(0)
    if "L40S" not in device_name:
        raise RuntimeError("Modal probe did not receive the registered L40S")
    receipt = {
        "schema_version": 1,
        "phase": "complete",
        "provider": "modal",
        "gpu_type": "L40S",
        "device_name": device_name,
        "device_count": torch.cuda.device_count(),
        "gpu_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
        "torch": str(torch.__version__),
        "cuda_runtime": str(torch.version.cuda),
        "python": platform.python_version(),
        "volume_writable": True,
    }
    target = Path("/vol/probes/modal-gpu-v1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    volume.commit()
    return receipt


@app.local_entrypoint()
def main():
    print(json.dumps(gpu_probe.remote(), sort_keys=True))
