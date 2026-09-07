"""Fixed host-only G1 probe entrypoint; accepts no arguments."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_runtime.home5090_run import probe

if __name__ == '__main__':
    probe()
