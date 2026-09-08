"""One bounded CPU import and offline catalog job in a disposable Modal container."""
import fcntl
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

from lab_runtime.catalog_run import artifact_hashes, digest
from lab_runtime.task_sources import atomic_json, relative_path, run_import, safe_path

SECONDS = 1150


def resolve_volume_root(root, volume_id):
    """Allow only Modal's declared /vol alias to this hydrated Volume object."""
    root = Path(root)
    if not root.is_symlink():
        return safe_path(root)
    if (root != Path('/vol') or not isinstance(volume_id, str)
            or not volume_id.startswith('vo-')
            or not volume_id[3:] or not volume_id[3:].isalnum()):
        raise ValueError('volume_mount_identity')
    expected = Path('/__modal/volumes') / volume_id
    if root.readlink() != expected or root.resolve(strict=True) != expected:
        raise ValueError('volume_mount_identity')
    return safe_path(expected)


def job(root, manifest, commit, *, runner=subprocess.run, importer=run_import, reader=None):
    root, manifest = Path(root), Path(manifest)
    sha = digest(manifest)
    output = safe_path(root / f'data/m1/v2/catalog-{sha[:16]}')
    status = safe_path(root / 'artifacts/m1/v2/modal-import-latest.json')
    receipt = safe_path(root / f'artifacts/m1/v2/modal-catalog-{sha[:16]}.json')
    status.parent.mkdir(parents=True, exist_ok=True)
    lockpath = safe_path(status.parent / 'modal-import.lock')
    deadline = time.monotonic() + SECONDS
    result = dict(phase='import', manifest_sha256=sha, output_path=str(output))

    def expired(signum, frame):
        raise TimeoutError()

    def acquire():
        handle = lockpath.open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise
        return handle

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(SECONDS)
    lock = None
    try:
        lock = acquire()
        if output.exists():
            if not receipt.is_file():
                raise ValueError('output_conflict')
            prior = json.loads(receipt.read_text())
            if (prior.get('phase') != 'complete' or prior.get('manifest_sha256') != sha
                    or prior.get('output_path') != str(output)
                    or prior.get('artifacts') != artifact_hashes(output)):
                raise ValueError('output_conflict')
            result = prior
        else:
            specification = json.loads(manifest.read_text())
            importer(specification, root=root)
            # Volume commits require closed files. Platform single-container/single-input
            # execution and the launcher's active-App check cover these lock gaps.
            lock.close()
            lock = None
            commit()
            lock = acquire()
            result['phase'] = 'install'
            wheels = [a for a in specification['assets']
                      if a['path'].startswith('tools/pyarrow') and a['path'].endswith('.whl')]
            if len(wheels) != 1:
                raise ValueError('wheel_identity')
            spec = wheels[0]
            wheel = relative_path(root / 'data/m1/v2/sources', spec['path'])
            if not wheel.is_file() or wheel.stat().st_size != spec['size'] or digest(wheel) != spec['sha256']:
                raise ValueError('wheel_identity')
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            runner([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)],
                   timeout=left, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp',
                        'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
                        'CUDA_VISIBLE_DEVICES': '', 'PYTHONNOUSERSITE': '1'})
            result['phase'] = 'catalog'
            if reader is None:
                from lab_runtime.source_catalog import catalog_sources
                reader = catalog_sources
            counts = reader(root / 'data/m1/v2/sources', specification, output)
            result.update(counts, phase='complete', artifacts=artifact_hashes(output))
            atomic_json(receipt, result)
    except Exception as exc:
        allowed = {'wheel_identity', 'output_conflict', 'symlink'}
        error = ('timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                 else 'job_busy' if isinstance(exc, BlockingIOError)
                 else str(exc) if isinstance(exc, ValueError) and str(exc) in allowed
                 else 'operation_failed')
        result.update(failed_phase=result['phase'], phase='failed', error=error)
    finally:
        if lock is not None:
            lock.close()
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    # Final persistence has the platform's remaining 50 seconds; no files remain open.
    if result.get('error') != 'job_busy':
        atomic_json(status, result)
        try:
            commit()
        except Exception:
            return dict(result, phase='failed', error='commit_failed')
    return result
