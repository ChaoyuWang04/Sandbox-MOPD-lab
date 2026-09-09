"""CPU-only inspection of the official SkyRL Modal image package identity."""

import json

import modal


app = modal.App("sandbox-mopd-lab-m2-stack-preflight")
image = modal.Image.from_registry("novaskyai/skyrl-train-ray-2.51.1-py3.12-cu12.8")


@app.function(image=image, cpu=2, memory=8192, max_containers=1,
              retries=0, timeout=300, startup_timeout=300)
def inspect_stack() -> str:
    from importlib.metadata import PackageNotFoundError, version
    import platform

    names = ("torch", "vllm", "transformers", "peft", "ray", "modal", "harbor")
    observed = {}
    for name in names:
        try:
            observed[name] = version(name)
        except PackageNotFoundError:
            observed[name] = None
    expected = {"torch": "2.10.0", "vllm": "0.19.0", "transformers": "5.3.0",
                "peft": "0.18.1", "ray": "2.51.1", "modal": "1.5.5",
                "harbor": "0.4.0"}
    receipt = {"schema_version": 1, "phase": "complete" if observed == expected else "mismatch",
               "python": platform.python_version(), "expected": expected, "observed": observed}
    return json.dumps(receipt, sort_keys=True)


@app.local_entrypoint()
def main():
    receipt = json.loads(inspect_stack.remote())
    print(json.dumps(receipt, sort_keys=True))
    if receipt["phase"] != "complete":
        raise RuntimeError("official SkyRL image requires an explicit M2 dependency layer")
