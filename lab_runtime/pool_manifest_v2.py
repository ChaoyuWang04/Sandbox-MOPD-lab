"""Offline unified200 metadata checks; does not select tasks or enforce runtime limits.

Hashes here are declarations, except that frozen declarations must equal the
explicit legacy reference. No task files, containers, licenses or source claims
are inspected. A successful result is only ``metadata_validated``.
"""
from collections import Counter, defaultdict
import math
from pathlib import PurePosixPath
import re


SOURCE_QUOTAS = {
    'self': {'A': 24, 'B': 24},
    'swe-smith': {'A': 26, 'B': 26},
    'swe-gym': {'A': 20, 'B': 20, 'combo': 10},
    'terminal-bench': {'A': 10, 'B': 10, 'combo': 10, 'OOD': 20},
}
SPLIT_QUOTAS = {'train': {'A': 40, 'B': 40}, 'dev': {'A': 10, 'B': 10},
                'final': {'A': 30, 'B': 30, 'combo': 20, 'OOD': 20}}
ROUTES = {'A': 'A', 'B': 'B', 'combo': 'both', 'OOD': 'none'}
LEGACY_SELF_SKILLS = {'fs_logs': 'A', 'data_csv': 'A', 'data_json': 'A', 'fs_inventory': 'B'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _hashes(value):
    _require(isinstance(value, dict) and bool(value), 'task file hashes must be a nonempty map')
    for path, digest in value.items():
        _require(_text(path) and not PurePosixPath(path).is_absolute()
                 and '..' not in PurePosixPath(path).parts and '\\' not in path,
                 'task file hash paths must be relative and safe')
        _require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'invalid SHA256')


def _legacy_index(manifest):
    _require(isinstance(manifest, dict) and isinstance(manifest.get('instances'), list),
             'legacy manifest must contain instances')
    instances = manifest['instances']
    _require(len(instances) == 32, 'legacy reference must contain exactly 32 instances')
    index = {}
    for old in instances:
        _require(isinstance(old, dict) and _text(old.get('instance'))
                 and _text(old.get('family')) and old.get('split') in ('train', 'eval'),
                 'invalid legacy reference record')
        _require(old['instance'] not in index, 'duplicate legacy ID')
        _hashes(old.get('file_sha256'))
        index[old['instance']] = old
    _require(Counter(x['split'] for x in instances) == {'train': 16, 'eval': 16},
             'legacy reference must contain train16/eval16')
    return index


def validate_manifest(records, legacy_manifest):
    """Validate declarations, raising ValueError; return counts, never readiness.

    Resource profile units: cpu (cores), ram_mb, gpu (count, currently zero),
    network (boolean), timeout_sec. Groups must already use canonical global
    names; this function does not infer repository aliases. Only original
    template leakage groups of the exact frozen legacy records are exempted.
    Function-group collisions never receive that diagnostic exemption.
    """
    legacy = _legacy_index(legacy_manifest)
    _require(isinstance(records, list), 'records must be a list')
    seen = set()
    source_identities = set()
    sources = defaultdict(Counter)
    splits = defaultdict(Counter)
    leakage = defaultdict(list)
    functions = defaultdict(set)
    for record in records:
        _require(isinstance(record, dict), 'record must be an object')
        for key in ('id', 'source', 'source_id', 'source_revision', 'source_sha256',
                    'license', 'primary_skill', 'mechanism', 'generation_method',
                    'leakage_group', 'split', 'teacher_route', 'classification_reason'):
            _require(_text(record.get(key)), f'missing or invalid {key}')
        ident = record['id']
        _require(ident not in seen, f'duplicate ID: {ident}')
        seen.add(ident)
        _require(re.fullmatch('[0-9a-f]{64}', record['source_sha256']), 'invalid source SHA256')
        source, skill, split = record['source'], record['primary_skill'], record['split']
        identity = (source, record['source_id'])
        _require(identity not in source_identities, f'duplicate source identity: {identity}')
        source_identities.add(identity)
        _require(source in SOURCE_QUOTAS and skill in ROUTES and split in SPLIT_QUOTAS,
                 'invalid source, skill or split')
        _require(record['teacher_route'] == ROUTES[skill], 'teacher route mismatch')
        _require(not (source == 'terminal-bench' or skill in ('combo', 'OOD')) or split == 'final',
                 'terminal-bench, combo and OOD are final only')
        for key in ('secondary_skills', 'function_groups'):
            values = record.get(key)
            _require(isinstance(values, list) and all(_text(x) for x in values), f'invalid {key}')
            _require(len(values) == len(set(values)), f'duplicate {key}')
        _require(all(x in ROUTES for x in record['secondary_skills']), 'invalid secondary skill')
        _require('repo' in record and (record['repo'] is None or _text(record['repo'])), 'invalid repo')
        _require(source in ('self', 'terminal-bench') or _text(record['repo']), 'repository required')
        for key in ('frozen_legacy', 'legacy_eval'):
            _require(type(record.get(key)) is bool, f'invalid {key}')
        _hashes(record.get('task_files_sha256'))
        resource = record.get('resource_profile')
        _require(isinstance(resource, dict), 'resource_profile required')
        for key in ('cpu', 'ram_mb', 'timeout_sec'):
            value = resource.get(key)
            _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                     f'resource {key} must be positive and finite')
        _require(type(resource.get('gpu')) is int and resource['gpu'] == 0, 'only CPU profiles supported')
        _require(type(resource.get('network')) is bool, 'network must be explicit boolean')
        old = legacy.get(ident)
        if source == 'self':
            expected_skill = LEGACY_SELF_SKILLS.get(old['family']) if old is not None else 'B'
            _require(skill == expected_skill, 'self skill mapping mismatch')
        _require(record['frozen_legacy'] == (old is not None), 'frozen legacy identity mismatch')
        _require(record['legacy_eval'] == (old is not None and old['split'] == 'eval'), 'legacy eval identity mismatch')
        if old is not None:
            _require(source == 'self' and record['source_id'] == ident, 'legacy source identity changed')
            _require(record['task_files_sha256'] == old['file_sha256'], 'frozen legacy task hashes changed')
            _require(record['leakage_group'] == old['family'], 'legacy template group changed')
            _require(not record['legacy_eval'] or split == 'final', 'legacy eval must be final')
        sources[source][skill] += 1
        splits[split][skill] += 1
        leakage[record['leakage_group']].append(record)
        for group in record['function_groups']:
            functions[group].add(split)
    _require(set(legacy) <= seen, 'missing frozen legacy IDs')
    _require(dict(sources) == SOURCE_QUOTAS, 'source/skill quota mismatch')
    _require(dict(splits) == SPLIT_QUOTAS, 'split/skill quota mismatch')
    for group, members in leakage.items():
        if len({r['split'] for r in members}) <= 1:
            continue
        # Exclude only original eval siblings; all remaining original train
        # siblings must still occupy a single split. No new record may join.
        _require(all(r['id'] in legacy and legacy[r['id']]['family'] == group for r in members)
                 and len({r['split'] for r in members if not r['legacy_eval']}) <= 1,
                 f'cross-split leakage group: {group}')
    _require(all(len(s) <= 1 for s in functions.values()), 'cross-split function group')
    return {'metadata_validated': True, 'total': len(records),
            'source_skill': {k: dict(v) for k, v in sources.items()},
            'split_skill': {k: dict(v) for k, v in splits.items()},
            'frozen_legacy_count': len(legacy), 'legacy_eval_count': 16}
