"""Deterministic Harbor SWE packaging; no source code runs during rendering."""
import hashlib
import json
from pathlib import Path
import re
import shlex

from .swe_verify import patch_paths, safe_path


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + '\n').encode()


def render_swe_task(record, profile, source, resource_profile):
    """Return files and metadata after binding record to its exact profile ID.

    Preparation receipts and Smith restore patches are deliberately absent:
    the pre-agent controller must derive these from the actual pinned image.
    """
    if source not in ('smith', 'gym'):
        raise ValueError('unsupported source')
    identity = record['instance_id']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,239}', identity) or '..' in identity:
        raise ValueError('unsafe source ID')
    if profile.get('instance_id') != identity:
        raise ValueError('source/profile instance identity mismatch')
    for key in ('repo', 'base_commit', 'version') if source == 'gym' else ('repo', 'image_name'):
        if record.get(key) != profile.get(key) or not record.get(key):
            raise ValueError('source/profile identity mismatch: ' + key)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', profile['repo']):
        raise ValueError('unsafe repository')
    image = profile['image_name']
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/-]*', image):
        raise ValueError('unsafe image name')
    digest = profile['image_digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('image must have exact digest')
    if not isinstance(record['problem_statement'], str) or not record['problem_statement'].strip():
        raise ValueError('empty original problem')
    gold_paths = patch_paths(record['patch'])
    tests = record.get('test_patch', '') if source == 'gym' else ''
    test_paths = patch_paths(tests) if tests else []
    if set(gold_paths) & set(test_paths):
        raise ValueError('gold and test paths overlap')
    if not profile.get('test_command'):
        raise ValueError('missing frozen test command')
    limits = dict(cpus=4, memory_mb=8192, storage_mb=10240, build_timeout_sec=600,
                  agent_timeout_sec=900, verifier_timeout_sec=1800)
    if set(resource_profile) - set(limits):
        raise ValueError('unknown resource setting')
    limits.update(resource_profile)
    if any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ValueError('resource settings must be positive integers')
    if limits['cpus'] > 4 or limits['memory_mb'] > 8192 or limits['storage_mb'] > 10240 or limits['build_timeout_sec'] > 600:
        raise ValueError('resource scope exceeded')
    if sum(limits[k] for k in ('build_timeout_sec', 'agent_timeout_sec', 'verifier_timeout_sec')) > 3600:
        raise ValueError('pilot duration exceeded')
    private = dict(source=source, source_id=identity, profile=profile,
                   parser_identity=profile['parser_identity'], gold_patch_paths=gold_paths,
                   test_patch_paths=test_paths, timeout=limits['verifier_timeout_sec'])
    for original, key in [('FAIL_TO_PASS', 'fail_to_pass'), ('PASS_TO_PASS', 'pass_to_pass')]:
        value = record[original]
        value = json.loads(value) if isinstance(value, str) else value
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise ValueError('invalid test identity list')
        private[key] = value
    shortname = 'swe-' + source + '-' + hashlib.sha256(identity.encode()).hexdigest()[:20]
    docker = 'FROM ' + image + '@' + digest + '\nWORKDIR /testbed\nRUN mkdir -p /logs/verifier\n'
    if source == 'smith':
        commit = profile.get('instance_commit')
        if commit is not None and not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('invalid instance commit')
        ref = commit or 'refs/heads/' + identity
        docker += ('RUN git fetch origin ' + shlex.quote(ref) + ' && git checkout --detach FETCH_HEAD\n')
    reverse = ' --reverse' if source == 'smith' else ''
    files = {
        'instruction.md': record['problem_statement'].encode(),
        'environment/Dockerfile': docker.encode(),
        'solution/gold.patch': record['patch'].encode(),
        'solution/solve.sh': ('#!/bin/bash\nset -euo pipefail\ncd /testbed\n'
            'git apply --check' + reverse + ' /solution/gold.patch\n'
            'git apply' + reverse + ' /solution/gold.patch\n').encode(),
        'tests/private.json': _json(private),
        'tests/test.sh': b'#!/bin/sh\nset -eu\ncd /tests\nexec python3 -I -c "import sys; sys.path.insert(0, \'/tests\'); from lab_runtime.swe_verify import main; main()"\n',
        'tests/lab_runtime/__init__.py': b'',
    }
    if source == 'gym':
        files['tests/test.patch'] = tests.encode()
    for module in ('swe_verify.py', 'swe_grading.py', 'swe_test_runner.py', 'swe_pytest_plugin.py'):
        files['tests/lab_runtime/' + module] = Path(__file__).with_name(module).read_bytes()
    files['task.toml'] = ('''schema_version = "1.4"
[task]
name = "sandbox-mopd/''' + shortname + '''"
version = "1.0.0"
authors = []
keywords = ["m1", "swe"]
[metadata]
category = "software-engineering"
[verifier]
timeout_sec = ''' + str(limits['verifier_timeout_sec']) + '''
[agent]
timeout_sec = ''' + str(limits['agent_timeout_sec']) + '''
[environment]
gpus = 0
''' + ''.join(k + ' = ' + str(limits[k]) + '\n' for k in
             ('cpus', 'memory_mb', 'storage_mb', 'build_timeout_sec'))).encode()
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}
    metadata = dict(source=source, source_id=identity, task_name=shortname, file_sha256=hashes,
                    record_sha256=hashlib.sha256(_json(record)).hexdigest(),
                    profile_sha256=hashlib.sha256(_json(profile)).hexdigest(), resources=limits)
    metadata['content_sha256'] = hashlib.sha256(_json(hashes)).hexdigest()
    return dict(files=files, metadata=metadata)


def materialize_swe_task(destination, rendered):
    """Exclusive initial creation; an existing tree must match every byte."""
    root = Path(destination)
    for parent in (root,) + tuple(root.parents):
        if parent.is_symlink():
            raise ValueError('symlink destination')
    files = rendered['files']
    for name, content in files.items():
        safe_path(name)
        if hashlib.sha256(content).hexdigest() != rendered['metadata']['file_sha256'][name]:
            raise ValueError('content hash mismatch')
    if root.exists():
        actual = {}
        for item in root.rglob('*'):
            if item.is_symlink():
                raise ValueError('symlink in existing task')
            if item.is_file():
                actual[item.relative_to(root).as_posix()] = item.read_bytes()
        if actual != files:
            raise ValueError('existing task differs or contains foreign files')
        return rendered['metadata']
    root.mkdir(parents=True, exist_ok=False)
    for name, content in sorted(files.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
        if name.endswith('.sh'):
            path.chmod(0o755)
    return rendered['metadata']
