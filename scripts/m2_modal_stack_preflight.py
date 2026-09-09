"""CPU-only build and inspection of the frozen SkyRL fsdp+harbor environment."""

import json
import subprocess

import modal


SKYRL_COMMIT = "eddb418dd4c560db9d43ffde561f1c5e669c8990"
app = modal.App("sandbox-mopd-lab-m2-stack-preflight-v3")
image = (
    modal.Image.from_registry("novaskyai/skyrl-train-ray-2.51.1-py3.12-cu12.8")
    .apt_install("git")
    .run_commands(
        "git clone https://github.com/NovaSky-AI/SkyRL.git /root/SkyRL",
        f"cd /root/SkyRL && git checkout --detach {SKYRL_COMMIT}",
        "cd /root/SkyRL && uv sync --frozen --extra fsdp --extra harbor --no-dev",
    )
)


@app.function(image=image, cpu=2, memory=8192, max_containers=1,
              retries=0, timeout=300, startup_timeout=300)
def inspect_stack() -> str:
    code = """
import json, platform
from importlib.metadata import version
names = ('torch','vllm','transformers','peft','ray','modal','harbor')
print(json.dumps({'python': platform.python_version(),
                  'packages': {name: version(name) for name in names}}, sort_keys=True))
"""
    completed = subprocess.run(["/root/SkyRL/.venv/bin/python", "-c", code],
                               text=True, capture_output=True, timeout=120, check=True)
    observed = json.loads(completed.stdout.strip())
    expected = {"python": "3.12.3", "packages": {
        "torch": "2.10.0+cu128", "vllm": "0.19.0", "transformers": "5.3.0",
        "peft": "0.18.1", "ray": "2.51.1", "modal": "1.5.5", "harbor": "0.4.0"}}
    receipt = {"schema_version": 1, "phase": "complete" if observed == expected else "mismatch",
               "expected": expected, "observed": observed}
    return json.dumps(receipt, sort_keys=True)


@app.local_entrypoint()
def main():
    receipt = json.loads(inspect_stack.remote())
    print(json.dumps(receipt, sort_keys=True))
    if receipt["phase"] != "complete":
        raise RuntimeError("frozen SkyRL environment does not match the registered lock")
