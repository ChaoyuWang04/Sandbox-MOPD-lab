"""Foreground Mac controller; remote Daytona task compute, no cloud secret copy."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))


def run_foreground(coroutine, seconds):
    """Hard process fallback plus bounded async shutdown; never a background service."""
    loop = asyncio.new_event_loop()
    previous = signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(seconds)
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.wait(pending, timeout=5))
        loop.close()
        asyncio.set_event_loop(None)
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=('status', 'trial'), default='status')
    parser.add_argument('--trial-index', type=int)
    args = parser.parse_args()
    from lab_runtime.controls_v2 import execute, validate_config
    from lab_runtime.m1_run import provider_credentials
    cfg = json.loads((SOURCE/'configs/m1-controls-v2.json').read_text())
    validate_config(cfg)
    base = SOURCE/'artifacts/m1/v2/controls'/cfg['campaign']
    base.mkdir(parents=True, exist_ok=True)
    with (base/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = base/'campaign.json'
        if args.action == 'status':
            print(ledger.read_text() if ledger.exists() else '{"attempts": []}')
            return
        if args.trial_index is None:
            parser.error('--trial-index required for trial')
        cfg = json.loads((SOURCE/'configs/m1-controls-v2.json').read_text())
        validate_config(cfg)
        # Fixed existing Lab secret; allow only the provider key into this process.
        lab = SOURCE.parents[1] if SOURCE.parent.name == '.worktrees' else SOURCE
        credentials = lab/'secrets/.env'
        if credentials.is_symlink() or not credentials.is_file():
            raise ValueError('existing private credentials missing')
        values = {}
        for line in credentials.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'DAYTONA_API_KEY':
                if values:
                    raise ValueError('duplicate provider key')
                values['DAYTONA_API_KEY'] = value.strip().strip('\"\'')
        if not values.get('DAYTONA_API_KEY'):
            raise ValueError('provider key missing')
        with provider_credentials(values):
            result = run_foreground(execute(SOURCE, SOURCE, lambda: None, args.trial_index), cfg['controller_seconds'])
        print(json.dumps(result, sort_keys=True))
        if result['state'] != 'passed':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
