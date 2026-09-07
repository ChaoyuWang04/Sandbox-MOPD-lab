"""No-argument, bounded home-5090 entrypoints. Importing this module does no work."""
import contextlib
import contextvars
import datetime
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from .home5090 import (ROOT, UV, VENV, MODEL, SETTINGS, check_host,
                       isolated_env, server_argv, validate_completion, validate_tool_call,
                       PREPARE_SECONDS, PACKAGE_INDEX, DOWNLOAD_ENV,
                       model_download_argv, qualifies_cold_prepare)

SOURCE = Path(__file__).resolve().parents[1]
OWNED_SERVER_PGID = contextvars.ContextVar('owned_server_pgid', default=None)
HEADER_SHA256 = '864360533639b45256258475c7843ac7c56f9bf556b356753f21f7e3012fea67'
HEADER_URL = 'https://mirrors.aliyun.com/ubuntu/pool/main/p/python3.12/libpython3.12-dev_3.12.3-1ubuntu0.16_amd64.deb'


def header_manifest(root, deadline):
    result = {}
    for path in sorted(root.rglob('*')):
        remaining(deadline)
        if path.is_symlink():
            raise ValueError('header symlinks are not permitted')
        if path.is_file():
            result[str(path.relative_to(root))] = sha(path, deadline)
    if not result:
        raise ValueError('empty Python headers')
    return result


def header_environment(root):
    env = isolated_env(os.environ)
    env['CPATH'] = f'{root}/usr/include/python3.12:{root}/usr/include'
    return env


def prepare_headers(run, deadline, log):
    # Verified Ubuntu payload, unpacked privately: never apt install or sudo.
    archive = safe_path(run/'python-dev.deb')
    command(['/usr/bin/curl', '-q', '--fail', '--location', '--max-time', '120',
             '--max-filesize', '6000000', '--output', str(archive), HEADER_URL], deadline, log)
    if sha(archive, deadline) != HEADER_SHA256:
        raise ValueError('Python header package hash mismatch')
    root = safe_path(run/'python-headers')
    root.mkdir()
    command(['/usr/bin/dpkg-deb', '--extract', str(archive), str(root)], deadline, log)
    include = safe_path(root/'usr/include')
    manifest = header_manifest(include, deadline)
    source = safe_path(run/'header-check.c')
    source.write_text('#include <Python.h>\n#if PY_MAJOR_VERSION != 3 || PY_MINOR_VERSION != 12\n#error wrong Python ABI\n#endif\nint main(void) { return 0; }\n')
    command(['/usr/bin/gcc', '-fsyntax-only', '-I'+str(include/'python3.12'),
             '-I'+str(include), str(source)], deadline, log)
    return {'root': str(root), 'package_sha256': HEADER_SHA256,
            'manifest': manifest, 'syntax_check': True,
            'version': '3.12.3-1ubuntu0.16',
            'scope': 'headers only; system Python and libraries unchanged'}


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError('overall budget exhausted')
    return value


def safe_path(path):
    path = Path(path)
    if ROOT.is_symlink() or ROOT.resolve() != ROOT:
        raise ValueError('Lab root must not traverse symlinks')
    if not path.is_relative_to(ROOT) or path.resolve() != path:
        raise ValueError('managed path escapes or traverses symlinks')
    return path


def sha(path, deadline):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while True:
            remaining(deadline)
            block = handle.read(1024 * 1024)
            if not block:
                return h.hexdigest()
            h.update(block)


def write_json(path, value):
    safe_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_group(child):
    # Only accepts the Popen object created with start_new_session=True below.
    pgid = child.pid
    if pgid == os.getpgrp():
        raise RuntimeError('refusing own controller process group')
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if group_exists(pgid):
            os.killpg(pgid, sig)
        until = time.monotonic() + 3
        while time.monotonic() < until:
            child.poll()  # Reap the group leader before checking disappearance.
            if not group_exists(pgid):
                child.wait(timeout=1)
                return
            time.sleep(0.05)
    raise RuntimeError('owned process group did not disappear')


@contextlib.contextmanager
def cleanup_signals():
    # Cleanup is independently bounded by join(7) and TERM/KILL waits (6s).
    # A repeated controller signal must not abandon an already owned process.
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    started = time.monotonic()
    pending = []
    saved_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signals = (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)
    handlers = {sig: signal.getsignal(sig) for sig in signals}
    def defer(signum, frame):
        pending.append(signum)
    for sig in signals:
        signal.signal(sig, defer)
    try:
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if saved_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, saved_timer[0] - (time.monotonic()-started)), saved_timer[1])
        if signal.SIGINT in pending:
            raise KeyboardInterrupt
        if signal.SIGTERM in pending:
            raise RuntimeError('received SIGTERM during cleanup')
        if signal.SIGALRM in pending:
            raise TimeoutError('deadline reached during cleanup')


def cleanup_child(child, monitor=None, stop=None):
    with cleanup_signals():
        try:
            if stop is not None:
                stop.set()
            if monitor is not None:
                monitor.join(timeout=7)
        finally:
            stop_group(child)


def command(argv, deadline, log):
    timeout = remaining(deadline)
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=log,
                             text=True, env=isolated_env(os.environ),
                             start_new_session=True)
    try:
        output, _ = child.communicate(timeout=timeout)
        log.write(output)
        log.flush()
        remaining(deadline)
        if child.returncode:
            raise RuntimeError('command failed: ' + str(argv[0]))
        return output
    finally:
        cleanup_child(child)


def has_runtime_content(path):
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_dir():
        return True
    return any(p.name != '.gitkeep' or not p.is_file() or p.is_symlink() or p.stat().st_size != 0
               for p in path.iterdir())


@contextlib.contextmanager
def run_context(kind, seconds, *, artifact_stage='m0'):
    if artifact_stage not in ('m0', 'm1'):
        raise ValueError('unknown artifact stage')
    start = time.monotonic()
    deadline = start + seconds
    safe_path(ROOT)
    # Capture cold state before making the runtime directories.
    existed = {k: has_runtime_content(p) for k, p in [('environment', VENV), ('model', MODEL), ('cache', ROOT/'cache')]}
    for path in (ROOT, ROOT/'envs', ROOT/'models', ROOT/'cache', ROOT/'artifacts', ROOT/'artifacts/m0', ROOT/'artifacts/m0/home5090'):
        safe_path(path).mkdir(exist_ok=True)
    lock_base = ROOT/'artifacts/m0/home5090'
    base = ROOT/f'artifacts/{artifact_stage}/home5090'
    safe_path(base.parent).mkdir(exist_ok=True)
    safe_path(base).mkdir(exist_ok=True)
    run = base / (kind + '-' + uuid.uuid4().hex if artifact_stage == 'm1' else kind + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex)
    run.mkdir()
    state = {'kind': kind, 'success': False, 'run_dir': str(run), 'existed_before': existed,
             'cold_start': not any(existed.values()), 'revision': SETTINGS['revision']}
    lock = None
    acquired = False
    previous = signal.getsignal(signal.SIGTERM)
    previous_alarm = signal.getsignal(signal.SIGALRM)
    def terminate(signum, frame):
        raise RuntimeError('received SIGTERM')
    signal.signal(signal.SIGTERM, terminate)
    def timed_out(signum, frame):
        raise TimeoutError('overall wall-clock budget exhausted')
    signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, remaining(deadline))
    try:
        lock = safe_path(lock_base/'run.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        with (run/'subprocess.log').open('x') as log:
            yield run, state, deadline, log
        remaining(deadline)
        state['success'] = True
    except BaseException as exc:
        state['success'] = False
        state['error_type'] = type(exc).__name__
        state['error'] = str(exc)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        state['elapsed_seconds'] = time.monotonic() - start
        write_json(run/'state.json', state)
        if acquired:
            write_json(base/(kind+'-latest.json'), state)
        if lock:
            lock.close()
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGALRM, previous_alarm)


def claim_paths(marker, existed):
    identity = {'environment': str(VENV), 'model': str(MODEL), 'cache': str(ROOT/'cache')}
    owned = set()
    if marker.exists():
        saved = json.loads(safe_path(marker).read_text())
        if saved.get('root') != str(ROOT) or not isinstance(saved.get('paths'), list):
            raise ValueError('managed path ownership mismatch')
        owned = set(saved['paths'])
        for path in owned:
            safe_path(Path(path))
    for domain, path in identity.items():
        if existed.get(domain, False) and path not in owned:
            raise ValueError('refusing to modify unknown existing environment/model/cache')
    write_json(marker, {'root': str(ROOT), 'paths': sorted(owned | set(identity.values()))})


def asset_usage(deadline, log):
    usage = {}
    for name, path in [('environment', VENV), ('model', MODEL), ('cache', ROOT/'cache')]:
        safe_path(path)
        result = command(['/usr/bin/du', '-s', '-B1', '--', str(path)], deadline, log)
        value = int(result.split()[0])
        if value < 0:
            raise ValueError('invalid disk allocation reading')
        usage[name] = value
    return usage


def source_identity(deadline, log):
    git = ['/usr/bin/git', '--no-optional-locks', '-C', str(SOURCE)]
    if command(git + ['rev-parse', '--abbrev-ref', 'HEAD'], deadline, log).strip() != 'HEAD':
        raise ValueError('source must be a detached checkout')
    if command(git + ['status', '--porcelain', '--untracked-files=all'], deadline, log).strip():
        raise ValueError('source checkout must be clean')
    return command(git + ['rev-parse', 'HEAD'], deadline, log).strip()


def install_environment(run, deadline, log):
    # A frozen uv sync uses artifact URLs from uv.lock even with an index override.
    # Export the unchanged lock so the transport index may change, but not hashes/versions.
    requirements = run/'requirements.txt'
    project = SOURCE/'environments/home5090'
    command([str(UV), 'export', '--frozen', '--offline', '--no-dev', '--no-emit-project',
             '--project', str(project), '--format', 'requirements-txt',
             '--output-file', str(requirements)], deadline, log)
    if not VENV.exists():
        command([str(UV), 'venv', '--python', '/usr/bin/python3', str(VENV)], deadline, log)
    command([str(UV), 'pip', 'sync', str(requirements), '--require-hashes', '--no-build',
             '--default-index', PACKAGE_INDEX, '--python', str(VENV/'bin/python')], deadline, log)
    command([str(UV), 'pip', 'check', '--python', str(VENV/'bin/python')], deadline, log)
    return requirements


def prepare():
    check_host(sys.argv, platform.system(), platform.machine())
    with run_context('prepare', PREPARE_SECONDS) as (run, state, deadline, log):
        if shutil.disk_usage(ROOT).free < 80 * 1024**3:
            raise ValueError('less than 80 GiB disk free')
        safe_path(VENV)
        safe_path(MODEL)
        claim_paths(ROOT/'artifacts/m0/home5090/managed-paths.json', state['existed_before'])
        project = SOURCE/'environments/home5090'
        state['lock_sha256'] = sha(project/'uv.lock', deadline)
        state['source_git_sha'] = source_identity(deadline, log)
        state['download_policy'] = {'package_index': PACKAGE_INDEX, **DOWNLOAD_ENV,
                                    'model_workers': 2, 'work_budget_seconds': PREPARE_SECONDS}
        for path in isolated_env(os.environ).values():
            if path.startswith(str(ROOT) + '/cache/'):
                safe_path(Path(path)).mkdir(parents=True, exist_ok=True)
        requirements = install_environment(run, deadline, log)
        state['python_headers'] = prepare_headers(run, deadline, log)
        state['requirements_sha256'] = sha(requirements, deadline)
        command(model_download_argv(), deadline, log)
        state['pip_freeze'] = command([str(UV), 'pip', 'freeze', '--python', str(VENV/'bin/python')], deadline, log)
        manifest = model_manifest(deadline)
        write_json(run/'model-manifest.json', manifest)
        state['model_manifest'] = str(run/'model-manifest.json')
        state['allocated_disk_bytes'] = asset_usage(deadline, log)
        state['g4_candidate'] = qualifies_cold_prepare(state['cold_start'],
                                                     PREPARE_SECONDS - remaining(deadline))
        state['gpu_validated'] = False


def model_manifest(deadline):
    manifest = {}
    for path in sorted(MODEL.rglob('*')):
        relative = path.relative_to(MODEL)
        if '.cache' in relative.parts:
            continue
        safe_path(path)
        if path.is_file():
            manifest[str(relative)] = sha(path, deadline)
    if 'config.json' not in manifest or not any(name.endswith('.safetensors') for name in manifest):
        raise ValueError('model requires config.json and safetensors weights')
    json.loads((MODEL/'config.json').read_text())
    index = MODEL/'model.safetensors.index.json'
    if index.exists():
        shards = set(json.loads(index.read_text())['weight_map'].values())
        if not shards or not shards.issubset(manifest):
            raise ValueError('model weight index references missing shards')
    return manifest


def validate_models(result):
    entries = result.get('data', [])
    if len(entries) != 1 or entries[0].get('id') != SETTINGS['model'] or entries[0].get('root') != str(MODEL):
        raise ValueError('model API identity does not match fixed local model')


def assert_listener_owned(pgid, proc=Path('/proc')):
    endpoint = '0100007F:' + format(SETTINGS['port'], '04X')
    listeners = set()
    for line in (proc/'net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1] == endpoint and fields[3] == '0A':
            listeners.add(fields[9])
    if not listeners:
        raise ConnectionRefusedError('fixed loopback listener not ready')
    owned = set()
    for pid_path in proc.iterdir():
        if not pid_path.name.isdecimal():
            continue
        try:
            if os.getpgid(int(pid_path.name)) != pgid:
                continue
            for fd in (pid_path/'fd').iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if target.startswith('socket:[') and target.endswith(']'):
                    owned.add(target[8:-1])
        except ProcessLookupError:
            continue
    if not listeners.issubset(owned):
        raise ValueError('fixed listener does not belong to owned server process group')


def validate_prepared(state, lock_sha, source_sha=None):
    if state.get('success') is not True or state.get('revision') != SETTINGS['revision'] or state.get('lock_sha256') != lock_sha or (source_sha is not None and state.get('source_git_sha') != source_sha):
        raise ValueError('successful matching prepare state required')


def http_json(method, path, payload, deadline):
    # Direct HTTPConnection never uses proxy environment or follows redirects.
    if OWNED_SERVER_PGID.get() is not None:
        assert_listener_owned(OWNED_SERVER_PGID.get())
    connection = http.client.HTTPConnection('127.0.0.1', SETTINGS['port'], timeout=remaining(deadline))
    try:
        connection.request(method, path, body=None if payload is None else json.dumps(payload),
                           headers={'Content-Type': 'application/json'})
        if connection.sock:
            connection.sock.settimeout(remaining(deadline))
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('loopback HTTP status ' + str(response.status))
        chunks = []
        while True:
            if connection.sock:
                connection.sock.settimeout(remaining(deadline))
            block = response.read1(65536)
            if not block:
                break
            chunks.append(block)
        remaining(deadline)
        return json.loads(b''.join(chunks))
    finally:
        connection.close()


def validate_gpu_identity(output):
    fields = [part.strip() for part in output.strip().split(',')]
    if len(fields) != 4 or fields[1] != 'NVIDIA GeForce RTX 5090' or not fields[0].startswith('GPU-'):
        raise ValueError('GPU0 must be NVIDIA GeForce RTX 5090')
    return {'uuid': fields[0], 'name': fields[1], 'driver_version': fields[2], 'total_memory_mib': int(fields[3])}


def probe():
    check_host(sys.argv, platform.system(), platform.machine())
    with run_context('probe', 600) as (run, state, deadline, log):
        with managed_server(run, state, deadline, log) as child:
            _probe_requests(child, run, state, deadline, log)


@contextlib.contextmanager
def managed_server(run, state, deadline, log, *, allow_previous_source=False):
    prepared = json.loads(safe_path(ROOT/'artifacts/m0/home5090/prepare-latest.json').read_text())
    source_sha = source_identity(deadline, log)
    lock_sha = sha(SOURCE/'environments/home5090/uv.lock', deadline)
    validate_prepared(prepared, lock_sha, None if allow_previous_source else source_sha)
    headers = prepared['python_headers']
    header_root = safe_path(Path(prepared['run_dir'])/'python-headers')
    if headers['root'] != str(header_root) or headers['package_sha256'] != HEADER_SHA256:
        raise ValueError('prepared header identity mismatch')
    if header_manifest(safe_path(header_root/'usr/include'), deadline) != headers['manifest']:
        raise ValueError('prepared headers drifted')
    state['python_headers'] = {'root': str(header_root), 'package_sha256': HEADER_SHA256}
    state['prepare_source_git_sha'] = prepared.get('source_git_sha')
    state['source_git_sha'] = source_sha
    state['lock_sha256'] = lock_sha
    expected_manifest_path = safe_path(Path(prepared['run_dir'])/'model-manifest.json')
    expected_manifest = json.loads(expected_manifest_path.read_text())
    actual_manifest = model_manifest(deadline)
    if expected_manifest != actual_manifest:
        raise ValueError('prepared model content drifted')
    write_json(run/'model-manifest.json', actual_manifest)
    remaining(deadline)
    state['prepare_run'] = prepared['run_dir']
    gpu = command(['/usr/bin/nvidia-smi', '--id=0', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], deadline, log)
    state['gpu_identity'] = validate_gpu_identity(command(['/usr/bin/nvidia-smi', '--id=0', '--query-gpu=uuid,name,driver_version,memory.total', '--format=csv,noheader,nounits'], deadline, log))
    free = command(['/usr/bin/free', '-b'], deadline, log)
    state['resource_preflight'] = {'gpu_free_mib': gpu, 'free_bytes': free}
    if len(gpu.strip().splitlines()) != 1 or int(gpu.strip()) < 26*1024:
        raise ValueError('requires one GPU with >=26 GiB free')
    mem = next(line.split() for line in free.splitlines() if line.startswith('Mem:'))
    if int(mem[-1]) < 18*1024**3:
        raise ValueError('less than 18 GiB RAM available')
    with socket.socket() as port:
        port.bind(('127.0.0.1', SETTINGS['port']))
    state['post_model_check'] = 'not_checked'
    handles = {}
    child = subprocess.Popen(server_argv(), stdout=log, stderr=log,
                             env=header_environment(header_root), start_new_session=True)
    try:
        _start_server_monitor(child, handles, run, state, deadline, log)
        token = OWNED_SERVER_PGID.set(child.pid)
        try:
            yield child
        finally:
            OWNED_SERVER_PGID.reset(token)
    finally:
        try:
            cleanup_child(child, handles.get('monitor'), handles.get('stop'))
        finally:
            state['owned_group_gone'] = not group_exists(child.pid)
    if handles['monitor'].is_alive():
        raise RuntimeError('GPU sampler did not stop')
    if handles['errors']:
        raise handles['errors'][0]
    if model_manifest(deadline) != actual_manifest:
        raise ValueError('probe modified prepared model content')
    state['post_model_check'] = 'verified'


def _start_server_monitor(child, handles, run, state, deadline, log):
    state['server_pid'] = child.pid
    state['server_argv'] = server_argv()
    state['peak_owned_gpu_mib'] = 0
    state['gpu_sampling_limitation'] = '1 second polling; transient peaks and processes leaving the owned process group are not observable'
    stop = threading.Event()
    errors = []
    def sample():
        try:
            with (run/'gpu-samples.log').open('x') as sample_log:
                while not stop.is_set():
                    output = command(['/usr/bin/nvidia-smi', '--id=0', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'], min(deadline, time.monotonic()+5), sample_log)
                    total = 0
                    for line in output.splitlines():
                        pid, memory = line.split(',')
                        try:
                            owned = os.getpgid(int(pid)) == child.pid
                        except ProcessLookupError:
                            continue
                        if owned:
                            total += int(memory)
                    state['peak_owned_gpu_mib'] = max(state['peak_owned_gpu_mib'], total)
                    if total > 24576:
                        raise RuntimeError('owned GPU memory exceeded 24 GiB')
                    stop.wait(1)
        except BaseException as exc:
            errors.append(exc)
            try:
                if group_exists(child.pid):
                    os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    monitor = threading.Thread(target=sample, daemon=True)
    handles['stop'] = stop
    handles['monitor'] = monitor
    handles['errors'] = errors
    monitor.start()
    ready_deadline = min(deadline, time.monotonic()+300)
    while True:
        if errors:
            raise errors[0]
        if child.poll() is not None:
            raise RuntimeError('server exited before ready')
        remaining(ready_deadline)
        try:
            assert_listener_owned(child.pid)
            models = http_json('GET', '/v1/models', None, ready_deadline)
            if child.poll() is not None:
                raise RuntimeError('owned server exited during readiness check')
            validate_models(models)
            assert_listener_owned(child.pid)
            break
        except (ConnectionError, socket.timeout, http.client.HTTPException):
            time.sleep(min(1, remaining(ready_deadline)))
    write_json(run/'models.json', models)


def _probe_requests(child, run, state, deadline, log):
    tool = {'model': SETTINGS['model'], 'messages': [{'role': 'user', 'content': 'Call add_numbers with a=17 and b=25.'}],
            'tools': [{'type': 'function', 'function': {'name': 'add_numbers', 'parameters': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}}}],
            'tool_choice': 'auto', 'temperature': 0, 'max_tokens': 256, 'chat_template_kwargs': {'enable_thinking': False}}
    write_json(run/'tool-request.json', tool)
    assert_listener_owned(child.pid)
    result = http_json('POST', '/v1/chat/completions', tool, min(deadline, time.monotonic()+60))
    write_json(run/'tool-response.json', result)
    validate_tool_call(result)
    tokenizer = 'from transformers import AutoTokenizer; import json; t=AutoTokenizer.from_pretrained('+repr(str(MODEL))+',local_files_only=True); print(json.dumps(t.encode("A fixed context for bounded inference. "*20000,add_special_tokens=False)[:12288]))'
    tokens = json.loads(command([str(VENV/'bin/python'), '-c', tokenizer], min(deadline, time.monotonic()+30), log))
    if len(tokens) != 12288 or any(type(x) is not int for x in tokens):
        raise ValueError('invalid fixed 12288 token prompt')
    request = {'model': SETTINGS['model'], 'prompt': tokens, 'n': 1, 'stream': False, 'add_special_tokens': False, 'ignore_eos': True, 'max_tokens': SETTINGS['output_tokens'], 'return_token_ids': True, 'temperature': 0}
    write_json(run/'completion-request.json', request)
    start = time.monotonic()
    assert_listener_owned(child.pid)
    result = http_json('POST', '/v1/completions', request, min(deadline, start+90))
    elapsed = time.monotonic()-start
    write_json(run/'completion-response.json', result)
    state['completion_wall_seconds'] = elapsed
    validate_completion(result, elapsed)
