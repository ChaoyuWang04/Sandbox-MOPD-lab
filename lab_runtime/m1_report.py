"""Audit an explicit set of M1 summaries; never hide duplicate attempts.

Summary checks are necessary, not sufficient: raw trace and provider witnesses
must be inspected before declaring M1 complete.
"""
from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from .home5090 import SETTINGS
from .m2_contract import classify_verifier

SOURCE = Path(__file__).resolve().parents[1]
RUNTIME_LIMITS = {'max_model_len': 16384, 'output_tokens': 4096, 'rounds': 10, 'tool_seconds': 20, 'agent_seconds': 180}


def _v2_manifest_index(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get('records'), list):
        raise ValueError('v2 manifest records required')
    index = {}
    for row in manifest['records']:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError('invalid v2 manifest record')
        if row['id'] in index:
            raise ValueError('duplicate v2 manifest task')
        index[row['id']] = row
    return index


def select_overfit16_v2(manifest, screen_records, config):
    """Select a frozen A8+B8 prefix from complete, mixed within-task groups."""
    index = _v2_manifest_index(manifest)
    if not isinstance(config, dict) or config.get('schema_version') != 1:
        raise ValueError('unsupported M1 close config')
    candidates = config.get('candidate_task_ids')
    if (not isinstance(candidates, list) or not candidates
            or len(candidates) != len(set(candidates))
            or any(not isinstance(task_id, str) or not task_id for task_id in candidates)):
        raise ValueError('candidate task IDs must be unique nonempty strings')
    group_size = config.get('group_size')
    if type(group_size) is not int or group_size < 2:
        raise ValueError('group size must be at least two')
    if config.get('temperature') != 1.0 or config.get('top_p') != 1.0:
        raise ValueError('unregistered screen sampling')
    base_seed = config.get('seed')
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError('invalid screen seed')
    quotas = config.get('select_per_skill')
    if (not isinstance(quotas, dict) or set(quotas) != {'A', 'B'}
            or any(type(value) is not int or value < 1 for value in quotas.values())):
        raise ValueError('A/B selection quotas required')
    for task_id in candidates:
        row = index.get(task_id)
        if row is None or row.get('split') != 'train' or row.get('primary_skill') not in quotas:
            raise ValueError('selection candidates must be registered A/B train tasks')

    if not isinstance(screen_records, list):
        raise ValueError('screen records must be a list')
    groups = {task_id: [] for task_id in candidates}
    seen = set()
    invalid_attempts = 0
    candidate_position = {task_id: position for position, task_id in enumerate(candidates)}
    for record in screen_records:
        if not isinstance(record, dict):
            raise ValueError('screen record must be an object')
        task_id = record.get('task_id')
        repetition = record.get('repetition_id')
        if task_id not in groups or type(repetition) is not int or not 0 <= repetition < group_size:
            raise ValueError('unexpected screen attempt')
        key = (task_id, repetition)
        if key in seen:
            raise ValueError('duplicate screen attempt')
        seen.add(key)
        expected_seed = base_seed + candidate_position[task_id] * group_size + repetition
        if (record.get('group_id') != f'm1-close/{task_id}'
                or record.get('seed') != expected_seed
                or record.get('temperature') != config['temperature']
                or record.get('top_p') != config['top_p']
                or record.get('task_files_sha256') != index[task_id].get('task_files_sha256')):
            raise ValueError('screen attempt identity mismatch')
        outcome = classify_verifier(record)
        if outcome['classification'] == 'invalid':
            invalid_attempts += 1
        else:
            groups[task_id].append(outcome['reward'])
    expected = {(task_id, repetition) for task_id in candidates for repetition in range(group_size)}
    if seen != expected:
        raise ValueError('screen attempt set is incomplete')

    mixed = [task_id for task_id in candidates
             if len(groups[task_id]) >= 2 and set(groups[task_id]) == {0, 1}]
    selection = []
    for skill in ('A', 'B'):
        eligible = [task_id for task_id in mixed if index[task_id]['primary_skill'] == skill]
        selection.extend(eligible[:quotas[skill]])
    ready = len(selection) == sum(quotas.values())
    return {'schema_version': 1, 'selection_ready': ready,
            'overfit_16': selection if ready else [], 'mixed_group_count': len(mixed),
            'invalid_attempts': invalid_attempts,
            'group_status': {task_id: ('mixed' if task_id in mixed else
                'insufficient_valid' if len(groups[task_id]) < 2 else 'zero_variance')
                for task_id in candidates}}


def validate_tb_representative(manifest, task_id, records):
    """Validate one Terminal-Bench NOP/oracle lifecycle without extrapolation."""
    row = _v2_manifest_index(manifest).get(task_id)
    if row is None or row.get('source') != 'terminal-bench' or row.get('split') != 'final':
        raise ValueError('representative must be a registered final Terminal-Bench task')
    if not isinstance(records, list) or len(records) != 2:
        raise ValueError('exactly two representative controls required')
    by_agent = {}
    for record in records:
        if not isinstance(record, dict) or record.get('agent') not in ('nop', 'oracle'):
            raise ValueError('unexpected representative control')
        agent = record['agent']
        if agent in by_agent:
            raise ValueError('duplicate representative control')
        if (record.get('task_id') != task_id
                or record.get('task_files_sha256') != row.get('task_files_sha256')
                or record.get('verifier_complete') is not True
                or record.get('private_files_absent_during_agent') is not True
                or record.get('cleanup_confirmed_empty') is not True
                or record.get('exception_type') is not None):
            raise ValueError('representative lifecycle or identity mismatch')
        outcome = classify_verifier(record)
        if outcome['classification'] != 'valid_reward' or outcome['reward'] != (agent == 'oracle'):
            raise ValueError('representative reward pole mismatch')
        by_agent[agent] = record
    if set(by_agent) != {'nop', 'oracle'}:
        raise ValueError('NOP and oracle controls required')
    return {'task_id': task_id, 'status': 'representative_control_passed'}


def reward(record):
    value = (record.get('rewards') or {}).get('reward')
    return value if type(value) in (int, float) and value in (0, 1) else None


def valid(record):
    return (record.get('valid_for_denominator') is True
            and not record.get('exception_type') and not record.get('exception_info_type')
            and reward(record) is not None
            and record.get('cleanup', {}).get('confirmed_empty') is True)


def complete_usage(record):
    usage = record.get('usage')
    return isinstance(usage, dict) and usage.get('complete') is True and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input', 'output'))


def stats(records):
    clean = [r for r in records if valid(r)]
    successful = sum(reward(r) == 1 for r in clean)
    wall = [r['total_wall_seconds'] for r in records if type(r.get('total_wall_seconds')) in (int, float) and math.isfinite(r['total_wall_seconds']) and r['total_wall_seconds'] >= 0]
    costs = [(r.get('cost') or {}).get('estimated_usd') for r in records]
    known_costs = [value for value in costs if type(value) in (int, float) and math.isfinite(value) and value >= 0]
    return {'attempts': len(records), 'valid_attempts': len(clean), 'successes': successful,
            'model_failures': sum(reward(r) == 0 for r in clean),
            'invalid_attempts': len(records)-len(clean),
            'pass_at_1': successful/len(clean) if clean else None,
            'success_fraction_all_attempts': successful/len(records) if records else None,
            'termination_counts': dict(Counter(r.get('termination') or 'unknown' for r in records)),
            'wall_seconds': {'measured_attempts': len(wall), 'sum': sum(wall) if wall else None, 'median': statistics.median(wall) if wall else None},
            'cost_estimate': {'known_usd': sum(known_costs) if known_costs else None, 'unknown_attempts': len(records)-len(known_costs), 'billing_verified': False},
            'input_tokens_known': sum(r['usage']['input'] for r in records if complete_usage(r)),
            'output_tokens_known': sum(r['usage']['output'] for r in records if complete_usage(r)),
            'usage_unknown_attempts': sum(not complete_usage(r) for r in records)}


def audit(manifest, summaries, expected_pool_sha):
    lock_sha = hashlib.sha256((SOURCE/'environments/home5090/uv.lock').read_bytes()).hexdigest()
    agent_sha = hashlib.sha256((SOURCE/'lab_runtime/m1_agent.py').read_bytes()).hexdigest()
    instances = {row['instance']: row for row in manifest['instances']}
    if len(instances) != len(manifest['instances']):
        raise ValueError('duplicate pool instance')
    families = {'fs_logs': 'FS', 'fs_inventory': 'FS', 'data_csv': 'DATA', 'data_json': 'DATA'}
    if len(instances) != 32 or any(row['split'] not in ('train','eval') or families.get(row['family']) != row['domain'] for row in instances.values()):
        raise ValueError('invalid frozen pool composition')
    if Counter((row['family'], row['split']) for row in instances.values()) != {(family, split): 4 for family in families for split in ('train', 'eval')}:
        raise ValueError('expected four instances per family/split')
    for split in ('train', 'eval'):
        if Counter(row['domain'] for row in instances.values() if row['split']==split) != {'FS': 8, 'DATA': 8}:
            raise ValueError('expected frozen 8 FS / 8 DATA per split')
    rows = {}
    all_rows = []
    duplicates = []
    agent_shas = set()
    cleanup_ok = bool(summaries)
    sources = []
    for summary in summaries:
        if summary.get('pool_manifest_sha256') != expected_pool_sha or summary.get('agent_source_sha256') != agent_sha:
            raise ValueError('summary identity mismatch')
        if summary.get('model_revision') != SETTINGS['revision'] or summary.get('serving_lock_sha256') != lock_sha or summary.get('runtime_limits') != RUNTIME_LIMITS:
            raise ValueError('runtime identity mismatch')
        agent_shas.add(summary['agent_source_sha256'])
        mode = summary['mode']
        if mode not in ('pilot', 'controls', 'screen', 'eval'):
            raise ValueError('unknown summary mode')
        cleanup_ok &= summary.get('cleanup_confirmed_empty') is True
        sources.append({'run_id': summary.get('run_id'), 'source_git_sha': summary.get('source_git_sha'), 'mode': mode})
        for record in summary['records']:
            identity = instances.get(record['instance'])
            if identity is None or any(record.get(k) != identity[k] for k in ('family', 'domain', 'split')):
                raise ValueError('record identity mismatch')
            expected_temperature = None if mode=='controls' else 0.6 if mode=='eval' else 1.0
            diagnostic = record.get('diagnostic_index', 0)
            if type(diagnostic) is not int or diagnostic not in ((0,1) if mode=='pilot' else (0,)):
                raise ValueError('diagnostic identity mismatch')
            if record.get('mode', mode) != mode or record.get('temperature') != expected_temperature or type(record.get('seed')) is not int or record['seed'] != identity['seed']*100+record['repetition']+diagnostic*100000000:
                raise ValueError('sampling identity mismatch')
            key = (mode, record['instance'], record['repetition'], record['agent'])
            if mode == 'pilot':
                key += (diagnostic,)
            if key in rows:
                duplicates.append(list(key))
            if type(record['repetition']) is not int or record['repetition'] < 0:
                raise ValueError('invalid repetition')
            rows.setdefault(key, record)
            all_rows.append((key, record))
    if len(agent_shas) > 1:
        raise ValueError('agent identity drift; do not pool incompatible baselines')
    expected = set()
    for instance, row in instances.items():
        expected.update(('controls', instance, 0, actor) for actor in ('nop', 'oracle'))
        mode, repetitions = ('screen', 8) if row['split']=='train' else ('eval', 3)
        expected.update((mode, instance, rep, 'm1') for rep in range(repetitions))
    formal = {key for key in rows if key[0] != 'pilot'}
    if formal-expected:
        raise ValueError('unexpected formal attempt')
    missing = sorted(expected-formal)
    controls_ok = all(valid(record) and reward(record) == (1 if key[3]=='oracle' else 0)
                      for key, record in all_rows if key[0]=='controls')
    train = [record for key, record in all_rows if key[0]=='screen']
    evaluation = [record for key, record in all_rows if key[0]=='eval']
    train_stats, eval_stats = stats(train), stats(evaluation)
    per_task = {name: stats([r for r in train if r['instance']==name]) for name, row in instances.items() if row['split']=='train'}
    mixed = train_stats['successes'] > 0 and train_stats['model_failures'] > 0
    usage_ok = train_stats['usage_unknown_attempts'] == eval_stats['usage_unknown_attempts'] == 0
    complete = not missing and cleanup_ok and controls_ok and mixed and usage_ok and not duplicates
    def control_supported(name, actor, expected_reward):
        attempts = [record for key, record in all_rows if key==('controls', name, 0, actor) and valid(record)]
        # Unknown infrastructure outcomes are reported separately. Contradictory
        # valid rewards cannot be hidden by choosing the first or best attempt.
        return bool(attempts) and all(reward(record)==expected_reward for record in attempts)
    eligible = [name for name in per_task if per_task[name]['valid_attempts'] > 0 and all(
        control_supported(name, actor, expected_reward) for actor, expected_reward in (('nop', 0), ('oracle', 1)))]
    infra = [{'mode': key[0], 'instance': key[1], 'repetition': key[2], 'agent': key[3],
              'exception_type': record.get('exception_type'), 'exception_info_type': record.get('exception_info_type')}
             for key, record in all_rows if not valid(record)]
    return {'schema_version': 1, 'scope': 'summary-matrix audit; raw traces and lifecycle evidence still require verification',
            'evidence_complete': complete, 'pool_manifest_sha256': expected_pool_sha,
            'summary_matrix_complete': not missing, 'duplicate_attempts': duplicates,
            'errors_requiring_attribution': infra, 'requires_error_review': bool(infra or duplicates),
            'agent_source_sha256': next(iter(agent_shas), None), 'sources': sources,
            'missing_attempts': [list(key) for key in missing], 'cleanup_confirmed': cleanup_ok,
            'controls_correct': controls_ok and not any(key[0]=='controls' for key in missing),
            'screen': train_stats, 'screen_per_task': per_task, 'eval_clean': eval_stats,
            'eval_by_domain': {domain: stats([r for r in evaluation if r['domain']==domain]) for domain in ('FS','DATA')},
            'overfit_16': sorted(eligible) if len(eligible)==16 and mixed else [],
            'eval_external': {'status': 'deferred', 'claim': 'no external validity result'},
            'statistical_limitation': '16 held-out instances from seen families, 3 samples each; not 48 independent tasks',
            'strict_matrix_policy': 'All attempts retained. Error attribution is a separate review; one infrastructure failure is not automatically a systemic blocker.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--summary', type=Path, action='append', required=True)
    args = parser.parse_args()
    content = args.manifest.read_bytes()
    result = audit(json.loads(content), [json.loads(path.read_text()) for path in args.summary], hashlib.sha256(content).hexdigest())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
