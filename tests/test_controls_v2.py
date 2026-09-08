import unittest
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from lab_runtime.controls_v2 import admit, make_trial_config, valid_result


class ControlsTests(unittest.TestCase):
    def test_own_peak_rss_platform_units(self):
        from lab_runtime.controls_v2 import peak_rss_bytes
        self.assertEqual(peak_rss_bytes(1234, 'darwin'), 1234)
        self.assertEqual(peak_rss_bytes(1234, 'linux'), 1234*1024)
        with self.assertRaises(ValueError):
            peak_rss_bytes(1234, 'unknown')

    def test_config_rejects_unregistered_values(self):
        from lab_runtime.controls_v2 import validate_config
        source = Path(__file__).resolve().parents[1]
        cfg = json.loads((source/'configs/m1-controls-v2.json').read_text())
        validate_config(cfg)
        for key in ('controller_seconds', 'trial_seconds', 'cpus', 'ttl_minutes', 'max_attempts'):
            with self.assertRaises(ValueError):
                validate_config(dict(cfg, **{key: cfg[key]+1}))

    def test_identity_binds_live_hooks_and_entry(self):
        from lab_runtime.controls_v2 import source_identity
        source = Path(__file__).resolve().parents[1]
        identity = source_identity(source)
        for path in ('lab_runtime/swe_hooks.py', 'lab_runtime/swe_workspace.py',
                     'lab_runtime/m1_run.py', 'scripts/m1_controls.py'):
            self.assertIn(path, identity)

    def test_admission_serial_and_no_retry(self):
        admit({'attempts': []}, 0)
        for state in ('active', 'failed', 'uncertain'):
            with self.assertRaises(ValueError):
                admit({'attempts': [{'state': state}]}, 1)
        with self.assertRaises(ValueError):
            admit({'attempts': []}, 1)
        with self.assertRaises(ValueError):
            admit({'attempts': [{'state': 'passed'}]*6}, 6)

    def test_real_harbor_config(self):
        from pathlib import Path
        c = make_trial_config(Path('/tmp/task'), Path('/tmp/run'), 'nop', {'m1_v2_run': 'v2', 'm1_v2_attempt': '0'})
        self.assertEqual(c.environment.override_gpus, 0)
        self.assertEqual(c.environment.override_memory_mb, 8192)
        self.assertFalse(c.environment.kwargs['network_block_all'])
        self.assertEqual(c.agent.name, 'nop')

    def test_invalid_zero_is_never_nop_pass(self):
        base = dict(exception_type=None, reward=0, cleanup_empty=True,
                    create_uncertain=False, phase='verification_ready')
        self.assertTrue(valid_result(base, 'nop', None))
        for key, value in [('exception_type', 'DependencyError'), ('cleanup_empty', False), ('create_uncertain', True)]:
            self.assertFalse(valid_result(dict(base, **{key: value}), 'nop', None))
        self.assertFalse(valid_result(base, 'nop', {'protocol_complete': False}))
        self.assertTrue(valid_result(base, 'nop', {'protocol_complete': True, 'reward': 0, 'runtime_errors': [], 'owned_process_group_stopped': True}))

    def test_local_entry_exists_and_no_modal_secret(self):
        path = Path(__file__).resolve().parents[1]/'scripts/m1_controls.py'
        self.assertTrue(path.is_file())
        self.assertNotIn('modal.Secret', path.read_text())


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        source = Path(__file__).resolve().parents[1]
        if not (source/'data/m1/v2/tasks/data_csv-train-00/task.toml').is_file():
            self.skipTest('ignored unified200 generated task assets unavailable')

    async def test_client_setup_is_inside_work_deadline(self):
        from lab_runtime.controls_v2 import _execute
        from harbor.environments.daytona.environment import DaytonaClientManager
        source = Path(__file__).resolve().parents[1]
        async def stuck():
            await asyncio.sleep(30)
        with tempfile.TemporaryDirectory() as temp, patch.object(DaytonaClientManager, 'get_instance', side_effect=stuck):
            result = await _execute(temp, source, lambda: None, 0,
                work_deadline=asyncio.get_running_loop().time()+0.03,
                deadline=asyncio.get_running_loop().time()+1)
            self.assertEqual(result['exception_type'], 'TimeoutError')
            self.assertEqual(result['state'], 'uncertain')

    async def test_owned_sdk_closed_and_singleton_cleared_after_admission_failure(self):
        from lab_runtime.controls_v2 import execute
        from harbor.environments.daytona.environment import DaytonaClientManager
        source = Path(__file__).resolve().parents[1]
        class Client:
            closed = False
            def list(self, *args, **kwargs):
                async def values():
                    raise ConnectionError()
                    yield
                return values()
            async def close(self):
                self.closed = True
        client = Client()
        async def get_client():
            return client
        manager = SimpleNamespace(_client=client, get_client=get_client)
        async def get_instance():
            return manager
        with tempfile.TemporaryDirectory() as temp, patch.object(DaytonaClientManager, 'get_instance', side_effect=get_instance):
            result = await execute(temp, source, lambda: None, 0)
            self.assertTrue(result['sdk_closed'])
            self.assertTrue(client.closed)
            self.assertIsNone(manager._client)

    async def test_complete_self_control_and_next_admission(self):
        from lab_runtime.controls_v2 import execute
        from lab_runtime import daytona_v2
        from daytona import DaytonaNotFoundError
        source = Path(__file__).resolve().parents[1]
        class Client:
            def list(self, *args, **kwargs):
                async def values():
                    if False:
                        yield
                return values()
            async def get(self, sid):
                raise DaytonaNotFoundError('gone')
        class Environment:
            async def exec(self, **kwargs):
                return SimpleNamespace(return_code=0)
        class Trial:
            def __init__(self, config):
                self.config = config
                self.agent_environment = Environment()
                self.hooks = []
            def add_hook(self, event, callback):
                self.hooks.append(callback)
            async def run(self):
                daytona_v2.EVENT_SINK({'status': 'created', 'sandbox_id': 'owned-id'})
                for hook in self.hooks:
                    await hook(None)
                output = self.config.trials_dir/'control/verifier'
                output.mkdir(parents=True)
                (output/'result.json').write_text(json.dumps({'passed': False, 'detail': 'missing_or_invalid_answer_or_input'}))
                return SimpleNamespace(exception_info=None, verifier_result=SimpleNamespace(rewards={'reward': 0}))
        async def factory(config):
            return Trial(config)
        with tempfile.TemporaryDirectory() as temp:
            result = await execute(temp, source, lambda: None, 0, trial_factory=factory, client=Client())
            self.assertEqual(result['state'], 'passed')
            ledger = json.loads((Path(temp)/'artifacts/m1/v2/controls/campaign.json').read_text())
            admit(ledger, 1)

    async def test_unknown_create_and_cleanup_failure_block_next_attempt(self):
        from lab_runtime.controls_v2 import execute
        from lab_runtime import daytona_v2
        source = Path(__file__).resolve().parents[1]
        class Client:
            def list(self, *args, **kwargs):
                async def values():
                    if False:
                        yield None
                return values()
        class Trial:
            agent_environment = SimpleNamespace(v2_create_events=[])
            def add_hook(self, *args):
                pass
            async def run(self):
                self.agent_environment.v2_create_events = [{'status': 'uncertain', 'sandbox_id': None}]
                daytona_v2.EVENT_SINK({'status': 'uncertain', 'sandbox_id': None})
                raise TimeoutError()
        async def factory(config):
            self.assertEqual(json.loads(ledger.read_text())['attempts'][0]['state'], 'active')
            return Trial()
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp)/'artifacts/m1/v2/controls/campaign.json'
            result = await execute(temp, source, lambda: None, 0, trial_factory=factory, client=Client())
            self.assertEqual(result['state'], 'uncertain')
            self.assertTrue(result['cleanup_empty'])
            with self.assertRaises(ValueError):
                await execute(temp, source, lambda: None, 1, trial_factory=factory, client=Client())

    async def test_cleanup_never_deletes_mismatched_owner(self):
        from lab_runtime.controls_v2 import cleanup
        class Client:
            def list(self, *args, **kwargs):
                async def values():
                    yield SimpleNamespace(id='foreign', labels={'m1_v2_run': 'other'})
                return values()
            async def delete(self, *args, **kwargs):
                self.fail('foreign deletion')
        with self.assertRaises(RuntimeError):
            await cleanup(Client(), {'m1_v2_run': 'ours'}, set(), lambda e: None)

    async def test_cleanup_failure_is_uncertain_even_without_create(self):
        from lab_runtime.controls_v2 import execute
        source = Path(__file__).resolve().parents[1]
        class Client:
            def list(self, *args, **kwargs):
                async def values():
                    raise ConnectionError()
                    yield
                return values()
        with tempfile.TemporaryDirectory() as temp:
            result = await execute(temp, source, lambda: None, 0, client=Client())
            self.assertEqual(result['state'], 'uncertain')
            self.assertEqual(result['cleanup_error_type'], 'ConnectionError')

    async def test_factory_uncertainty_is_retained_when_no_trial_returned(self):
        from lab_runtime.controls_v2 import execute
        from lab_runtime import daytona_v2
        source = Path(__file__).resolve().parents[1]
        class Client:
            def list(self, *args, **kwargs):
                async def values():
                    if False:
                        yield
                return values()
        async def factory(config):
            daytona_v2.EVENT_SINK({'status': 'uncertain', 'sandbox_id': None})
            raise TimeoutError()
        with tempfile.TemporaryDirectory() as temp:
            result = await execute(temp, source, lambda: None, 0, client=Client(), trial_factory=factory)
            self.assertTrue(result['create_uncertain'])
            self.assertEqual(result['state'], 'uncertain')

    async def test_final_create_has_ttl_and_single_attempt(self):
        from lab_runtime import daytona_v2
        env = object.__new__(daytona_v2.ControlsDaytonaEnvironment)
        params = SimpleNamespace()
        calls = []
        async def create(instance, actual, client):
            calls.append(actual)
            self.assertEqual(actual.ttl_minutes, 60)
            self.assertFalse(actual.public)
            self.assertFalse(actual.network_block_all)
            self.assertEqual(actual.auto_delete_interval, 0)
            raise TimeoutError()
        events = []
        with patch.object(daytona_v2, 'EVENT_SINK', events.append), patch.object(daytona_v2.DaytonaEnvironment._create_sandbox, 'retry_with', return_value=create):
            with self.assertRaises(TimeoutError):
                await env._create_sandbox(params)
        self.assertEqual(len(calls), 1)
        self.assertEqual(events[-1]['status'], 'uncertain')
