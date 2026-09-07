"""Fresh, isolated reuse of the normal preparation implementation, without GPU."""
import contextlib
import fcntl
import json
import platform
import re
import sys
import time
import uuid

from . import home5090 as contract, home5090_run as runner


@contextlib.contextmanager
def private_roots(root):
    base = runner.ROOT
    if root.parent != base/'cold-rebuilds' or not re.fullmatch('[a-f0-9]{32}', root.name):
        raise ValueError('cold root must be a unique Lab child')
    runner.safe_path(root)
    saved = [(module, name, getattr(module, name))
             for module in (contract, runner) for name in ('ROOT', 'VENV', 'MODEL')]
    try:
        for module in (contract, runner):
            module.ROOT = root
            module.VENV = root/'envs'/contract.VENV.name
            module.MODEL = root/'models/Qwen3-4B'/contract.SETTINGS['revision']
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def prepare_cold():
    contract.check_host(sys.argv, platform.system(), platform.machine())
    started = time.monotonic()
    base = runner.safe_path(runner.ROOT)
    index = runner.safe_path(base/'artifacts/m0/home5090')
    # Share the ordinary Lab lock: a cold install cannot race prepare/probe.
    with runner.safe_path(index/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent = runner.safe_path(base/'cold-rebuilds')
        parent.mkdir(exist_ok=True)
        root = parent/uuid.uuid4().hex
        runner.safe_path(root).mkdir(exist_ok=False)
        result = {'success': False, 'cold_root': str(root)}
        try:
            with private_roots(root):
                runner.prepare()
        finally:
            status_path = root/'artifacts/m0/home5090/prepare-latest.json'
            if status_path.is_file():
                result = json.loads(status_path.read_text())
            result['cold_root'] = str(root)
            result['end_to_end_seconds'] = time.monotonic()-started
            result['g4_candidate'] = bool(result.get('success')) and contract.qualifies_cold_prepare(
                result.get('cold_start', False), result['end_to_end_seconds'])
            runner.write_json(index/'cold-prepare-latest.json', result)

