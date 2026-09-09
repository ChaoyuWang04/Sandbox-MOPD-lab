"""Fixed no-argument launcher for the bounded Daytona GPU probe."""
import os
import sys
sys.dont_write_bytecode = True
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from lab_runtime.home5090 import VENV

if Path(sys.prefix) != VENV:
    executable = VENV / "bin/python"
    if not executable.is_file():
        raise ValueError("prepared M1 Python environment is missing; no automatic install")
    env = {key: os.environ[key] for key in ("HOME", "USER", "LANG") if key in os.environ}
    env.update({"PATH": f"{VENV}/bin:/usr/local/bin:/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
    os.execve(str(executable), [str(executable), "-B", str(Path(__file__).resolve())], env)
    raise RuntimeError("execve unexpectedly returned")

from lab_runtime.daytona_gpu_probe import main

if __name__ == "__main__":
    main()
