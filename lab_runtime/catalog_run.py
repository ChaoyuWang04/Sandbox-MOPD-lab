"""Offline CPU catalog bootstrap with one parent-owned deadline and lock."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

from lab_runtime.home5090 import ROOT, UV, VENV, check_host
from lab_runtime.task_sources import safe_path, atomic_json

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'configs/m1-sources-v2.json'
SCRIPT = REPO / 'scripts/m1_catalog.py'
SECONDS = 1200


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(root, manifest):
    sha = digest(manifest)
    return sha, safe_path(root / f'data/m1/v2/catalog-{sha[:16]}'), safe_path(root / 'artifacts/m1/v2/catalog-latest.json')


def save(path, result):
    atomic_json(path, result)


def environment_identity(envdir, wheel_sha):
    safe_path(envdir / 'bin')
    config = safe_path(envdir / 'pyvenv.cfg')
    interpreter = (envdir / 'bin/python').resolve(strict=True)
    for parent, dirs, files in os.walk(envdir):
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink():
                target = path.resolve(strict=True)
                is_python = path.parent == envdir / 'bin' and name in ('python', 'python3', 'python3.12')
                if not target.is_relative_to(envdir.resolve()) and not (is_python and target == interpreter):
                    raise ValueError('environment_conflict')
    return {'environment_path': str(envdir.resolve()), 'wheel_sha256': wheel_sha,
            'python_path': str(interpreter), 'python_sha256': digest(interpreter),
            'config_sha256': digest(config)}


def artifact_hashes(output):
    files = {}
    for p in sorted(output.rglob('*')):
        if p.is_symlink() or not p.is_file():
            raise ValueError('output_conflict')
        files[str(p.relative_to(output))] = digest(p)
    if not files:
        raise ValueError('output_conflict')
    return files


def catalog(root, manifest, reader=None):
    sha, output, status = identity(root, manifest)
    if output.exists():
        if output.is_symlink() or not status.is_file():
            raise ValueError('output_conflict')
        previous = json.loads(status.read_text())
        if (previous.get('phase') != 'complete' or previous.get('manifest_sha256') != sha
                or previous.get('output_path') != str(output)
                or previous.get('artifacts') != artifact_hashes(output)):
            raise ValueError('output_conflict')
        return previous
    if reader is None:
        from lab_runtime.source_catalog import catalog_sources
        reader = catalog_sources
    counts = reader(root / 'data/m1/v2/sources', json.loads(manifest.read_text()), output)
    result = dict(counts, phase='complete', manifest_sha256=sha,
                  output_path=str(output), artifacts=artifact_hashes(output))
    save(status, result)
    return result


def run(root=ROOT, manifest=MANIFEST, runner=subprocess.run):
    deadline = time.monotonic() + SECONDS
    sha, output, status = identity(root, manifest)
    result = dict(phase='bootstrap', manifest_sha256=sha, output_path=str(output))
    status.parent.mkdir(parents=True, exist_ok=True)
    with safe_path(status.parent / 'catalog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(result, phase='failed', error='catalog_busy')
        try:
            if output.exists():
                return catalog(root, manifest)
            assets = json.loads(manifest.read_text())['assets']
            wheels = [a for a in assets if a['path'].startswith('tools/pyarrow') and a['path'].endswith('.whl')]
            if len(wheels) != 1:
                raise ValueError('wheel_identity')
            spec = wheels[0]
            wheel = safe_path(root / 'data/m1/v2/sources' / spec['path'])
            if ('..' in Path(spec['path']).parts or wheel.is_symlink()
                    or not wheel.is_file() or wheel.stat().st_size != spec['size']
                    or digest(wheel) != spec['sha256']):
                raise ValueError('wheel_identity')
            envdir = safe_path(root / ('envs/m1-catalog-' + spec['sha256'][:12]))
            receipt = safe_path(envdir / '.catalog-environment.json')
            existed = envdir.exists()
            if existed:
                if not receipt.is_file() or json.loads(receipt.read_text()) != environment_identity(envdir, spec['sha256']):
                    raise ValueError('environment_conflict')
            cache = safe_path(root / 'cache/m1-catalog')
            for p in (cache / 'tmp', cache / 'home', cache / 'config'):
                safe_path(p).mkdir(parents=True, exist_ok=True)
            safe_path(cache / 'uv')
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(cache / 'home'),
                   'LANG': 'C.UTF-8', 'CUDA_VISIBLE_DEVICES': '',
                   'UV_OFFLINE': '1', 'UV_NO_CONFIG': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                   'UV_CACHE_DIR': str(cache / 'uv'), 'XDG_CACHE_HOME': str(cache),
                   'XDG_CONFIG_HOME': str(cache / 'config'), 'TMPDIR': str(cache / 'tmp'),
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
            def execute(argv, child=False):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                child_env = dict(env)
                if child:
                    child_env['M1_CATALOG_CHILD'] = str(os.getpid())
                runner(argv, env=child_env, cwd=str(REPO), timeout=remaining,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result['phase'] = 'venv'
            if not existed:
                execute([str(UV), 'venv', '--python', str(VENV / 'bin/python'),
                         '--no-python-downloads', str(envdir)])
                save(receipt, environment_identity(envdir, spec['sha256']))
            result['phase'] = 'install'
            execute([str(UV), 'pip', 'install', '--python', str(envdir / 'bin/python'),
                     '--no-deps', '--no-index', str(wheel)])
            result['phase'] = 'catalog'
            execute([str(envdir / 'bin/python'), '-I', '-B', str(SCRIPT)], child=True)
            return catalog(root, manifest)
        except Exception as exc:
            allowed = {'wheel_identity', 'output_conflict', 'environment_conflict', 'symlink'}
            error = ('timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                     else str(exc) if isinstance(exc, ValueError) and str(exc) in allowed
                     else 'operation_failed')
            result.update(failed_phase=result['phase'], phase='failed', error=error)
            save(status, result)
            return result


def main():
    check_host(sys.argv, platform.system(), platform.machine())
    if os.environ.get('M1_CATALOG_CHILD') == str(os.getppid()):
        # Parent holds the lock and enforces the remaining wall-clock deadline.
        catalog(ROOT, MANIFEST)
        return 0
    def expired(signum, frame):
        raise TimeoutError()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(SECONDS)
    try:
        return 0 if run()['phase'] == 'complete' else 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
