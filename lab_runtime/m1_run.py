"""Fixed, serial M1 Harbor batches. Importing this module starts no workload."""
import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import stat
import sys
import time
from urllib.parse import urlsplit

from . import home5090_run as runtime
from .home5090 import ROOT, VENV, SETTINGS, check_host, isolated_env
from .task_pool import build_pool
from . import m1_campaign as campaign

SOURCE = Path(__file__).resolve().parents[1]
WORK_SECONDS = 3600
STOP_NEW_SECONDS = 3200
CLEANUP_SECONDS = 90  # Within the externally reserved 120s, including GPU stop.
TRIAL_SECONDS = 360
MIN_NEW_TRIAL_SECONDS = TRIAL_SECONDS + 120 + 30
COST_RATES = {'source': 'https://www.daytona.io/pricing', 'checked_date': '2026-09-08',
              'cpu_per_vcpu_hour': 0.0504, 'ram_per_gib_hour': 0.0162,
              'disk_per_gib_hour': 0.000108, 'disk_gib_charged': 3,
              'free_storage_ignored': True, 'billing_verified': False}
SECRET_KEYS = {'DAYTONA_API_KEY', 'DAYTONA_API_URL', 'DAYTONA_TARGET'}


class CleanupUncertain(RuntimeError):
    pass


def bootstrap():
    check_host(sys.argv, platform.system(), platform.machine())
    env = isolated_env(os.environ)
    executable = VENV/'bin/python'
    if not executable.is_file():
        raise ValueError('prepared M1 Python environment is missing; no automatic install')
    if Path(sys.prefix) != VENV:
        os.execve(str(executable), [str(executable), '-B', str(SOURCE/'scripts/m1_run.py')], env)
        raise RuntimeError('execve unexpectedly returned')
    os.environ.clear()
    os.environ.update(env)
    main()


def read_credentials(path):
    path = Path(path)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('private Daytona configuration must not traverse symlinks')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_size > 16384:
            raise ValueError('private Daytona configuration requires owned regular 0600 file')
        content = handle.read(16385).decode('utf-8')
    values = {}
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in SECRET_KEYS or key in values or not value or value != value.strip() or '\0' in value:
            raise ValueError('invalid private Daytona configuration syntax or key')
        values[key] = value
    if not values.get('DAYTONA_API_KEY'):
        raise ValueError('private Daytona API key missing')
    if 'DAYTONA_API_URL' in values:
        url = urlsplit(values['DAYTONA_API_URL'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Daytona API URL must be an uncredentialed HTTPS URL')
    return values


@contextlib.contextmanager
def provider_credentials(values):
    previous = {key: os.environ.get(key) for key in SECRET_KEYS}
    previous_factory = logging.getLogRecordFactory()
    def factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        message = record.getMessage()
        message = message.replace(values['DAYTONA_API_KEY'], '[REDACTED]')
        record.msg, record.args = message, ()
        if record.exc_info:
            # Do not allow provider tracebacks to put credential-bearing errors in logs.
            record.exc_text = type(record.exc_info[1]).__name__
            record.exc_info = None
        return record
    logging.setLogRecordFactory(factory)
    for key in SECRET_KEYS:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        logging.setLogRecordFactory(previous_factory)


def select_trials(config, manifest):
    if set(config) != {'mode', 'start', 'count', 'max_trials', 'concurrency'}:
        raise ValueError('unknown or missing M1 config keys; retries are not supported')
    mode = config['mode']
    if mode not in ('pilot', 'controls', 'screen', 'eval') or config['concurrency'] != 1:
        raise ValueError('M1 supports only registered modes and serial trials')
    if any(type(config[key]) is not int for key in ('start', 'count', 'max_trials', 'concurrency')):
        raise ValueError('M1 limits must be integers')
    if config['start'] < 0 or not 1 <= config['count'] <= config['max_trials'] <= (4 if mode == 'pilot' else 64):
        raise ValueError('invalid M1 slice or batch limit')
    selected = []
    for item in manifest['instances']:
        if mode == 'pilot' and (item['split'] != 'train' or not item['instance'].endswith('-00')):
            continue
        if mode == 'screen' and item['split'] != 'train':
            continue
        if mode == 'eval' and item['split'] != 'eval':
            continue
        agents = ('nop', 'oracle') if mode == 'controls' else ('m1',)
        repetitions = 8 if mode == 'screen' else 3 if mode == 'eval' else 1
        for agent in agents:
            for repetition in range(repetitions):
                selected.append({key: item[key] for key in ('instance', 'family', 'domain', 'split', 'path')})
                selected[-1].update(trial_id=f'{mode}-{item["instance"]}-{agent}-{repetition:02d}',
                    repetition=repetition, diagnostic_index=0, seed=item['seed']*100+repetition, agent=agent,
                    temperature=0.6 if mode == 'eval' else 1.0 if agent == 'm1' else None)
    end = config['start']+config['count']
    if end > len(selected):
        raise ValueError('M1 slice exceeds registered complete schedule')
    return selected[config['start']:end]


async def cleanup_sandboxes(client, labels, events, deadline):
    from daytona import ListSandboxesQuery
    try:
        async with asyncio.timeout(runtime.remaining(deadline)):
            sandboxes = [sandbox async for sandbox in client.list(ListSandboxesQuery(labels=labels), request_timeout=min(15, runtime.remaining(deadline)))]
            for sandbox in sandboxes:
                if any(sandbox.labels.get(key) != value for key, value in labels.items()):
                    raise CleanupUncertain('provider returned a sandbox outside the exact batch labels')
                event = {'status': 'deleting', 'sandbox_id': sandbox.id, 'start_monotonic': time.monotonic()}
                events.append(event)
                await client.delete(sandbox, wait=True, timeout=min(30, runtime.remaining(deadline)))
                event.update(status='delete_returned', wall_seconds=time.monotonic()-event['start_monotonic'])
            remaining_ids = [sandbox.id async for sandbox in client.list(ListSandboxesQuery(labels=labels), request_timeout=min(15, runtime.remaining(deadline)))]
            if remaining_ids:
                events.append({'status': 'not_empty', 'sandbox_ids': remaining_ids})
                raise CleanupUncertain('batch deletion is not independently confirmed')
            events.append({'status': 'empty', 'monotonic': time.monotonic()})
    except BaseException as exc:
        events.append({'status': 'uncertain', 'error_type': type(exc).__name__})
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise CleanupUncertain('batch cleanup is uncertain: '+type(exc).__name__) from None


def make_trial_config(run, item, labels):
    from harbor.models.trial.config import TrialConfig
    agent = {'name': item['agent']} if item['agent'] != 'm1' else {
        'import_path': 'lab_runtime.m1_agent:M1Agent', 'model_name': SETTINGS['model'],
        'kwargs': {'temperature': item['temperature'], 'seed': item['seed']}}
    return TrialConfig(task={'path': run/'pool'/item['path']}, agent=agent,
        trials_dir=run/'trials', trial_name=item['trial_id'], environment={
            'type': 'daytona', 'import_path': 'lab_runtime.daytona:BoundedDaytonaEnvironment',
            'delete': True, 'cpu_enforcement_policy': 'request', 'memory_enforcement_policy': 'request',
            'override_cpus': 1, 'override_memory_mb': 1024, 'override_storage_mb': 3072, 'override_gpus': 0,
            'kwargs': {'auto_snapshot': False, 'auto_labels': False, 'labels': labels,
                       'network_block_all': True, 'auto_stop_interval_mins': 1,
                       'auto_delete_interval_mins': 0, 'expose_sandbox_id': True}})


def summarize_result(record, result, run):
    if result is not None:
        exception = getattr(result, 'exception_info', None)
        record['exception_info_type'] = getattr(exception, 'exception_type', None)
        verifier = getattr(result, 'verifier_result', None)
        record['rewards'] = getattr(verifier, 'rewards', None)
        context = getattr(result, 'agent_result', None)
        if context is not None:
            record['agent_context'] = {key: getattr(context, key, None) for key in (
                'metadata', 'n_input_tokens', 'n_output_tokens', 'n_cache_tokens', 'cost_usd')}
    trace_path = run/'trials'/record['trial_id']/'agent/m1-trace.json'
    if trace_path.is_file():
        trace = json.loads(trace_path.read_text())
        record['trace_path'] = str(trace_path)
        record['termination'] = trace.get('termination')
        record['error_phase'] = trace.get('error_phase')
        record['error_type'] = trace.get('error_type')
        usage = trace.get('usage', {})
        record['usage'] = {'input': usage.get('input'), 'output': usage.get('output'),
                           'complete': trace.get('usage_complete') is True}
        record['tool_wall_seconds'] = sum(tool.get('wall_seconds', 0) for turn in trace.get('turns', []) for tool in turn.get('tools', []))
    record['valid_for_denominator'] = (record['exception_type'] is None and record['exception_info_type'] is None
        and isinstance(record['rewards'], dict) and type(record['rewards'].get('reward')) in (int, float)
        and record['rewards'].get('reward') in (0, 1)
        and record['cleanup']['confirmed_empty'])
    if record['agent'] == 'm1' and (record['trace_path'] is None or record['usage']['complete'] is not True):
        record['valid_for_denominator'] = False


def cost_estimate(record):
    events = record['create_events']
    confirmed = record['cleanup']['confirmed_empty']
    ends = [event['monotonic'] for event in record['cleanup']['events'] if event['status'] == 'empty']
    known = confirmed and bool(ends) and len(events) == 1 and events[0]['status'] == 'created' and bool(events[0]['sandbox_id'])
    seconds = max(0, ends[-1]-events[0]['start_monotonic']) if known else None
    rate = (COST_RATES['cpu_per_vcpu_hour'] + COST_RATES['ram_per_gib_hour'] + 3*COST_RATES['disk_per_gib_hour'])/3600
    return {'estimated_usd': None if seconds is None else seconds*rate,
            'cpu_seconds': seconds, 'ram_gib_seconds': seconds,
            'disk_gib_seconds': None if seconds is None else seconds*3,
            'ttl_max_estimate_usd': 300*rate, 'billing_verified': False}


def persist_summary(run, summary):
    summary['attempted_count'] = len(summary['records'])
    summary['valid_denominator'] = sum(record['valid_for_denominator'] for record in summary['records'])
    summary['success_count'] = sum(record['valid_for_denominator'] and record['rewards'].get('reward') == 1 for record in summary['records'])
    runtime.write_json(run/'summary.json', summary)


def scrub_provider_errors(run, secrets):
    # Harbor saves its raw exception strings itself. Scrub known credentials from
    # its small text evidence before publishing our summary; do not copy secrets.
    for name in ('result.json', 'exception.txt', 'trial.log'):
        for path in (run/'trials').glob('*/'+name):
            if path.is_symlink() or path.stat().st_size > 16*1024**2:
                raise ValueError('unexpected provider evidence file')
            content = path.read_text()
            clean = content
            if secrets.get('DAYTONA_API_KEY'):
                clean = clean.replace(secrets['DAYTONA_API_KEY'], '[REDACTED]')
            if content != clean:
                path.write_text(clean)


async def run_batch(run, summary, deadline, secrets, server=None, campaign_identity=None):
    from harbor.trial.trial import Trial
    from harbor.environments.daytona.environment import DaytonaClientManager
    labels = {'lab': 'sandbox-rl-mopd', 'm1_run': run.name}
    manager = await DaytonaClientManager.get_instance()
    client = await manager.get_client()
    consecutive_create_failures = 0
    try:
        await cleanup_sandboxes(client, labels, summary['cleanup_events'], min(deadline, time.monotonic()+30))
        for item in summary['planned_trials']:
            if time.monotonic() >= deadline-WORK_SECONDS+STOP_NEW_SECONDS or deadline-time.monotonic() < MIN_NEW_TRIAL_SECONDS:
                summary['stopping_reason'] = 'budget_no_new_trials'
                break
            if server is not None:
                if server.poll() is not None:
                    raise RuntimeError('owned model server exited')
                runtime.assert_listener_owned(server.pid)
            if campaign_identity is not None:
                campaign.mark_attempt(ROOT/'artifacts/m1/campaign.json', campaign_identity, run.name, item['trial_id'])
            record = dict(item, exception_type=None, exception_info_type=None, rewards=None,
                          valid_for_denominator=False, agent_context=None, termination=None,
                          error_phase=None, error_type=None,
                          usage={'input': None, 'output': None, 'complete': False}, trace_path=None,
                          tool_wall_seconds=None, total_wall_seconds=None, create_events=[],
                          cleanup={'confirmed_empty': False, 'events': []}, cost=None)
            summary['records'].append(record)
            persist_summary(run, summary)
            started = time.monotonic()
            trial, result = None, None
            try:
                async with asyncio.timeout(min(TRIAL_SECONDS, runtime.remaining(deadline)-150)):
                    trial = await Trial.create(make_trial_config(run, item, labels))
                    result = await trial.run()
            except BaseException as exc:
                record['exception_type'] = type(exc).__name__
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
            finally:
                record['total_wall_seconds'] = time.monotonic()-started
                if trial is not None:
                    environment = getattr(trial, 'agent_environment', None)
                    record['create_events'] = getattr(environment, 'm1_create_events', [])
                    result = result or getattr(trial, 'result', None)
                try:
                    with runtime.cleanup_signals():
                        await cleanup_sandboxes(client, labels, record['cleanup']['events'], min(time.monotonic()+60, deadline+30))
                    record['cleanup']['confirmed_empty'] = True
                finally:
                    summarize_result(record, result, run)
                    record['total_wall_seconds'] = time.monotonic()-started
                    record['cost'] = cost_estimate(record)
                    scrub_provider_errors(run, secrets)
                    persist_summary(run, summary)
            failed_create = bool(record['create_events']) and all(event['status'] != 'created' for event in record['create_events'])
            if any(event['status'] == 'uncertain' for event in record['create_events']):
                raise CleanupUncertain('create response uncertain; an empty immediate list is insufficient')
            consecutive_create_failures = consecutive_create_failures+1 if failed_create else 0
            if consecutive_create_failures >= 3:
                summary['stopping_reason'] = 'three_consecutive_create_failures'
                break
        else:
            summary['stopping_reason'] = 'planned_slice_completed'
    except BaseException as exc:
        summary['stopping_reason'] = type(exc).__name__
        raise
    finally:
        try:
            with runtime.cleanup_signals():
                await cleanup_sandboxes(client, labels, summary['cleanup_events'], min(time.monotonic()+30, deadline+40))
            summary['cleanup_confirmed_empty'] = not any(event['status'] == 'uncertain'
                for record in summary['records'] for event in record['create_events'])
        finally:
            persist_summary(run, summary)
            try:
                with runtime.cleanup_signals():
                    async with asyncio.timeout(min(5, max(0, deadline+45-time.monotonic()))):
                        await client.close()
            finally:
                manager._client = None  # Pinned Harbor singleton; avoid cross-loop atexit reuse.


def main():
    check_host(sys.argv, platform.system(), platform.machine())
    with runtime.run_context('m1', WORK_SECONDS, artifact_stage='m1') as (run, state, deadline, log):
        summary = {'schema_version': 1, 'run_id': run.name, 'mode': None, 'config': None,
                   'source_git_sha': None, 'agent_source_sha256': None, 'pool_manifest_sha256': None,
                   'model_revision': SETTINGS['revision'], 'serving_lock_sha256': None, 'gpu_run': False,
                   'runtime_limits': {'max_model_len': 16384, 'output_tokens': 4096,
                                      'rounds': 10, 'tool_seconds': 20, 'agent_seconds': 180},
                   'planned_trials': [], 'records': [], 'stopping_reason': 'preflight',
                   'cleanup_confirmed_empty': False, 'cleanup_events': [], 'cost_rates': COST_RATES}
        campaign_identity = None
        reserved = False
        persist_summary(run, summary)
        try:
            config = json.loads((SOURCE/'configs/m1.json').read_text())
            summary.update(config=config, mode=config['mode'])
            frozen_path = SOURCE/'configs/m1-pool-manifest.json'
            frozen = json.loads(frozen_path.read_text())
            summary['pool_manifest_sha256'] = runtime.sha(frozen_path, deadline)
            if build_pool(run/'pool') != frozen:
                raise ValueError('generated M1 pool differs from frozen manifest')
            summary['planned_trials'] = select_trials(config, frozen)
            summary['source_git_sha'] = runtime.source_identity(deadline, log)
            summary['agent_source_sha256'] = runtime.sha(SOURCE/'lab_runtime/m1_agent.py', deadline)
            summary['serving_lock_sha256'] = runtime.sha(SOURCE/'environments/home5090/uv.lock', deadline)
            campaign_identity = {key: summary[key] for key in campaign.IDENTITY_KEYS}
            summary['planned_trials'] = campaign.reserve_run(ROOT/'artifacts/m1/campaign.json',
                campaign_identity, run.name, config['mode'], summary['planned_trials'])
            reserved = True
            summary['campaign_path'] = str(ROOT/'artifacts/m1/campaign.json')
            values = read_credentials(ROOT/'secrets/daytona.env')
            persist_summary(run, summary)
            with provider_credentials(values):
                if config['mode'] == 'controls':
                    asyncio.run(run_batch(run, summary, deadline, values, campaign_identity=campaign_identity))
                else:
                    with runtime.managed_server(run, state, deadline, log, allow_previous_source=True) as server:
                        summary['gpu_run'] = True
                        asyncio.run(run_batch(run, summary, deadline, values, server, campaign_identity))
        except BaseException as exc:
            summary['stopping_reason'] = type(exc).__name__
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError('M1 batch stopped: '+type(exc).__name__) from None
        finally:
            summary['gpu_run'] = 'server_pid' in state
            summary['gpu_cleanup_confirmed'] = not summary['gpu_run'] or state.get('owned_group_gone') is True
            summary['model_content_verified_after'] = state.get('post_model_check') == 'verified'
            persist_summary(run, summary)
            if reserved:
                campaign.finish_run(ROOT/'artifacts/m1/campaign.json', campaign_identity,
                    run.name, summary, run/'summary.json')
            state['summary_path'] = str(run/'summary.json')
            state['batch_completed'] = summary['stopping_reason'] == 'planned_slice_completed'
