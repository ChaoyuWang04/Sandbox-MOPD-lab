"""Fixed M0 serving contract; never imports or initializes CUDA on import."""

import json
import hashlib
from pathlib import Path

ROOT = Path("/home/samwang/data/sandbox-rl-MOPD-lab")
UV = Path("/home/samwang/.local/bin/uv")
LOCKFILE = Path(__file__).resolve().parents[1] / "environments/home5090/uv.lock"
LOCK_ID = hashlib.sha256(LOCKFILE.read_bytes()).hexdigest()[:12]
VENV = ROOT / f"envs/m0-serving-{LOCK_ID}"
SETTINGS = {
    "model": "Qwen/Qwen3-4B",
    "revision": "1cfa9a7208912126459214e8b04321603b3df60c",
    "max_model_len": 16384,
    "output_tokens": 4096,
    "gpu_memory_utilization": 0.70,
    "port": 18741,
}
MODEL = ROOT / "models/Qwen3-4B" / SETTINGS["revision"]
PREPARE_SECONDS = 7200
G4_SECONDS = 1200
PACKAGE_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
DOWNLOAD_ENV = {
    "UV_HTTP_TIMEOUT": "120",
    "UV_CONCURRENT_DOWNLOADS": "4",
    "HF_ENDPOINT": "https://huggingface.co",
    "HF_HUB_DOWNLOAD_TIMEOUT": "120",
    "HF_HUB_ETAG_TIMEOUT": "30",
    # Use the HTTP transfer path measured by the network probe, not untested Xet fan-out.
    "HF_HUB_DISABLE_XET": "1",
}


def qualifies_cold_prepare(cold_start, elapsed):
    return bool(cold_start) and 0 < elapsed <= G4_SECONDS


def model_download_argv():
    return [str(VENV / "bin/hf"), "download", SETTINGS["model"],
            "--revision", SETTINGS["revision"], "--local-dir", str(MODEL),
            "--max-workers", "2"]


def validate_completion(result, elapsed):
    if result.get("usage", {}).get("prompt_tokens") != SETTINGS["max_model_len"] - SETTINGS["output_tokens"]:
        raise ValueError("Did not exercise exactly 12288 input tokens")
    if result.get("usage", {}).get("completion_tokens") != SETTINGS["output_tokens"]:
        raise ValueError("Did not generate exactly 4096 output tokens")
    if not 0 < elapsed < 90:
        raise ValueError("Generation wall time must be below 90 seconds")
    if result.get("choices", [{}])[0].get("finish_reason") != "length":
        raise ValueError("Completion did not reach the requested length")
    token_ids = result["choices"][0].get("token_ids", [])
    if len(token_ids) != SETTINGS["output_tokens"] or any(type(i) is not int for i in token_ids):
        raise ValueError("Missing or malformed actual generated token IDs")


def validate_tool_call(result):
    try:
        calls = result["choices"][0]["message"]["tool_calls"]
        valid = len(calls) == 1 and calls[0]["type"] == "function"
        function = calls[0]["function"]
        valid = valid and function["name"] == "add_numbers"
        args = json.loads(function["arguments"])
        valid = valid and args == {"a": 17, "b": 25}
        valid = valid and all(type(v) is int for v in args.values())
        valid = valid and result["choices"][0].get("finish_reason") == "tool_calls"
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed tool call") from exc
    if not valid:
        raise ValueError("Wrong tool or arguments")


def isolated_env(parent):
    # Deliberately omit tokens, general Python paths and implicit proxy credentials.
    env = {k: parent[k] for k in ("HOME", "USER", "LANG") if k in parent}
    env.update({
        "PATH": f"{VENV}/bin:/usr/local/bin:/usr/bin:/bin",
        "UV_CACHE_DIR": str(ROOT / "cache/uv"),
        "UV_PYTHON_INSTALL_DIR": str(ROOT / "cache/uv-python"),
        "UV_PROJECT_ENVIRONMENT": str(VENV),
        "UV_PYTHON_DOWNLOADS": "never",
        "HF_HOME": str(ROOT / "cache/huggingface"),
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "XDG_CACHE_HOME": str(ROOT / "cache/xdg"),
        "XDG_CONFIG_HOME": str(ROOT / "cache/config"),
        "VLLM_CONFIG_ROOT": str(ROOT / "cache/vllm-config"),
        "FLASHINFER_WORKSPACE_BASE": str(ROOT / "cache/flashinfer-workspace"),
        "CUDA_VISIBLE_DEVICES": "0",
        "TORCH_HOME": str(ROOT / "cache/torch"),
        "TRITON_CACHE_DIR": str(ROOT / "cache/triton"),
        "VLLM_CACHE_ROOT": str(ROOT / "cache/vllm"),
        "CUDA_CACHE_PATH": str(ROOT / "cache/cuda"),
        "TMPDIR": str(ROOT / "cache/tmp"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "VLLM_NO_USAGE_STATS": "1",
        "DO_NOT_TRACK": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    })
    env.update(DOWNLOAD_ENV)
    return env


def check_host(argv, system, machine):
    if len(argv) != 1 or system != "Linux" or machine != "x86_64":
        raise ValueError("Fixed no-argument entrypoint requires Linux x86_64 home-5090")


def server_argv():
    return [str(VENV / "bin/python"), "-m", "vllm.entrypoints.openai.api_server",
            "--model", str(MODEL), "--served-model-name", SETTINGS["model"],
            "--dtype", "bfloat16", "--host", "127.0.0.1", "--port", str(SETTINGS["port"]),
            "--max-model-len", str(SETTINGS["max_model_len"]), "--max-num-seqs", "1",
            "--gpu-memory-utilization", str(SETTINGS["gpu_memory_utilization"]),
            "--enable-auto-tool-choice", "--tool-call-parser", "hermes"]
