"""One registered Qwen3-4B -> Daytona pilot; importing starts no workload."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

from . import home5090_run as runtime
from .controls_v2 import cleanup as cleanup_sandboxes
from .home5090 import ROOT, SETTINGS, VENV, check_host, isolated_env
from .m1_run import COST_RATES, cost_estimate, provider_credentials, read_credentials, scrub_provider_errors
from .task_sources import atomic_json


SOURCE = Path(__file__).resolve().parents[1]
CONFIG_PATH = SOURCE/'configs/m1-pilot-v2.json'
WORK_SECONDS = 3600
CAMPAIGN_REL = Path('artifacts/m1/v2/model-pilot/campaign.json')
OLD_CAMPAIGN_REL = Path('artifacts/m1/campaign.json')

EXPECTED_CONFIG = {
    'schema_version': 1,
    'mode': 'model-pilot-v2',
    'task_generator': 'lab_runtime.task_pool_v2:materialize_self_pool',
    'task_generator_sha256': 'ab5f82a8591d913f59f6fa8d62840bfdb2b8ef2609556db79e8e742e8e61c1f3',
    'generated_manifest_sha256': '3f13ba82d7d5f657956c578402bc9ebc2aa87435b29ba11690c7af3fe640303a',
    'trials': [
        {'task_id': 'self-v2-config_precedence-00',
         'trial_id': 'model-pilot-v2-self-v2-config_precedence-00',
         'seed': 910001, 'temperature': .6},
        {'task_id': 'self-v2-resource_lifetime-00',
         'trial_id': 'model-pilot-v2-self-v2-resource_lifetime-00',
         'seed': 910002, 'temperature': .6},
        {'task_id': 'self-v2-async_dependencies-00',
         'trial_id': 'model-pilot-v2-self-v2-async_dependencies-00',
         'seed': 910003, 'temperature': .6},
        {'task_id': 'self-v2-atomic_replace-00',
         'trial_id': 'model-pilot-v2-self-v2-atomic_replace-00',
         'seed': 910004, 'temperature': .6}],
    'model': {'id': 'Qwen/Qwen3-4B',
              'revision': '1cfa9a7208912126459214e8b04321603b3df60c',
              'max_model_len': 16384, 'max_output_tokens': 4096, 'max_turns': 10},
    'trial': {'concurrency': 1, 'agent_timeout_seconds': 180,
              'tool_timeout_seconds': 20, 'verifier_timeout_seconds': 30,
              'build_timeout_seconds': 120, 'daytona_ttl_seconds': 300,
              'max_created_sandboxes': 4, 'network': False, 'cpus': 1,
              'memory_mb': 1024, 'storage_mb': 3072, 'gpus': 0},
    'batch': {'work_seconds': 3600, 'hlab_timeout_seconds': 3660,
              'daytona_reserve_usd': .1}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(config):
    if config != EXPECTED_CONFIG:
        raise ValueError('unregistered model pilot configuration')
    trials = config['trials']
    if (len({row['task_id'] for row in trials}) != 4
            or len({row['trial_id'] for row in trials}) != 4
            or len({row['seed'] for row in trials}) != 4):
        raise ValueError('model pilot trial identities must be unique')


def validate_agent_contract(config):
    validate_config(config)
    if (config['model'] != {'id': SETTINGS['model'], 'revision': SETTINGS['revision'],
                            'max_model_len': 16384, 'max_output_tokens': 4096,
                            'max_turns': 10}
            or config['trial']['agent_timeout_seconds'] != 180
            or config['trial']['tool_timeout_seconds'] != 20):
        raise ValueError('registered pilot differs from the fixed agent/server contract')


def select_trials(config, manifest):
    validate_config(config)
    rows = {}
    for requested in config['trials']:
        matches = [row for row in manifest.get('instances', [])
                   if row.get('instance') == requested['task_id']]
        if len(matches) != 1:
            raise ValueError('registered pilot task absent or duplicated')
        task = matches[0]
        resources = task.get('resources')
        if (task.get('source') != 'self-authored' or task.get('variant') != 0
                or task.get('provisional_split') != 'train'
                or resources != {'cpus': 1, 'memory_mb': 1024, 'storage_mb': 3072,
                                  'gpus': 0, 'network': False,
                                  'build_timeout_sec': 120,
                                  'agent_timeout_sec': 180,
                                  'verifier_timeout_sec': 30}):
            raise ValueError('registered pilot task metadata changed')
        item = {key: task[key] for key in ('instance', 'family', 'domain', 'path')}
        item.update(split='train', agent='m1', repetition=0, diagnostic_index=0,
                    trial_id=requested['trial_id'], seed=requested['seed'],
                    temperature=requested['temperature'])
        rows[requested['task_id']] = item
    return [rows[item['task_id']] for item in config['trials']]


def _load_ledger(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('pilot campaign evidence missing or unreadable') from None
    return value


def _old_identity(path):
    path = Path(path)
    return {'exists': path.exists(), 'sha256': digest(path) if path.exists() else None}


def _assert_old_unchanged(path, expected):
    if _old_identity(path) != expected:
        raise ValueError('legacy M1 campaign changed during v2 pilot')


def _persist_ledger(path, ledger):
    path = Path(path)
    atomic_json(path, ledger)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reserve_campaign(path, old_path, identity, run_id, plans):
    path, old_path = Path(path), Path(old_path)
    if (set(identity) != {'source_git_sha', 'config_sha256'}
            or any(not isinstance(value, str) or not value for value in identity.values())
            or len(plans) != 4 or len({row['trial_id'] for row in plans}) != 4):
        raise ValueError('model pilot campaign has invalid identity')
    old = _old_identity(old_path)
    history = []
    if path.exists():
        previous = _load_ledger(path)
        if (previous.get('schema_version') != 1 or previous.get('state') != 'finished'
                or previous.get('provider_create_authorizations') != 0
                or previous.get('old_campaign_identity') != old):
            raise ValueError('model pilot campaign is active or consumed a provider authorization')
        history = list(previous.get('prior_runs', []))
        history.append({key: value for key, value in previous.items() if key != 'prior_runs'})
    ledger = {'schema_version': 1, 'campaign': 'm1-model-pilot-v2',
              'identity': identity, 'run_id': run_id, 'state': 'active',
              'plans': plans, 'attempts': [], 'provider_create_authorizations': 0,
              'max_provider_creates': 4, 'old_campaign_identity': old,
              'prior_runs': history}
    _persist_ledger(path, ledger)
    _assert_old_unchanged(old_path, old)
    return ledger


def mark_attempt(path, identity, run_id, plan, index):
    path = Path(path)
    ledger = _load_ledger(path)
    if (ledger.get('identity') != identity or ledger.get('run_id') != run_id
            or ledger.get('state') != 'active' or type(index) is not int
            or index != len(ledger.get('attempts', []))
            or index >= ledger.get('max_provider_creates', -1)
            or index >= len(ledger.get('plans', []))
            or ledger['plans'][index] != plan):
        raise ValueError('pilot attempt is not the next registered authorization')
    record = {'index': index, 'trial_id': plan['trial_id'], 'task_id': plan['instance'],
              'state': 'admitted', 'events': [], 'exception_type': None,
              'create_uncertain': False, 'cleanup_empty': False, 'reward': None,
              'classification': None}
    ledger['attempts'].append(record)
    ledger['provider_create_authorizations'] += 1
    _persist_ledger(path, ledger)
    return record


def update_attempt(path, identity, run_id, index, updates):
    ledger = _load_ledger(path)
    if (ledger.get('identity') != identity or ledger.get('run_id') != run_id
            or index >= len(ledger.get('attempts', []))):
        raise ValueError('pilot attempt update identity mismatch')
    ledger['attempts'][index].update(updates)
    _persist_ledger(path, ledger)
    return ledger['attempts'][index]


def finish_campaign(path, identity, run_id, summary_path, summary_sha256):
    ledger = _load_ledger(path)
    if (ledger.get('identity') != identity or ledger.get('run_id') != run_id
            or ledger.get('state') != 'active' or not summary_path
            or not isinstance(summary_sha256, str) or len(summary_sha256) != 64):
        raise ValueError('pilot campaign cannot be finalized')
    ledger['state'] = 'finished'
    ledger['summary_path'] = summary_path
    ledger['summary_sha256'] = summary_sha256
    _persist_ledger(path, ledger)
    return ledger


def classify_record(record):
    if record.get('create_uncertain'):
        return 'infra_invalid_create_uncertain'
    if record.get('cleanup_empty') is not True:
        return 'infra_invalid_cleanup'
    if record.get('exception_type') is not None:
        return 'infra_invalid_exception'
    if record.get('usage_complete') is not True or record.get('model_responses', 0) < 1:
        return 'model_invalid_trace_or_usage'
    if type(record.get('reward')) not in (int, float) or record['reward'] not in (0, 1):
        return 'infra_invalid_reward'
    return 'valid_reward'


def creation_uncertain(events):
    creation = [event for event in events if event.get('status') in (
        'creating', 'created', 'rejected', 'uncertain')]
    created = [event for event in creation if event.get('status') == 'created']
    if created and (len(created) != 1 or not isinstance(created[0].get('sandbox_id'), str)
                    or not created[0]['sandbox_id']):
        return True
    return bool(creation) and creation[-1].get('status') in ('creating', 'uncertain')


def estimate_cost(record):
    rate = (COST_RATES['cpu_per_vcpu_hour']
            + COST_RATES['ram_per_gib_hour']
            + 3*COST_RATES['disk_per_gib_hour'])/3600
    events = record.get('events', [])
    created = [event for event in events if event.get('status') == 'created']
    created_ids = {event.get('sandbox_id') for event in created
                   if isinstance(event.get('sandbox_id'), str) and event['sandbox_id']}
    starts = [event.get('time') for event in events
              if event.get('status') == 'creating' and type(event.get('time')) in (int, float)]
    uncertain = record.get('create_uncertain') or creation_uncertain(events)
    known = (not uncertain and record.get('cleanup_empty') is True
             and len(created) == 1 and len(created_ids) == 1 and len(starts) == 1
             and type(record.get('finished')) in (int, float))
    if known:
        seconds = max(0, record['finished']-starts[0])
        estimate = seconds*rate
    elif not uncertain and not created:
        seconds, estimate = 0, 0
    else:
        seconds, estimate = None, None
    return {'estimated_usd': estimate, 'sandbox_seconds': seconds,
            'ttl_max_estimate_usd': 300*rate, 'billing_verified': False}


def summarize_records(records):
    records = [dict(record) for record in records]
    for record in records:
        record['classification'] = classify_record(record)
        record['cost'] = record.get('cost') or estimate_cost(record)
    valid = [record for record in records if record['classification'] == 'valid_reward']
    costs = [record['cost']['estimated_usd'] for record in records]
    return {'records': records, 'attempted_count': len(records),
            'valid_reward_count': len(valid), 'invalid_count': len(records)-len(valid),
            'reward_counts': {str(value): sum(record['reward'] == value for record in valid)
                              for value in (0, 1)},
            'success_count_observation': sum(record['reward'] == 1 for record in valid),
            'estimated_daytona_usd': (None if any(value is None for value in costs)
                                       else sum(costs)),
            'cost_unknown_count': sum(value is None for value in costs),
            'real_agent_chain_passed': any(
                record.get('model_responses', 0) > 0
                and record.get('tool_calls', 0) > 0
                and record.get('tool_observations', 0) > 0
                and record.get('shell_executions', 0) > 0 for record in valid),
            'attempts_terminal_and_classified': len(records) == 4 and all(
                record.get('state') == 'finished' and record['classification']
                and record.get('cleanup_empty') is True
                and record.get('create_uncertain') is False
                for record in records)}


def make_trial_config(run, item, labels):
    from harbor.models.trial.config import TrialConfig
    return TrialConfig(task={'path': run/'pool'/item['path']}, agent={
        'import_path': 'lab_runtime.m1_agent:M1Agent', 'model_name': SETTINGS['model'],
        'kwargs': {'temperature': item['temperature'], 'seed': item['seed']}},
        trials_dir=run/'trials', trial_name=item['trial_id'], environment={
            'type': 'daytona', 'import_path': 'lab_runtime.daytona_pilot:PilotDaytonaEnvironment',
            'delete': True, 'cpu_enforcement_policy': 'request',
            'memory_enforcement_policy': 'request', 'override_cpus': 1,
            'override_memory_mb': 1024, 'override_storage_mb': 3072,
            'override_gpus': 0, 'kwargs': {'auto_snapshot': False,
                'auto_labels': False, 'labels': labels, 'network_block_all': True,
                'auto_stop_interval_mins': 1, 'auto_delete_interval_mins': 0,
                'expose_sandbox_id': True}})


def _trace_fields(run, trial_id):
    path = run/'trials'/trial_id/'agent/m1-trace.json'
    if not path.is_file():
        return {'trace_path': None, 'usage_complete': False, 'model_responses': 0,
                'tool_calls': 0, 'tool_observations': 0, 'shell_executions': 0,
                'termination': None,
                'usage': {'input': None, 'output': None}}
    trace = json.loads(path.read_text())
    turns = trace.get('turns', [])
    tools = [tool for turn in turns for tool in turn.get('tools', [])]
    return {'trace_path': str(path), 'usage_complete': trace.get('usage_complete') is True,
            'model_responses': sum(isinstance(turn.get('response'), dict) for turn in turns),
            'tool_calls': trace.get('tool_calls', 0),
            'tool_observations': sum('observation' in tool for tool in tools),
            'shell_executions': sum(isinstance(tool.get('observation'), dict)
                                    and type(tool['observation'].get('return_code')) is int
                                    for tool in tools),
            'termination': trace.get('termination'), 'usage': trace.get('usage')}


async def execute_trials(run, summary, ledger_path, old_path, identity, deadline, secrets):
    from daytona import ListSandboxesQuery
    from harbor.environments.daytona.environment import DaytonaClientManager
    from harbor.trial.trial import Trial
    from . import daytona_pilot
    manager = await DaytonaClientManager.get_instance()
    client = await manager.get_client()
    campaign_labels = {'m1_v2_pilot': 'm1-model-pilot-v2'}
    try:
        existing = [sandbox.id async for sandbox in client.list(
            ListSandboxesQuery(labels=campaign_labels), request_timeout=20)]
        if existing:
            raise RuntimeError('model pilot objects already exist; refusing ownership guess')
        for index, item in enumerate(summary['planned_trials']):
            record = mark_attempt(ledger_path, identity, summary['run_id'], item, index)
            labels = {**campaign_labels, 'm1_v2_run': summary['run_id'],
                      'm1_v2_attempt': str(index)}

            def persist_event(event):
                current = _load_ledger(ledger_path)['attempts'][index]
                events = list(current.get('events', []))
                events.append(event)
                update_attempt(ledger_path, identity, summary['run_id'], index,
                               {'events': events})

            trial = None
            result = None
            daytona_pilot.EVENT_SINK = persist_event
            try:
                async with asyncio.timeout(min(360, max(0, deadline-time.monotonic()-150))):
                    trial = await Trial.create(make_trial_config(run, item, labels))
                    result = await trial.run()
                record['exception_type'] = getattr(
                    getattr(result, 'exception_info', None), 'exception_type', None)
                rewards = getattr(getattr(result, 'verifier_result', None), 'rewards', None)
                record['reward'] = rewards.get('reward') if isinstance(rewards, dict) else None
            except BaseException as exc:
                record['exception_type'] = type(exc).__name__
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
            finally:
                daytona_pilot.EVENT_SINK = None
                saved = _load_ledger(ledger_path)['attempts'][index]
                events = saved.get('events', [])
                creation = [event for event in events if event.get('status') in (
                    'creating', 'created', 'rejected', 'uncertain')]
                known_ids = {event['sandbox_id'] for event in creation if event.get('sandbox_id')}
                record['events'] = events
                record['create_uncertain'] = creation_uncertain(creation)
                cleanup_events = []
                try:
                    await cleanup_sandboxes(client, labels, known_ids,
                                            cleanup_events.append,
                                            timeout_seconds=min(120, max(.01, deadline-time.monotonic()-30)))
                    record['cleanup_empty'] = True
                except BaseException as exc:
                    record['cleanup_error_type'] = type(exc).__name__
                record['cleanup_events'] = cleanup_events
                record.update(_trace_fields(run, item['trial_id']))
                record['classification'] = classify_record(record)
                record['state'] = 'finished'
                record['finished'] = time.time()
                record['cost'] = estimate_cost(record)
                update_attempt(ledger_path, identity, summary['run_id'], index, record)
                scrub_provider_errors(run, secrets)
                records = _load_ledger(ledger_path)['attempts']
                summary.update(summarize_records(records))
                runtime.write_json(run/'summary.json', summary)
                _assert_old_unchanged(old_path, _load_ledger(ledger_path)['old_campaign_identity'])
            if record['create_uncertain'] or not record['cleanup_empty']:
                summary['stopping_reason'] = 'cleanup_or_creation_uncertain'
                break
        else:
            summary['stopping_reason'] = 'planned_trials_terminal'
    finally:
        daytona_pilot.EVENT_SINK = None
        try:
            await client.close()
        finally:
            manager._client = None


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


def main():
    check_host(sys.argv, platform.system(), platform.machine())
    with runtime.run_context('m1', WORK_SECONDS, artifact_stage='m1') as (run, state, deadline, log):
        summary = {'schema_version': 2, 'pilot_kind': 'real-model-no-training',
                   'run_id': run.name, 'records': [], 'planned_trials': [],
                   'stopping_reason': 'preflight', 'gpu_run': False,
                   'model_revision': SETTINGS['revision'], 'cost_rates': COST_RATES,
                   'm2_a_passed': False}
        runtime.write_json(run/'summary.json', summary)
        ledger_path = ROOT/CAMPAIGN_REL
        old_path = ROOT/OLD_CAMPAIGN_REL
        reserved = False
        try:
            config = json.loads(CONFIG_PATH.read_text())
            validate_agent_contract(config)
            if digest(SOURCE/'lab_runtime/task_pool_v2.py') != config['task_generator_sha256']:
                raise ValueError('registered task generator changed')
            from .task_pool_v2 import materialize_self_pool
            manifest = materialize_self_pool(run/'pool')
            if digest(run/'pool/manifest.json') != config['generated_manifest_sha256']:
                raise ValueError('generated self48 manifest changed')
            summary['planned_trials'] = select_trials(config, manifest)
            summary['source_git_sha'] = runtime.source_identity(deadline, log)
            summary['config_sha256'] = digest(CONFIG_PATH)
            summary['task_generator_sha256'] = config['task_generator_sha256']
            summary['generated_manifest_sha256'] = config['generated_manifest_sha256']
            identity = {key: summary[key] for key in ('source_git_sha', 'config_sha256')}
            reserve_campaign(ledger_path, old_path, identity, run.name,
                             summary['planned_trials'])
            reserved = True
            summary['campaign_path'] = str(ledger_path)
            values = read_credentials(ROOT/'secrets/daytona.env')
            runtime.write_json(run/'summary.json', summary)
            with provider_credentials(values):
                with runtime.managed_server(run, state, deadline, log,
                                            allow_previous_source=True) as server:
                    summary['gpu_run'] = True
                    asyncio.run(execute_trials(run, summary, ledger_path, old_path,
                                               identity, deadline, values))
                    if server.poll() is not None:
                        raise RuntimeError('owned model server exited')
        except BaseException as exc:
            summary['stopping_reason'] = type(exc).__name__
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError('M1 v2 model pilot stopped: '+type(exc).__name__) from None
        finally:
            summary['gpu_cleanup_confirmed'] = state.get('owned_group_gone') is True
            summary['model_content_verified_after'] = state.get('post_model_check') == 'verified'
            summary['pilot_execution_complete'] = (
                summary.get('attempts_terminal_and_classified') is True
                and summary.get('gpu_cleanup_confirmed') is True
                and summary.get('model_content_verified_after') is True)
            summary['m2_a_passed'] = False
            runtime.write_json(run/'summary.json', summary)
            if reserved:
                ledger = finish_campaign(ledger_path, identity, run.name,
                                         str(run/'summary.json'),
                                         digest(run/'summary.json'))
                _assert_old_unchanged(old_path, ledger['old_campaign_identity'])
            state['summary_path'] = str(run/'summary.json')
            state['batch_completed'] = summary.get('pilot_execution_complete') is True
