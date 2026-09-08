"""Build registered unified200 files locally; no containers or task execution."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.pool_build_v2 import build_pool

if __name__ == '__main__':
    result = build_pool(Path(__file__).resolve().parents[1])
    print(result['status'], result['counts'])
