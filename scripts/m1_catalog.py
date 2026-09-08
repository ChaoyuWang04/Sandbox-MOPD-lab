"""Fixed home-5090 CPU-only catalog entrypoint."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.catalog_run import main

if __name__ == '__main__':
    raise SystemExit(main())
