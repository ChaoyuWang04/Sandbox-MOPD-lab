"""Fixed no-argument host entry; source import only."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.task_sources import main

if __name__ == '__main__':
    raise SystemExit(main())
