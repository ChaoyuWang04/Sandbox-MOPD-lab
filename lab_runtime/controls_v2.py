"""Independent first-six control campaign; one trial per invocation, fail closed."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def peak_rss_bytes(value, platform):
    if platform == 'darwin':
        return int(value)
    if platform.startswith('linux'):
        return int(value)*1024
    raise ValueError('unknown ru_maxrss units')


def validate_config(cfg):
    # This fixed first-six authorization is deliberately not a general scheduler.
    expected = dict(campaign='m1-v2-first-six-r2', task_ids=['data_csv-train-00',
        'andialbrecht__sqlparse.e57923b3.func_basic__0mum3b07', 'getmoto__moto-5752'],
        agents=['nop', 'oracle'], max_attempts=6, concurrency=1, cpus=4,
        memory_mb=8192, storage_mb=10240, gpus=0, trial_seconds=3600,
        ttl_minutes=60, controller_seconds=3900, reserve_usd=8, import_reserve_usd=2)
    expected.update(max_cumulative_attempts=7, predecessor=dict(campaign='m1-v2-first-six',
        attempts_consumed=1, ledger_path='artifacts/m1/v2/controls/campaign.json',
        ledger_sha256='d3dbdca04b0ccdcf3db10e6f2cebf56677793dc2052bf1ad012f77d0a632e53a',
        cleanup_path='artifacts/m1/v2/controls/attempt-00/independent-cleanup-check.json',
        cleanup_sha256='95a815524f4d89fdc80053890c893dcc476d87f7b3723795fbb94dd5c0f0f3e6'))
    if cfg != expected or any(type(cfg[k]) is not type(v) for k, v in expected.items()):
        raise ValueError('unregistered first-six configuration')


def verify_predecessor(root, cfg):
    prior = cfg['predecessor']
    for name in ('ledger', 'cleanup'):
        path = Path(root)/prior[name+'_path']
        if not path.is_file() or path.is_symlink() or digest(path) != prior[name+'_sha256']:
            raise ValueError('predecessor evidence missing or changed')


def source_identity(source):
    source = Path(source)
    paths = list((source/'lab_runtime').glob('*.py'))
    paths += [source/p for p in ('scripts/m1_controls.py', 'configs/m1-controls-v2.json',
        'configs/m1-pool-v2.json', 'configs/m1-swe-profiles-v2.json', 'uv.lock', 'pyproject.toml')]
    return {p.relative_to(source).as_posix(): digest(p) for p in sorted(paths)}


def admit(ledger, index):
    attempts = ledger['attempts']
    if type(index) is not int or index != len(attempts) or not 0 <= index < 6:
        raise ValueError('only the next unattempted control may run')
    if any(a['state'] != 'passed' for a in attempts):
        raise ValueError('previous attempt requires diagnosis; no retry or new creation')


def make_trial_config(task, run, agent, labels):
    from harbor.models.trial.config import TrialConfig
    if agent not in ('nop', 'oracle'):
        raise ValueError('trusted controls only')
    return TrialConfig(task={'path': task}, agent={'name': agent}, trials_dir=run/'trials',
        trial_name='control', environment={'type': 'daytona',
        'import_path': 'lab_runtime.daytona_v2:ControlsDaytonaEnvironment', 'delete': True,
        'cpu_enforcement_policy': 'request', 'memory_enforcement_policy': 'request',
        'override_cpus': 4, 'override_memory_mb': 8192, 'override_storage_mb': 10240,
        'override_gpus': 0, 'kwargs': {'auto_snapshot': False, 'auto_labels': False,
        'labels': labels, 'network_block_all': False, 'auto_delete_interval_mins': 0,
        'expose_sandbox_id': True}})


def valid_result(record, agent, grading):
    expected = 0 if agent == 'nop' else 1
    return (record.get('exception_type') is None and record.get('reward') == expected
        and record.get('cleanup_empty') is True and not record.get('create_uncertain')
        and record.get('phase') == 'verification_ready'
        and (grading is None or (grading.get('protocol_complete') is True
            and grading.get('reward') == expected and not grading.get('runtime_errors')
            and grading.get('owned_process_group_stopped') is True)))


async def cleanup(client, labels, known_ids, emit, *, timeout_seconds=120, poll_seconds=.5):
    from daytona import ListSandboxesQuery
    if not 0 < timeout_seconds <= 120 or not 0 < poll_seconds <= .5:
        raise ValueError('cleanup bound exceeded')
    async with asyncio.timeout(timeout_seconds):
        objects = [s async for s in client.list(ListSandboxesQuery(labels=labels), request_timeout=20)]
        for sid in known_ids:
            if sid not in {s.id for s in objects}:
                try:
                    objects.append(await client.get(sid))
                except Exception as exc:
                    if type(exc).__name__ != 'DaytonaNotFoundError':
                        raise
        for sandbox in objects:
            if any(sandbox.labels.get(k) != v for k, v in labels.items()):
                raise RuntimeError('cleanup ownership mismatch')
            emit(dict(event='cleanup_delete', sandbox_id=sandbox.id))
            try:
                await client.delete(sandbox, wait=True, timeout=30)
            except Exception as exc:
                if type(exc).__name__ not in ('DaytonaNotFoundError', 'DaytonaConflictError'):
                    raise
        # Delete once; list and direct-ID reads may lag the accepted deletion.
        # Both must independently converge inside the original deadline.
        while True:
            remaining = [s.id async for s in client.list(ListSandboxesQuery(labels=labels), request_timeout=20)]
            present = []
            for sid in known_ids:
                try:
                    await client.get(sid)
                except Exception as exc:
                    if type(exc).__name__ != 'DaytonaNotFoundError':
                        raise
                else:
                    present.append(sid)
            emit(dict(event='cleanup_list', remaining_ids=remaining, known_ids_present=present))
            if not remaining and not present:
                break
            await asyncio.sleep(poll_seconds)


def attach_absence(trial, agent):
    from harbor.trial.hooks import TrialEvent
    evidence = {'phase': 'registered'}
    async def check(event):
        phase = evidence['phase']
        command = 'test ! -e /tests && test ! -L /tests'
        if phase == 'registered' or agent == 'nop':
            command += ' && test ! -e /solution && test ! -L /solution'
        result = await trial.agent_environment.exec(command=command, timeout_sec=30)
        if result.return_code != 0:
            raise RuntimeError('normal phase private files present')
        evidence['phase'] = {'registered': 'agent_ready', 'agent_ready': 'agent_ended', 'agent_ended': 'verification_ready'}[phase]
    for event in (TrialEvent.AGENT_START, TrialEvent.AGENT_END, TrialEvent.VERIFICATION_START):
        trial.add_hook(event, check)
    return evidence


async def execute(root, source, commit, index, *, trial_factory=None, client=None):
    cfg = json.loads((Path(source)/'configs/m1-controls-v2.json').read_text())
    validate_config(cfg)
    started = asyncio.get_running_loop().time()
    deadline = started + cfg['controller_seconds'] - 10  # bounded loop shutdown reserve
    async with asyncio.timeout_at(deadline):
        return await _execute(root, source, commit, index, trial_factory=trial_factory,
            client=client, work_deadline=started+cfg['trial_seconds'], deadline=deadline)


async def _execute(root, source, commit, index, *, work_deadline, deadline,
                   trial_factory=None, client=None):
    from . import daytona_v2
    from .task_sources import atomic_json
    from .swe_hooks import attach_swe_hooks
    source, root = Path(source).resolve(), Path(root).resolve()
    config_path = source/'configs/m1-controls-v2.json'
    manifest_path = source/'configs/m1-pool-v2.json'
    cfg = json.loads(config_path.read_text())
    validate_config(cfg)
    verify_predecessor(root, cfg)
    identity = source_identity(source)
    ledger_path = root/'artifacts/m1/v2/controls'/cfg['campaign']/'campaign.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(identity=identity, attempts=[], predecessor=cfg['predecessor'])
    if ledger['identity'] != identity:
        raise ValueError('campaign identity changed')
    admit(ledger, index)
    task_id, agent = cfg['task_ids'][index//2], cfg['agents'][index%2]
    rows = [r for r in json.loads(manifest_path.read_text())['records'] if r['id'] == task_id]
    if len(rows) != 1:
        raise ValueError('exact task identity absent or duplicate')
    row = rows[0]
    task = source/row['task_path']
    actual = {p.relative_to(task).as_posix(): digest(p) for p in task.rglob('*') if p.is_file()}
    if actual != row['task_files_sha256'] or any(p.is_symlink() for p in task.rglob('*')):
        raise ValueError('task bytes changed')
    run = ledger_path.parent/f'attempt-{index:02d}'
    run.mkdir(parents=True, exist_ok=False)
    labels = {'m1_v2_run': cfg['campaign'], 'm1_v2_attempt': str(index)}
    record = dict(state='active', task_id=task_id, agent=agent, labels=labels,
                  controller_pid=os.getpid(), controller_platform=sys.platform,
                  started=time.time(), events=[], exception_type=None, cleanup_empty=False,
                  create_uncertain=False, reward=None)
    ledger['attempts'].append(record)
    ledger['cumulative_attempts'] = cfg['predecessor']['attempts_consumed'] + len(ledger['attempts'])
    def persist():
        atomic_json(ledger_path, ledger)
        descriptor = os.open(ledger_path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        commit()
    def emit(event):
        record['events'].append(event)
        persist()
    persist()  # Durable admission before any provider or Trial action.
    trial = None
    manager = None
    evidence = {}
    grading = None
    try:
        async with asyncio.timeout_at(work_deadline):
            if client is None:
                from harbor.environments.daytona.environment import DaytonaClientManager
                manager = await DaytonaClientManager.get_instance()
                client = await manager.get_client()
            # Read-only admission: old objects block creation, never silently reuse them.
            from daytona import ListSandboxesQuery
            async with asyncio.timeout(30):
                prior_objects = [s.id async for s in client.list(ListSandboxesQuery(labels={'m1_v2_run': cfg['predecessor']['campaign']}), request_timeout=20)]
                emit(dict(event='predecessor_admission_list', sandbox_ids=prior_objects))
                if prior_objects:
                    raise RuntimeError('predecessor campaign objects still exist')
                existing = [s.id async for s in client.list(ListSandboxesQuery(labels={'m1_v2_run': cfg['campaign']}), request_timeout=20)]
            emit(dict(event='admission_list', sandbox_ids=existing))
            if existing:
                raise RuntimeError('campaign objects already exist')
            daytona_v2.EVENT_SINK = emit
            if trial_factory is None:
                from harbor.trial.trial import Trial
                trial_factory = Trial.create
            trial = await trial_factory(make_trial_config(task, run, agent, labels))
            if row['source'] in ('swe-smith', 'swe-gym'):
                private = json.loads((task/'tests/private.json').read_text())
                evidence = attach_swe_hooks(trial, run/'private', private['source'],
                    private['profile'], private['gold_patch_paths'], agent)
            else:
                evidence = attach_absence(trial, agent)
            result = await trial.run()
            record['exception_type'] = getattr(getattr(result, 'exception_info', None), 'exception_type', None)
            record['reward'] = (getattr(getattr(result, 'verifier_result', None), 'rewards', None) or {}).get('reward')
            if row['source'] in ('swe-smith', 'swe-gym'):
                grading = json.loads((run/'trials/control/verifier/run.json').read_text())
            else:
                self_grade = json.loads((run/'trials/control/verifier/result.json').read_text())
                record['self_grading'] = self_grade
                if (type(self_grade.get('passed')) is not bool
                        or self_grade['passed'] != (agent == 'oracle')
                        or self_grade.get('detail') not in ('pass', 'answer_or_input_mismatch', 'missing_or_invalid_answer_or_input')):
                    raise RuntimeError('self grader did not complete expected control')
    except BaseException as exc:
        record['exception_type'] = type(exc).__name__
    finally:
        events = [e for e in record['events'] if e.get('status') in ('creating', 'created', 'uncertain', 'rejected')]
        record['create_uncertain'] = bool(events) and events[-1]['status'] in ('creating', 'uncertain')
        if (len([e for e in events if e['status'] == 'created' and e.get('sandbox_id')]) != 1
                and record['exception_type'] is None):
            record['exception_type'] = 'MissingSingleCreatedSandboxEvidence'
        record['phase'] = evidence.get('phase')
        try:
            if client is None:
                raise RuntimeError('no cleanup client')
            async with asyncio.timeout_at(min(deadline-30, asyncio.get_running_loop().time()+120)):
                await cleanup(client, labels, {e['sandbox_id'] for e in events if e.get('sandbox_id')}, emit)
            record['cleanup_empty'] = True
        except BaseException as exc:
            record['cleanup_error_type'] = type(exc).__name__
        if manager is not None:
            try:
                if client is None:
                    client = getattr(manager, '_client', None)
                if client is not None:
                    async with asyncio.timeout_at(min(deadline-10, asyncio.get_running_loop().time()+15)):
                        await client.close()
                record['sdk_closed'] = True
            except BaseException as exc:
                record['sdk_close_error_type'] = type(exc).__name__
                record['exception_type'] = record['exception_type'] or 'SDKCloseFailed'
            finally:
                manager._client = None  # pinned Harbor singleton; no atexit cross-loop close
        record['grading'] = grading
        record['state'] = 'passed' if valid_result(record, agent, grading) else 'failed'
        if record['create_uncertain'] or not record['cleanup_empty']:
            record['state'] = 'uncertain'
        record['finished'] = time.time()
        # Harbor writes raw exception strings; never persist a provider key.
        secret = os.environ.get('DAYTONA_API_KEY')
        if secret:
            for path in run.rglob('*'):
                if path.is_file():
                    data = path.read_bytes()
                    if secret.encode() in data:
                        path.write_bytes(data.replace(secret.encode(), b'[REDACTED]'))
        daytona_v2.EVENT_SINK = None
        record['controller_peak_rss_bytes'] = peak_rss_bytes(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sys.platform)
        record['controller_memory_target_bytes'] = 512*1024*1024
        record['controller_memory_within_target'] = record['controller_peak_rss_bytes'] <= record['controller_memory_target_bytes']
        persist()
    return record
