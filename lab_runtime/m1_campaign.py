"""Persistent M1 allocations and evidence gates.

Every mutating call runs under home5090_run.run_context's shared M0/M1 lock.
An active or uncertain previous batch requires explicit recovery, never reset.
Dollar reservations are accounting limits, not provider billing enforcement.
"""
import copy
import hashlib
import json
from pathlib import Path

from . import home5090_run as runtime

MAX_TRIALS = 264
MAX_PILOT = 8
MAX_BATCHES = 6
MAX_GPU_SECONDS = 21600
BUDGET_USD = 10
IDENTITY_KEYS = {'pool_manifest_sha256', 'agent_source_sha256', 'model_revision', 'serving_lock_sha256'}


class CampaignBlocked(ValueError):
    pass


def _counts(ledger):
    runs = ledger['runs']
    allocations = [(run['mode'], allocation) for run in runs for allocation in run['allocations']]
    reserved = sum(item['status'] == 'reserved' for _, item in allocations)
    attempted = sum(item['status'] == 'attempted' for _, item in allocations)
    return dict(allocated_trials=len(allocations), reserved_trials=reserved, attempted_trials=attempted,
                pilot_reserved=sum(mode == 'pilot' and item['status'] == 'reserved' for mode, item in allocations),
                pilot_attempted=sum(mode == 'pilot' and item['status'] == 'attempted' for mode, item in allocations),
                reserved_gpu_seconds=sum(run['reserved_gpu_seconds'] for run in runs),
                reserved_budget_usd=round((reserved+attempted)*BUDGET_USD/MAX_TRIALS, 8))


def _load(path, identity):
    runtime.safe_path(path)
    if set(identity) != IDENTITY_KEYS or any(not isinstance(value, str) or not value for value in identity.values()):
        raise CampaignBlocked('incomplete campaign identity')
    if not path.exists():
        return {'schema_version': 1, 'identity': dict(identity), 'runs': [],
                'unresolved_create': False, 'unresolved_cleanup': False,
                'limits': {'trials': MAX_TRIALS, 'pilot': MAX_PILOT, 'batches': MAX_BATCHES,
                           'gpu_seconds': MAX_GPU_SECONDS, 'budget_usd': BUDGET_USD},
                **_counts({'runs': []})}
    try:
        ledger = json.loads(path.read_text())
        if ledger['schema_version'] != 1 or ledger['identity'] != identity:
            raise CampaignBlocked('campaign identity/version mismatch; no automatic reset')
        if type(ledger['unresolved_create']) is not bool or type(ledger['unresolved_cleanup']) is not bool:
            raise CampaignBlocked('invalid campaign latch')
        expected_limits = {'trials': MAX_TRIALS, 'pilot': MAX_PILOT, 'batches': MAX_BATCHES,
                           'gpu_seconds': MAX_GPU_SECONDS, 'budget_usd': BUDGET_USD}
        if ledger['limits'] != expected_limits:
            raise CampaignBlocked('campaign limits changed')
        run_ids = set()
        for run in ledger['runs']:
            if run['run_id'] in run_ids or run['status'] not in ('active', 'finished') or run['mode'] not in ('pilot', 'controls', 'screen', 'eval'):
                raise CampaignBlocked('invalid campaign run history')
            run_ids.add(run['run_id'])
            if run['reserved_gpu_seconds'] != (0 if run['mode'] == 'controls' else 3600):
                raise CampaignBlocked('invalid campaign GPU reservation')
            for item in run['allocations']:
                if item['status'] not in ('reserved', 'attempted', 'released'):
                    raise CampaignBlocked('invalid allocation state')
            if run['status'] == 'finished':
                if not run.get('summary_path'):
                    raise CampaignBlocked('finished batch lacks durable evidence')
                _evidence(run, identity)
        if any(ledger.get(key) != value for key, value in _counts(ledger).items()):
            raise CampaignBlocked('campaign accounting mismatch')
        if (len(ledger['runs']) > MAX_BATCHES or ledger['attempted_trials']+ledger['reserved_trials'] > MAX_TRIALS
                or ledger['pilot_attempted']+ledger['pilot_reserved'] > MAX_PILOT
                or ledger['reserved_gpu_seconds'] > MAX_GPU_SECONDS or ledger['reserved_budget_usd'] > BUDGET_USD):
            raise CampaignBlocked('campaign limits exceeded')
        return ledger
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CampaignBlocked('unreadable or unknown campaign state') from None


def _save(path, ledger):
    ledger.update(_counts(ledger))
    runtime.write_json(path, ledger)


def _key(mode, plan):
    return (mode, plan['instance'], plan['repetition'], plan['agent'])


def _evidence(run, identity):
    if run['status'] != 'finished' or not run.get('summary_path'):
        return None
    path = runtime.safe_path(Path(run['summary_path']))
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != run['summary_sha256']:
            raise CampaignBlocked('campaign evidence changed after finalization')
        summary = json.loads(raw)
        if any(summary.get(key) != value for key, value in identity.items()):
            raise CampaignBlocked('campaign evidence identity mismatch')
        if summary.get('run_id') != run['run_id'] or summary.get('mode') != run['mode']:
            raise CampaignBlocked('campaign evidence run mismatch')
        return summary
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise CampaignBlocked('campaign evidence missing or invalid') from None


def _require_prerequisites(ledger):
    pilot_passed = False
    controls = {}
    frozen_path = runtime.SOURCE/'configs/m1-pool-manifest.json'
    raw = frozen_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ledger['identity']['pool_manifest_sha256']:
        raise CampaignBlocked('frozen task pool differs from campaign')
    expected_instances = {item['instance'] for item in json.loads(raw)['instances']}
    for run in ledger['runs']:
        summary = _evidence(run, ledger['identity'])
        if summary is None or summary.get('cleanup_confirmed_empty') is not True:
            continue
        records = summary.get('records', [])
        if run['mode'] == 'pilot':
            if (len(records) == 4 and summary.get('gpu_run') is True
                    and summary.get('gpu_cleanup_confirmed') is True
                    and summary.get('model_content_verified_after') is True
                    and summary.get('stopping_reason') == 'planned_slice_completed'
                    and len({row['family'] for row in records}) == 4
                    and all(row.get('valid_for_denominator') is True and row.get('usage', {}).get('complete') is True
                            and row.get('cleanup', {}).get('confirmed_empty') is True
                            and row.get('exception_type') is None and row.get('exception_info_type') is None for row in records)
                    and any(row.get('rewards', {}).get('reward') == 1 for row in records)):
                pilot_passed = True
        elif run['mode'] == 'controls':
            for row in records:
                key = (row['instance'], row['agent'])
                if key in controls:
                    raise CampaignBlocked('duplicate formal control evidence')
                controls[key] = (row.get('valid_for_denominator') is True
                    and row.get('exception_type') is None and row.get('exception_info_type') is None
                    and row.get('cleanup', {}).get('confirmed_empty') is True
                    and row.get('rewards', {}).get('reward') == (0 if row['agent'] == 'nop' else 1))
    if not pilot_passed or len(expected_instances) != 32 or not all(controls.get((name, agent)) is True for name in expected_instances for agent in ('nop', 'oracle')):
        raise CampaignBlocked('screen/eval requires a successful pilot and all 32 NOP0/oracle1 controls')


def reserve_run(path, identity, run_id, mode, plans):
    """Reserve before GPU/provider work; returns plans with pilot diagnostic IDs."""
    path = Path(path)
    new = not path.exists()
    ledger = _load(path, identity)
    if new:
        history = path.parent/'home5090'
        if history.exists() and any(entry.name != run_id and entry.name.startswith('m1-') for entry in history.iterdir()):
            raise CampaignBlocked('M1 history exists without its campaign ledger; recovery required')
    if ledger['unresolved_create'] or ledger['unresolved_cleanup'] or any(run['status'] == 'active' for run in ledger['runs']):
        raise CampaignBlocked('active or unresolved prior batch requires explicit recovery')
    if any(run['run_id'] == run_id for run in ledger['runs']):
        raise CampaignBlocked('run identity already allocated')
    if mode not in ('pilot', 'controls', 'screen', 'eval') or not plans:
        raise CampaignBlocked('invalid campaign mode or empty allocation')
    if mode in ('screen', 'eval'):
        _require_prerequisites(ledger)
    plans = copy.deepcopy(plans)
    for plan in plans:
        plan['diagnostic_index'] = 0
    if mode == 'pilot':
        diagnostic = sum(run['mode'] == 'pilot' and any(item['status'] == 'attempted' for item in run['allocations']) for run in ledger['runs'])
        if diagnostic > 1:
            raise CampaignBlocked('only one additional diagnostic pilot batch is registered')
        if diagnostic:
            for plan in plans:
                plan['diagnostic_index'] = diagnostic
                plan['seed'] += diagnostic*100000000
                plan['trial_id'] += f'-diagnostic{diagnostic}'
    existing = {_key(run['mode'], item['plan']) for run in ledger['runs'] for item in run['allocations'] if item['status'] != 'released' and run['mode'] != 'pilot'}
    keys = [_key(mode, plan) for plan in plans]
    if len(set(keys)) != len(keys) or mode != 'pilot' and any(key in existing for key in keys):
        raise CampaignBlocked('formal attempt already allocated or attempted')
    count = len(plans)
    if count > (4 if mode == 'pilot' else 64):
        raise CampaignBlocked('single batch allocation exceeded')
    used = ledger['attempted_trials']+ledger['reserved_trials']
    if used+count > MAX_TRIALS or len(ledger['runs']) >= MAX_BATCHES:
        raise CampaignBlocked('campaign trial or batch reservation exceeded')
    if mode == 'pilot' and ledger['pilot_attempted']+ledger['pilot_reserved']+count > MAX_PILOT:
        raise CampaignBlocked('campaign pilot limit exceeded')
    gpu_seconds = 0 if mode == 'controls' else 3600
    if ledger['reserved_gpu_seconds']+gpu_seconds > MAX_GPU_SECONDS:
        raise CampaignBlocked('campaign GPU reservation exceeded')
    ledger['runs'].append({'run_id': run_id, 'mode': mode, 'status': 'active',
                          'reserved_gpu_seconds': gpu_seconds, 'summary_path': None,
                          'allocations': [{'plan': plan, 'status': 'reserved'} for plan in plans]})
    _save(path, ledger)
    return plans


def mark_attempt(path, identity, run_id, trial_id):
    """Persist before Trial.create; even local setup failure consumes this attempt."""
    ledger = _load(Path(path), identity)
    run = next((run for run in ledger['runs'] if run['run_id'] == run_id), None)
    if run is None or run['status'] != 'active' or ledger['unresolved_create'] or ledger['unresolved_cleanup']:
        raise CampaignBlocked('attempt outside an active resolved reservation')
    item = next((item for item in run['allocations'] if item['plan']['trial_id'] == trial_id), None)
    if item is None or item['status'] != 'reserved':
        raise CampaignBlocked('trial is not reserved or was already attempted')
    item['status'] = 'attempted'
    _save(Path(path), ledger)


def finish_run(path, identity, run_id, summary, summary_path=None):
    """Release only never-started allocations, retaining all actual attempts."""
    path = Path(path)
    ledger = _load(path, identity)
    run = next((run for run in ledger['runs'] if run['run_id'] == run_id), None)
    if run is None or run['status'] != 'active':
        raise CampaignBlocked('no active batch to finalize')
    attempted = {item['plan']['trial_id'] for item in run['allocations'] if item['status'] == 'attempted'}
    records = summary.get('records', [])
    record_ids = {row['trial_id'] for row in records}
    if len(record_ids) != len(records) or not record_ids.issubset(attempted):
        raise CampaignBlocked('summary contains unallocated or duplicate attempts')
    uncertain_create = any(event.get('status') == 'uncertain' for row in records for event in row.get('create_events', []))
    uncertain_cleanup = bool(attempted) and (summary.get('cleanup_confirmed_empty') is not True or record_ids != attempted
        or any(row.get('cleanup', {}).get('confirmed_empty') is not True for row in records))
    if summary.get('gpu_run') is True and summary.get('gpu_cleanup_confirmed') is not True:
        uncertain_cleanup = True
    ledger['unresolved_create'] |= uncertain_create
    ledger['unresolved_cleanup'] |= uncertain_cleanup
    for item in run['allocations']:
        if item['status'] == 'reserved':
            item['status'] = 'released'
    if summary_path is None:
        # Primarily useful for offline tests and explicit recovery tooling.
        evidence = runtime.safe_path(path.parent/'campaign-evidence')
        evidence.mkdir(exist_ok=True)
        summary_path = evidence/(run_id+'.json')
        runtime.write_json(summary_path, dict(summary, **identity, run_id=run_id, mode=run['mode']))
    summary_path = runtime.safe_path(Path(summary_path))
    raw = summary_path.read_bytes()
    if summary_path is not None and json.loads(raw) != dict(summary, **identity, run_id=run_id, mode=run['mode']):
        raise CampaignBlocked('final summary differs from the supplied evidence')
    run.update(status='finished', summary_path=str(summary_path), summary_sha256=hashlib.sha256(raw).hexdigest())
    _save(path, ledger)
