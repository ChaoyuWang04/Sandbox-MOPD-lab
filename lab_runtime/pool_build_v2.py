"""Offline, hash-bound unified200 assembly. Never executes source tasks."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .pool_manifest_v2 import validate_manifest
from .swe_tasks import render_swe_task, materialize_swe_task

def sha(data):
    return hashlib.sha256(data).hexdigest()

def encoded(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()

def read_tree(root, expected=None):
    root = Path(root)
    if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('missing or symlink source tree')
    files = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('symlink source')
        if p.is_file():
            files[p.relative_to(root).as_posix()] = p.read_bytes()
    if expected is not None and {k: sha(v) for k, v in files.items()} != expected:
        raise ValueError('source tree hash mismatch')
    return files

def pin_tb_image(raw, digest):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('invalid image digest')
    # Restrict replacement to the environment section, preserving all other bytes.
    sections = list(re.finditer(rb'(?m)^\[([^\]\r\n]+)\][^\r\n]*', raw))
    env = [m for m in sections if m[1] == b'environment']
    if len(env) != 1:
        raise ValueError('missing environment section')
    start = env[0].end()
    end = next((m.start() for m in sections if m.start() > start), len(raw))
    matches = list(re.finditer(rb'(?m)^\s*docker_image\s*=\s*([\"\x27])([^\"\x27\r\n]+)\1', raw[start:end]))
    if len(matches) != 1 or b'@' in matches[0][2]:
        raise ValueError('missing or already pinned image')
    m = matches[0]
    at = start + m.end(2)
    return raw[:at] + b'@' + digest.encode() + raw[at:]

def publish_json(path, obj):
    path = Path(path)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('symlink destination')
    data = encoded(obj)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('refusing conflicting existing manifest: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(data)
        temp = Path(f.name)
    try:
        os.link(temp, path)  # atomic exclusive publication
    finally:
        temp.unlink()

def build_pool(root, registration='configs/m1-build-inputs-v2.json'):
    root = Path(root)
    def read(name):
        return json.loads((root / name).read_bytes())
    inputs = read(registration)
    for name, digest in inputs['input_sha256'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('unsafe input path')
        if sha((root / name).read_bytes()) != digest:
            raise ValueError('registered input hash mismatch: ' + name)
    base = 'data/m1/v2/'
    research = base + 'research/'
    annotations = read(research + 'candidate200-annotations.json')
    for name, digest in annotations['input_sha256'].items():
        if sha((root / name).read_bytes()) != digest:
            raise ValueError('annotation input hash mismatch: ' + name)
    legacy = read('configs/m1-pool-manifest.json')
    old = {r['instance']: r for r in legacy['instances']}
    self_records = {r['instance']: r for r in read(base + 'self48/manifest.json')['instances']}
    env = read(research + 'smith52-full-envelope.json')
    decoded = gzip.decompress(base64.b64decode(env['gzip_b64']))
    if sha(decoded) != env['sha256']:
        raise ValueError('Smith decoded source hash mismatch')
    sources = {'swe-smith': {r['instance_id']: r for r in json.loads(decoded)['records']},
               'swe-gym': {r['instance_id']: r for r in read(research + 'gym50-frozen.json')['records']}}
    profiles = read('configs/m1-swe-profiles-v2.json')
    tb_hashes = {r['path']: r['sha256'] for r in read(base + 'tb50-source/source-manifest.json')['files']}
    for path, digest in tb_hashes.items():
        if sha((root / base / 'tb50-source' / path).read_bytes()) != digest:
            raise ValueError('TB upstream hash mismatch')
    records, prepared = [], []
    names = {'self': 'self', 'SWE-smith': 'swe-smith', 'SWE-Gym': 'swe-gym', 'Terminal-Bench-2': 'terminal-bench'}
    for annotation in annotations['records']:
        r = dict(annotation)
        source = names[r['source']]
        ident = r['source_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', ident) or '..' in ident:
            raise ValueError('unsafe task identity')
        r.update(source=source, id=ident, status='files_prepared_runtime_unvalidated',
                 resource_profile=dict(r['resources']), frozen_legacy=ident in old,
                 legacy_eval=ident in old and old[ident]['split'] == 'eval', license_details=r['license'])
        r['license'] = r['license_details'].get('spdx') or ('self-authored-license-undeclared' if source == 'self' else 'TB-upstream-license-undeclared-internal-only')
        if source == 'self':
            item = self_records[ident]
            files = read_tree(root / base / 'self48' / item['path'], item['file_sha256'])
            r['source_revision'] = 'f15df30b6a46258e8ef88530198988c41eba444d'
            r['template_groups'] = r['function_groups']
            r['function_groups'] = []
            if ident in old:
                r['leakage_group'] = old[ident]['family']
            name = ident
        elif source in sources:
            short = 'smith' if source == 'swe-smith' else 'gym'
            rendered = render_swe_task(sources[source][ident], profiles[short][ident], short, {})
            files, metadata = rendered['files'], rendered['metadata']
            name = metadata['task_name']
            r['packaging'] = metadata
        else:
            expected = {k[len(ident)+1:]: v for k, v in tb_hashes.items() if k.startswith(ident + '/')}
            files = read_tree(root / base / 'tb50-source' / ident, expected)
            r['upstream_task_files_sha256'] = expected
            files['task.toml'] = pin_tb_image(files['task.toml'], r['container_registry_evidence']['digest'])
            r['packaging_changes'] = ['task.toml: append exact registry digest to environment.docker_image only']
            r['source_asset_sha256'] = 'afcd2b9813fc08e8d2b955db8165cc057acf0e37f74e55d26fc2506da196cebe'
            name = ident
        r['source_sha256'] = r['source_asset_sha256']
        r['task_path'] = base + 'tasks/' + name
        r['task_files_sha256'] = {k: sha(v) for k, v in sorted(files.items())}
        r['missing_fields'] = [x for x in r.get('missing_fields', []) if x not in ('source_revision', 'source_asset_sha256')]
        records.append(r)
        prepared.append((r['task_path'], files))
    counts = validate_manifest(records, legacy)
    if len({p for p, _ in prepared}) != 200:
        raise ValueError('duplicate task directory')
    # Preflight all existing destinations before creating any task.
    for path, files in prepared:
        if (root / path).exists() and read_tree(root / path) != files:
            raise ValueError('foreign existing task: ' + path)
    for path, files in prepared:
        materialize_swe_task(root / path, {'files': files, 'metadata': {'file_sha256': {k: sha(v) for k, v in files.items()}}})
    from harbor.models.task.task import Task
    for path, files in prepared:
        Task(root / path)
        read_tree(root / path, {k: sha(v) for k, v in files.items()})
    provenance = root / base / 'provenance/swe-licenses-complete.json'
    publish_json(provenance, read(research + 'swe-licenses-complete.json'))
    result = dict(schema_version=2, status='files_prepared_runtime_unvalidated', counts=counts,
                  input_registration=registration, input_sha256=inputs['input_sha256'], records=records,
                  harbor_tasks_loaded=200, limitations=[x for x in annotations['limitations']
                      if x != 'task_files_sha256 deliberately absent until assembler hashes actual files'],
                  license_artifact=str(provenance.relative_to(root)))
    publish_json(root / 'configs/m1-pool-v2.json', result)
    return result
