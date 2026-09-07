"""M1 orchestration safety tests; no real provider/model calls."""
import importlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class M1RunTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_run'), 'M1 runner missing')
        return importlib.import_module('lab_runtime.m1_run')

    def test_fixed_selection_and_limits(self):
        m = self.module()
        manifest = json.loads((m.SOURCE/'configs/m1-pool-manifest.json').read_text())
        config = dict(mode='pilot', start=0, count=4, max_trials=4, concurrency=1)
        trials = m.select_trials(config, manifest)
        self.assertEqual(len(trials), 4)
        self.assertEqual(len({trial['family'] for trial in trials}), 4)
        self.assertTrue(all(trial['split'] == 'train' for trial in trials))
        for bad in (dict(config, concurrency=2), dict(config, count=5), dict(config, start=-1), dict(config, retries=1)):
            with self.assertRaises(ValueError):
                m.select_trials(bad, manifest)

    def test_private_config_permissions_and_keys(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()/'daytona.env'
            path.write_text('DAYTONA_API_KEY=dummy-unit-test\n')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                m.read_credentials(path)
            path.chmod(0o600)
            self.assertEqual(m.read_credentials(path), {'DAYTONA_API_KEY': 'dummy-unit-test'})
            path.write_text('UNEXPECTED=dummy\nDAYTONA_API_KEY=dummy\n')
            with self.assertRaises(ValueError):
                m.read_credentials(path)
            link = path.with_name('link')
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                m.read_credentials(link)

    def test_bootstrap_host_rejects_before_exec(self):
        m = self.module()
        with patch.object(m.platform, 'system', return_value='Darwin'), patch.object(m.os, 'execve') as execute:
            with self.assertRaises(ValueError):
                m.bootstrap()
            execute.assert_not_called()

    def test_m1_context_keeps_m0_state_untouched(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root), patch.object(m.runtime, 'VENV', root/'env'), patch.object(m.runtime, 'MODEL', root/'model'):
                with m.runtime.run_context('m1', 10, artifact_stage='m1') as (run, state, deadline, log):
                    self.assertIn('/artifacts/m1/home5090/', str(run))
                self.assertTrue((root/'artifacts/m1/home5090/m1-latest.json').exists())
                self.assertFalse((root/'artifacts/m0/home5090/m1-latest.json').exists())

    def test_schedule_and_official_trial_config(self):
        m = self.module()
        manifest = json.loads((m.SOURCE/'configs/m1-pool-manifest.json').read_text())
        for mode, start, count in [('controls', 0, 64), ('screen', 64, 64), ('eval', 0, 48)]:
            plans = m.select_trials(dict(mode=mode, start=start, count=count, max_trials=64, concurrency=1), manifest)
            self.assertEqual(len(plans), count)
            self.assertEqual(len({row['trial_id'] for row in plans}), count)
            if mode == 'eval':
                self.assertTrue(all(row['temperature'] == .6 and row['split'] == 'eval' for row in plans))
            config = m.make_trial_config(Path('/unused-unit'), plans[0], {'lab': 'sandbox-rl-mopd', 'm1_run': 'unit'})
            self.assertTrue(config.environment.delete)
            self.assertEqual(config.environment.override_cpus, 1)
            self.assertTrue(config.environment.kwargs['network_block_all'])
            self.assertFalse(config.environment.kwargs['auto_snapshot'])
            self.assertNotIn('DAYTONA_API_KEY', config.model_dump_json())

    def test_loopback_http_checks_owned_listener_before_connect(self):
        m = self.module()
        token = m.runtime.OWNED_SERVER_PGID.set(1234)
        try:
            with patch.object(m.runtime, 'assert_listener_owned', side_effect=ValueError('foreign')) as ownership, patch.object(m.runtime.http.client, 'HTTPConnection') as connect:
                with self.assertRaises(ValueError):
                    m.runtime.http_json('POST', '/v1/chat/completions', {}, m.time.monotonic()+5)
                ownership.assert_called_once_with(1234)
                connect.assert_not_called()
        finally:
            m.runtime.OWNED_SERVER_PGID.reset(token)

    def test_missing_credentials_finalizes_zero_attempt_reservation(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m, 'ROOT', root), patch.object(m.runtime, 'ROOT', root), patch.object(m.runtime, 'VENV', root/'env'), patch.object(m.runtime, 'MODEL', root/'model'), patch.object(m, 'check_host'), patch.object(m.runtime, 'source_identity', return_value='unit-source'), patch.object(m, 'read_credentials', side_effect=ValueError('missing controlled file')), patch.object(m.runtime, 'managed_server') as server:
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, 'M1 batch stopped'):
                        m.main()
                server.assert_not_called()
            ledger = json.loads((root/'artifacts/m1/campaign.json').read_text())
            self.assertEqual(len(ledger['runs']), 2)
            self.assertEqual(ledger['attempted_trials'], 0)
            self.assertEqual(ledger['reserved_trials'], 0)
            self.assertFalse(ledger['unresolved_cleanup'])
            self.assertTrue(all(run['status'] == 'finished' for run in ledger['runs']))


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_run'), 'M1 runner missing')
        return importlib.import_module('lab_runtime.m1_run')

    async def test_cleanup_deletes_only_exact_labels_and_confirms_empty(self):
        m = self.module()
        labels = {'lab': 'sandbox-rl-mopd', 'm1_run': 'unit'}
        sandbox = SimpleNamespace(id='own-id', labels=labels)
        calls = 0
        async def listing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield sandbox
        client = SimpleNamespace(list=listing, delete=AsyncMock())
        events = []
        await m.cleanup_sandboxes(client, labels, events, m.time.monotonic()+5)
        client.delete.assert_awaited_once()
        self.assertTrue(client.delete.call_args.kwargs['wait'])
        self.assertEqual(events[-1]['status'], 'empty')

    async def test_cleanup_foreign_result_fails_without_delete(self):
        m = self.module()
        async def listing(*args, **kwargs):
            yield SimpleNamespace(id='foreign', labels={'lab': 'someone-else'})
        client = SimpleNamespace(list=listing, delete=AsyncMock())
        with self.assertRaises(m.CleanupUncertain):
            await m.cleanup_sandboxes(client, {'lab': 'sandbox-rl-mopd', 'm1_run': 'unit'}, [], m.time.monotonic()+5)
        client.delete.assert_not_awaited()

    async def test_three_create_failures_stop_without_retry(self):
        m = self.module()
        from harbor.trial.trial import Trial
        from harbor.environments.daytona.environment import DaytonaClientManager
        manifest = json.loads((m.SOURCE/'configs/m1-pool-manifest.json').read_text())
        plans = m.select_trials(dict(mode='controls', start=0, count=4, max_trials=4, concurrency=1), manifest)
        result = SimpleNamespace(exception_info=SimpleNamespace(exception_type='CreateError'), verifier_result=None, agent_result=None)
        trial = SimpleNamespace(run=AsyncMock(return_value=result), result=result,
            agent_environment=SimpleNamespace(m1_create_events=[{'status': 'failed', 'sandbox_id': None, 'error_type': 'CreateError', 'start_monotonic': m.time.monotonic()}]))
        client = SimpleNamespace(close=AsyncMock())
        manager = SimpleNamespace(get_client=AsyncMock(return_value=client), _client=client)
        async def cleanup(client, labels, events, deadline):
            events.append({'status': 'empty', 'monotonic': m.time.monotonic()})
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            summary = dict(planned_trials=plans, records=[], cleanup_events=[], cleanup_confirmed_empty=False)
            with patch.object(m.runtime, 'ROOT', root), patch.object(Trial, 'create', AsyncMock(return_value=trial)) as create, patch.object(DaytonaClientManager, 'get_instance', AsyncMock(return_value=manager)), patch.object(m, 'cleanup_sandboxes', side_effect=cleanup):
                await m.run_batch(root, summary, m.time.monotonic()+3600, {})
            self.assertEqual(create.await_count, 3)
            self.assertEqual(summary['stopping_reason'], 'three_consecutive_create_failures')
            self.assertEqual(summary['valid_denominator'], 0)
            self.assertTrue(all(row['rewards'] is None for row in summary['records']))
            self.assertTrue(all(row['exception_info_type'] == 'CreateError' for row in summary['records']))

    async def test_cleanup_uncertain_stops_next_create(self):
        m = self.module()
        from harbor.trial.trial import Trial
        from harbor.environments.daytona.environment import DaytonaClientManager
        manifest = json.loads((m.SOURCE/'configs/m1-pool-manifest.json').read_text())
        plans = m.select_trials(dict(mode='controls', start=0, count=4, max_trials=4, concurrency=1), manifest)
        result = SimpleNamespace(exception_info=None, verifier_result=SimpleNamespace(rewards={'reward': 0}), agent_result=None)
        trial = SimpleNamespace(run=AsyncMock(return_value=result), result=result, agent_environment=SimpleNamespace(m1_create_events=[]))
        client = SimpleNamespace(close=AsyncMock())
        manager = SimpleNamespace(get_client=AsyncMock(return_value=client), _client=client)
        attempts = 0
        async def cleanup(client, labels, events, deadline):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise m.CleanupUncertain('unit')
            events.append({'status': 'empty', 'monotonic': m.time.monotonic()})
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            summary = dict(planned_trials=plans, records=[], cleanup_events=[], cleanup_confirmed_empty=False)
            with patch.object(m.runtime, 'ROOT', root), patch.object(Trial, 'create', AsyncMock(return_value=trial)) as create, patch.object(DaytonaClientManager, 'get_instance', AsyncMock(return_value=manager)), patch.object(m, 'cleanup_sandboxes', side_effect=cleanup):
                with self.assertRaises(m.CleanupUncertain):
                    await m.run_batch(root, summary, m.time.monotonic()+3600, {})
            self.assertEqual(create.await_count, 1)
            self.assertFalse(summary['records'][0]['valid_for_denominator'])

    async def test_under_510_seconds_never_starts_trial(self):
        m = self.module()
        from harbor.trial.trial import Trial
        from harbor.environments.daytona.environment import DaytonaClientManager
        client = SimpleNamespace(close=AsyncMock())
        manager = SimpleNamespace(get_client=AsyncMock(return_value=client), _client=client)
        async def cleanup(client, labels, events, deadline):
            events.append({'status': 'empty', 'monotonic': m.time.monotonic()})
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            summary = dict(planned_trials=[{}], records=[], cleanup_events=[], cleanup_confirmed_empty=False)
            with patch.object(m.runtime, 'ROOT', root), patch.object(Trial, 'create', AsyncMock()) as create, patch.object(DaytonaClientManager, 'get_instance', AsyncMock(return_value=manager)), patch.object(m, 'cleanup_sandboxes', side_effect=cleanup):
                await m.run_batch(root, summary, m.time.monotonic()+509, {})
            create.assert_not_awaited()
            self.assertEqual(summary['stopping_reason'], 'budget_no_new_trials')
