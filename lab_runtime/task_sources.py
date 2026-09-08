"""Bounded, checksum-verified public source import. Never executes source code."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import tarfile
import tempfile
import time
import urllib.request

from lab_runtime.home5090 import ROOT

MAX_SECONDS = 7200
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXTRACT = 2 * 1024 * 1024 * 1024


class ImportFailure(ValueError):
    """Only sanitized reason codes cross the CLI boundary."""


def remaining(deadline, clock):
    left = deadline - clock()
    if left <= 0:
        raise ImportFailure('deadline')
    return min(30, left)


def safe_path(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ImportFailure('symlink')
    return path


def relative_path(base, name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ImportFailure('unsafe_asset_path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or name == '.':
        raise ImportFailure('unsafe_asset_path')
    target = safe_path(base / relative)
    if not target.resolve().is_relative_to(base.resolve()):
        raise ImportFailure('unsafe_asset_path')
    return target


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def digest(path, deadline, clock):
    h = hashlib.sha256()
    size = 0
    with safe_path(path).open('rb') as stream:
        while True:
            remaining(deadline, clock)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return {'size': size, 'sha256': h.hexdigest()}


def public_open(url, timeout):
    # Explicit empty proxy handler avoids credentials and environment proxies.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout)


def download(spec, target, deadline, *, opener=public_open, clock=time.monotonic, compare_existing_stream=False):
    remaining(deadline, clock)
    target = safe_path(target)
    if target.exists() and not compare_existing_stream:
        got = digest(target, deadline, clock)
        if not spec.get('sha256') or got['sha256'] != spec['sha256'] or ('size' in spec and got['size'] != spec['size']):
            raise ImportFailure('existing_conflict')
        return dict(got, reused=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    cap = spec.get('size', spec.get('max_bytes', MAX_ARCHIVE))
    fd, temporary = tempfile.mkstemp(prefix='.import-', dir=target.parent)
    try:
        h = hashlib.sha256()
        size = 0
        with os.fdopen(fd, 'wb') as output, opener(spec['url'], timeout=remaining(deadline, clock)) as source:
            while True:
                remaining(deadline, clock)
                chunk = source.read(min(1024 * 1024, cap - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    raise ImportFailure('size_limit')
                output.write(chunk)
                h.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        got = {'size': size, 'sha256': h.hexdigest()}
        if ('size' in spec and size != spec['size']) or ('sha256' in spec and got['sha256'] != spec['sha256']):
            raise ImportFailure('checksum_or_size')
        remaining(deadline, clock)
        if compare_existing_stream and target.exists():
            if digest(target, deadline, clock) != got:
                raise ImportFailure('existing_conflict')
            return dict(got, reused=True)
        os.link(temporary, target)  # fails rather than replacing a concurrent target
        return dict(got, reused=False)
    finally:
        os.unlink(temporary)


def extract(archive, destination, prefixes, deadline, *, max_bytes=MAX_EXTRACT, clock=time.monotonic):
    destination = safe_path(destination)
    records = []
    total = 0
    seen = set()
    with tarfile.open(safe_path(archive), 'r|gz') as tar:
        for member in tar:
            remaining(deadline, clock)
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith('/') or '..' in parts or '\\' in member.name or not (member.isfile() or member.isdir()):
                raise ImportFailure('unsafe_archive_member')
            total += member.size
            if total > max_bytes:
                raise ImportFailure('extracted_size_limit')
            relative = '/'.join(parts[1:])
            if not relative or member.isdir():
                continue
            if relative in seen:
                raise ImportFailure('duplicate_archive_member')
            seen.add(relative)
            if '*' not in prefixes and not any(relative == p or relative.startswith(p + '/') for p in prefixes):
                continue
            target = safe_path(destination / relative)
            # Stream the member into an owned temporary file while hashing. Existing
            # output is reused only after comparison to these actual archive bytes.
            spec = {'url': 'archive-member', 'size': member.size}
            result = download(spec, target, deadline,
                              opener=lambda *a, **k: tar.extractfile(member),
                              clock=clock, compare_existing_stream=True)
            if not result['reused']:
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            records.append(dict(result, path=relative))
    return records


def atomic_json(path, value):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_import(manifest, root=ROOT, *, opener=public_open, clock=time.monotonic):
    deadline = clock() + MAX_SECONDS
    base = safe_path(root / 'data/m1/v2/sources')
    status_path = safe_path(root / 'artifacts/m1/v2/import-latest.json')
    base.mkdir(parents=True, exist_ok=True)
    status = {'schema_version': 2, 'state': 'running', 'assets': {}, 'manifest_identity': identity(manifest)}
    lock_path = safe_path(base / '.import.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportFailure('writer_active') from None
        previous = json.loads(status_path.read_text()) if status_path.exists() else {}
        status['assets'] = dict(previous.get('assets', {}))
        atomic_json(status_path, status)
        try:
            for asset in manifest['assets']:
                name = asset['path']
                target = relative_path(base, name)
                destination = relative_path(base, asset['destination']) if asset.get('extract') else None
                spec = dict(asset)
                prior = previous.get('assets', {}).get(name, {})
                asset_identity = identity(asset)
                if prior and (prior.get('revision') != asset['revision'] or prior.get('source_url') != asset['url'] or prior.get('manifest_identity') != asset_identity):
                    raise ImportFailure('source_identity_conflict')
                if 'sha256' not in spec and prior.get('sha256'):
                    spec.update(sha256=prior['sha256'], size=prior['size'])
                result = download(spec, target, deadline, opener=opener, clock=clock)
                status['assets'][name] = dict(result, revision=asset['revision'], source_url=asset['url'], manifest_identity=asset_identity)
                atomic_json(status_path, status)
                if asset.get('extract'):
                    result['files'] = extract(target, destination, asset['extract'], deadline, clock=clock)
                    status['assets'][name].update(result)
                    atomic_json(status_path, status)
            status['state'] = 'complete'
        except Exception as exc:
            status['state'] = 'failed'
            status['reason'] = str(exc) if isinstance(exc, ImportFailure) else 'io_error'
            raise ImportFailure(status['reason']) from None
        finally:
            atomic_json(status_path, status)
    return status


def alarm_timeout(signum, frame):
    raise ImportFailure('deadline')


def main():
    import platform
    import sys
    from lab_runtime.home5090 import check_host
    check_host(sys.argv, platform.system(), platform.machine())
    signal.signal(signal.SIGALRM, alarm_timeout)
    signal.alarm(MAX_SECONDS)
    try:
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'configs/m1-sources-v2.json').read_text())
        run_import(manifest)
        print('m1_import_complete')
    except Exception:
        print('m1_import_failed; inspect artifacts/m1/v2/import-latest.json')
        return 1
    finally:
        signal.alarm(0)
    return 0
