"""Fixed M1 entry; system Python bootstraps the prepared serving environment."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.m1_run import bootstrap

if __name__ == '__main__':
    bootstrap()
