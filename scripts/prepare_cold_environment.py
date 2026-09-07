"""No-argument, host-only fresh M0 preparation. Keeps all existing assets."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.cold_prepare import prepare_cold

if __name__ == '__main__':
    prepare_cold()
