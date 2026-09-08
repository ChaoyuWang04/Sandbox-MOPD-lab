"""Self48 assets: immutable v1 plus sixteen stdlib runtime repair tasks.

The new tasks grade executed behavior, not an answer file. Private checks and
reference repair sources never enter the environment image. This is normal
phase isolation, not a security boundary against hostile submitted Python.
"""
import json
from pathlib import Path
import textwrap

from lab_runtime.task_pool import build_pool, _digest, _json


REPAIRS = {
    'config_precedence': '''
import copy
def resolve(defaults, file_values, env, cli):
    result = copy.deepcopy(defaults)
    for source in (file_values, env, cli):
        for key, value in source.items():
            if value is not None:
                result[key] = copy.deepcopy(value)
    return result
''',
    'resource_lifetime': '''
from contextlib import contextmanager
@contextmanager
def session(factory):
    resource = factory()
    try:
        yield resource
    finally:
        resource.close()
''',
    'async_dependencies': '''
import asyncio
async def execute(graph, run):
    pending = dict(graph)
    results = {}
    while pending:
        ready = sorted(name for name, deps in pending.items() if all(d in results for d in deps))
        if not ready:
            raise ValueError('cycle or missing dependency')
        values = await asyncio.gather(*(run(name, {d: results[d] for d in pending[name]}) for name in ready))
        results.update(zip(ready, values))
        for name in ready:
            del pending[name]
    return results
''',
    'atomic_replace': '''
import os
from pathlib import Path
import tempfile
def publish(destination, chunks):
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            for chunk in chunks:
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
''',
}

BROKEN = {
    'config_precedence': '''def resolve(defaults, file_values, env, cli):
    defaults.update(cli)
    defaults.update(env)
    defaults.update(file_values)
    return defaults
''',
    'resource_lifetime': '''from contextlib import contextmanager
@contextmanager
def session(factory):
    resource = factory()
    yield resource
    resource.close()
''',
    'async_dependencies': '''async def execute(graph, run):
    result = {}
    for name, deps in graph.items():
        result[name] = await run(name, result)
    return result
''',
    'atomic_replace': '''def publish(destination, chunks):
    with open(destination, 'wb') as handle:
        for chunk in chunks:
            handle.write(chunk)
''',
}

# Intentionally incomplete plausible fixes, exercised on all sixteen instances.
WRONG_REPAIRS = {
    'config_precedence': '''def resolve(defaults, file_values, env, cli):
    return {**defaults, **file_values, **env, **cli}
''',
    'resource_lifetime': '''from contextlib import contextmanager
@contextmanager
def session(factory):
    resource = factory()
    try:
        yield resource
    except Exception:
        resource.close()
''',
    'async_dependencies': '''async def execute(graph, run):
    result = {}
    pending = dict(graph)
    while pending:
        ready = sorted(n for n, deps in pending.items() if all(d in result for d in deps))
        if not ready:
            return result
        for n in ready:
            result[n] = await run(n, {d: result[d] for d in pending.pop(n)})
    return result
''',
    'atomic_replace': '''from pathlib import Path
def publish(destination, chunks):
    destination = Path(destination)
    temporary = destination.with_suffix('.tmp')
    with temporary.open('wb') as handle:
        for chunk in chunks:
            handle.write(chunk)
    temporary.replace(destination)
''',
}

CONTRACTS = {
    'config_precedence': 'Repair resolve(defaults, file_values, env, cli). Merge flat mappings in that ascending precedence order. None means absent at each layer; false, zero, and empty strings are explicit overrides. Return a deep independent copy, preserving unknown keys and nested values without mutating any input.',
    'resource_lifetime': 'Repair the session(factory) context manager. Acquire exactly once, yield that same object, and call close exactly once on both normal exit and any BaseException from the body. Propagate the original body exception when close succeeds. If acquisition fails do not close anything. Nested sessions must close in reverse acquisition order. close is assumed not to raise.',
    'async_dependencies': 'Repair async execute(graph, run). graph maps node names to dependency-name lists. Execute each node once only after all its dependencies finish; pass run(name, dependency_results) exactly its direct dependencies. Return all node results. Any cycle or missing dependency must raise ValueError; empty graph returns {}. Valid graphs must complete. run is async and may yield; propagate callback errors. Concurrent execution is allowed but not required.',
    'atomic_replace': 'Repair publish(destination, chunks). Atomically replace an existing regular file with concatenated bytes from an iterable. Use a temporary file in the destination directory and atomic replacement; readers during iteration must still see the entire old content. If iteration raises, propagate that exception and preserve old bytes. On success or failure remove your temporary files, preserve unrelated siblings. No symlink destinations or failing filesystem operations are required.',
}

CHECKS = {
    'config_precedence': '''
import copy
key = spec['key']
d = {key: 7, 'nested': {'items': [1]}, 'keep': 9}
f = {key: 8, 'zero': 4}
e = {key: 0, 'zero': 0, 'flag': False, 'text': ''}
c = {key: None, 'extra': spec['value']}
snapshot = copy.deepcopy([d, f, e, c])
actual = target.resolve(d, f, e, c)
expected = {key: 0, 'nested': {'items': [1]}, 'keep': 9, 'zero': 0, 'flag': False, 'text': '', 'extra': spec['value']}
assert type(actual) is dict and actual == expected
assert type(actual['zero']) is int and type(actual['flag']) is bool
assert [d, f, e, c] == snapshot
actual['nested']['items'].append(2)
assert [d, f, e, c] == snapshot
assert target.resolve({key: 1}, {key: 2}, {key: 3}, {key: 4}) == {key: 4}
assert target.resolve({}, {}, {}, {}) == {}
''',
    'resource_lifetime': '''
events = []
class Resource:
    def __init__(self, name):
        self.name = name
        events.append(('open', name))
    def close(self):
        events.append(('close', self.name))
name = spec['key']
with target.session(lambda: Resource(name)) as outer:
    assert outer.name == name
    with target.session(lambda: Resource('inner')) as inner:
        assert inner.name == 'inner'
assert events == [('open', name), ('open', 'inner'), ('close', 'inner'), ('close', name)]
class Stop(BaseException):
    pass
for error in (ValueError('body'), Stop('cancel')):
    events.clear()
    try:
        with target.session(lambda: Resource(name)):
            raise error
    except BaseException as caught:
        assert caught is error
    else:
        raise AssertionError('body error swallowed')
    assert events == [('open', name), ('close', name)]
events.clear()
def unavailable():
    events.append('acquire')
    raise LookupError('no resource')
try:
    with target.session(unavailable):
        raise AssertionError('must not enter')
except LookupError:
    pass
assert events == ['acquire']
''',
    'async_dependencies': '''
import asyncio
async def checks():
    name = spec['key']
    graph = {'end': [name, 'branch'], 'branch': ['start'], name: ['start'], 'start': []}
    done = {}
    async def run(node, dependencies):
        assert set(dependencies) == set(graph[node])
        assert all(d in done and dependencies[d] == done[d] for d in graph[node])
        assert node not in done
        await asyncio.sleep(0)
        done[node] = spec['value'] + sum(dependencies.values())
        return done[node]
    result = await target.execute(graph, run)
    unit = spec['value']
    assert result == {'start': unit, name: 2*unit, 'branch': 2*unit, 'end': 5*unit}
    assert await target.execute({}, run) == {}
    for invalid in ({'a': ['b'], 'b': ['a']}, {'a': ['missing']}):
        try:
            await target.execute(invalid, run)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid graph accepted')
    error = RuntimeError('callback')
    async def failed(node, dependencies):
        raise error
    try:
        await target.execute({'x': []}, failed)
    except RuntimeError as caught:
        assert caught is error
    else:
        raise AssertionError('callback failure swallowed')
asyncio.run(checks())
''',
    'atomic_replace': '''
import tempfile
with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    destination = base/(spec['key'] + '.bin')
    old = b'old-state'
    destination.write_bytes(old)
    sibling = base/'unrelated'
    sibling.write_bytes(b'keep')
    initial = {p.name for p in base.iterdir()}
    error = RuntimeError('stream failed')
    def failing():
        yield b'partial'
        assert destination.read_bytes() == old, 'premature publication'
        raise error
    try:
        target.publish(destination, failing())
    except RuntimeError as caught:
        assert caught is error
    else:
        raise AssertionError('stream failure swallowed')
    assert destination.read_bytes() == old
    assert {p.name for p in base.iterdir()} == initial
    def chunks():
        for chunk in (b'new', bytes([spec['value']]), b'\\x00end'):
            assert destination.read_bytes() == old
            yield chunk
    target.publish(destination, chunks())
    assert destination.read_bytes() == b'new' + bytes([spec['value']]) + b'\\x00end'
    assert sibling.read_bytes() == b'keep'
    assert {p.name for p in base.iterdir()} == initial
    target.publish(destination, iter(()))
    assert destination.read_bytes() == b''
    assert {p.name for p in base.iterdir()} == initial
''',
}

VERIFY = '''
import hashlib
import json
from pathlib import Path
import subprocess
import sys
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/app')
rewards = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/logs/verifier')
private = Path(__file__).parent
passed = False
try:
    expected = json.loads((private/'expected.json').read_text())
    base = root/'input'
    assert base.is_dir() and not base.is_symlink()
    actual = {}
    for path in base.rglob('*'):
        assert not path.is_symlink()
        if path.is_file():
            assert path.stat().st_size <= 65536
            actual[path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            assert path.is_dir()
    assert actual == expected
    code = root/'work/target.py'
    assert not (root/'work').is_symlink() and not code.is_symlink()
    assert code.is_file() and code.stat().st_size <= 65536
    checked = subprocess.run([sys.executable, '-I', '-B', str(private/'check.py'), str(root)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    passed = checked.returncode == 0
except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired):
    pass
rewards.mkdir(parents=True, exist_ok=True)
(rewards/'reward.txt').write_text('1\\n' if passed else '0\\n')
(rewards/'result.json').write_text(json.dumps({'passed': passed})+'\\n')
'''


def _files(family, instance, variant):
    spec = _json({'key': f'worker_{variant}', 'value': 11 + variant})
    check = '''import importlib.util
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
spec = json.loads((root/'input/spec.json').read_text())
module_spec = importlib.util.spec_from_file_location('target', root/'work/target.py')
target = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(target)
'''+textwrap.dedent(CHECKS[family]).lstrip()
    oracle = "from pathlib import Path\nimport sys\nroot = Path(sys.argv[1]) if len(sys.argv)>1 else Path('/app')\n"
    oracle += f"(root/'work/target.py').write_text({textwrap.dedent(REPAIRS[family]).lstrip()!r})\n"
    texts = {
        'instruction.md': f'# {instance}\n\n'+CONTRACTS[family]+'\n\nEdit /app/work/target.py. The defective implementation is visible there. '
                          'Input /app/input/spec.json is immutable instance context. Do not change input files. '
                          'The verifier imports your repaired module and executes additional behavioral cases. '
                          'No result.json answer is required. Python 3.12 stdlib only; no network or installation.\n',
        'environment/input/spec.json': spec,
        'environment/work/target.py': BROKEN[family],
        'environment/Dockerfile': 'FROM python:3.12.13-slim\nWORKDIR /app\nCOPY input/ /app/input/\nCOPY work/ /app/work/\n',
        'task.toml': f'''schema_version = "1.4"
[task]
name = "sandbox-mopd/{instance}"
version = "2.0.0"
authors = []
keywords = ["m1", "runtime-repair", "{family}"]
[metadata]
difficulty = "medium"
category = "software-engineering"
tags = ["stdlib", "deterministic", "repair"]
[verifier]
timeout_sec = 30.0
[agent]
timeout_sec = 180.0
[environment]
build_timeout_sec = 120.0
cpus = 1
memory_mb = 1024
storage_mb = 3072
gpus = 0
''',
        'solution/oracle.py': oracle,
        'solution/solve.sh': '#!/bin/sh\nset -eu\nexec python3 "$(dirname "$0")/oracle.py" "${1:-/app}"\n',
        'tests/verify.py': textwrap.dedent(VERIFY).lstrip(),
        'tests/check.py': check,
        'tests/expected.json': _json({'spec.json': _digest(spec.encode())}),
        'tests/test.sh': '#!/bin/sh\nset -eu\nexec python3 "$(dirname "$0")/verify.py" "${1:-/app}" "${2:-/logs/verifier}"\n',
    }
    return {name: content.encode() for name, content in texts.items()}


def materialize_self_pool(output_root: Path) -> dict:
    """Write tasks/ plus manifest.json to a new/empty nonsymlink root.

    Original row split fields are preserved. New rows only propose train and
    identify their family leakage group; the unified pool assigns final splits.
    """
    output = Path(output_root).absolute()
    manifest = build_pool(output)
    manifest['schema_version'] = 2
    manifest['pool'] = 'm1-self48-v2'
    manifest['split_policy'] = 'Preserve v1 assignments; new families provisional train, unified split pending.'
    for family in REPAIRS:
        for variant in range(4):
            instance = f'self-v2-{family}-{variant:02d}'
            relative = f'tasks/{instance}'
            files = _files(family, instance, variant)
            for name, content in sorted(files.items()):
                path = output/relative/name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as handle:
                    handle.write(content)
                if path.suffix == '.sh':
                    path.chmod(0o755)
            manifest['instances'].append(dict(instance=instance, family=family, domain='B',
                source='self-authored', generation_method='deterministic-runtime-repair-template',
                mechanism=family, primary_capability='system-software-runtime',
                secondary_capability='code-repair', provisional_split='train',
                leakage_group=f'self-v2/{family}', path=relative, variant=variant,
                resources=dict(cpus=1, memory_mb=1024, storage_mb=3072, gpus=0,
                               network=False, build_timeout_sec=120, agent_timeout_sec=180,
                               verifier_timeout_sec=30),
                classification_reason=CONTRACTS[family],
                file_sha256={name: _digest(data) for name, data in sorted(files.items())}))
    (output/'manifest.json').write_text(_json(manifest))
    return manifest
