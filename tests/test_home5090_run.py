import importlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch


class FixedRuns(unittest.TestCase):
    def test_mirror_install_preserves_frozen_hashes_and_own_venv(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'install_environment'))
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve()
            venv = run/'env'
            with patch.object(m, 'VENV', venv), patch.object(m, 'command', return_value='') as command:
                m.install_environment(run, m.time.monotonic()+10, Mock())
            argv = [call.args[0] for call in command.call_args_list]
            export = next(a for a in argv if 'export' in a)
            for flag in ('--frozen', '--offline', '--no-dev', '--no-emit-project'):
                self.assertIn(flag, export)
            self.assertEqual(export[export.index('--output-file')+1], str(run/'requirements.txt'))
            sync = next(a for a in argv if 'sync' in a)
            self.assertEqual(sync[1:3], ['pip','sync'])
            self.assertIn('--require-hashes', sync)
            self.assertIn('--no-build', sync)
            self.assertEqual(sync[sync.index('--python')+1], str(venv/'bin/python'))
            self.assertEqual(sync[sync.index('--default-index')+1], 'https://mirrors.aliyun.com/pypi/simple/')
            self.assertTrue(any(a[1:3]==['pip','check'] for a in argv))
            self.assertFalse(any('--system' in a or '--refresh' in a for a in argv))

    def module(self):
        spec = importlib.util.find_spec('lab_runtime.home5090_run')
        self.assertIsNotNone(spec, 'fixed runner is missing')
        return importlib.import_module('lab_runtime.home5090_run')

    def test_host_rejected_before_writes(self):
        m = self.module()
        with patch.object(m.platform, 'system', return_value='Darwin'), patch.object(m, 'run_context') as ctx:
            with self.assertRaises(ValueError):
                m.prepare()
            ctx.assert_not_called()

    def test_budget_exhaustion(self):
        m = self.module()
        with patch.object(m.time, 'monotonic', return_value=100):
            with self.assertRaises(TimeoutError):
                m.remaining(100)
            self.assertEqual(m.remaining(102), 2)

    def test_subprocess_failure_has_no_retry(self):
        m = self.module()
        child = Mock(pid=987654)
        child.communicate.return_value = ('failed', None)
        child.returncode = 3
        with patch.object(m.subprocess, 'Popen', return_value=child) as start, patch.object(m, 'stop_group') as stop:
            with self.assertRaises(RuntimeError):
                m.command(['false'], m.time.monotonic()+10, Mock())
            self.assertEqual(start.call_count, 1)
            stop.assert_called_once_with(child)

    def test_timeout_cleans_only_created_child(self):
        m = self.module()
        child = Mock(pid=987654)
        child.communicate.side_effect = m.subprocess.TimeoutExpired('x', 1)
        with patch.object(m.subprocess, 'Popen', return_value=child), patch.object(m, 'stop_group') as stop:
            with self.assertRaises(m.subprocess.TimeoutExpired):
                m.command(['x'], m.time.monotonic()+10, Mock())
            stop.assert_called_once_with(child)

    def test_prepare_state_fails_closed(self):
        m = self.module()
        for state in ({}, {'success': False}, {'success': True, 'revision': 'wrong'}):
            with self.assertRaises(ValueError):
                m.validate_prepared(state, 'lock')

    def test_failure_is_saved_and_lock_released(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'lab'
            with patch.object(m, 'ROOT', root), patch.object(m, 'VENV', root/'envs/m0-serving'), patch.object(m, 'MODEL', root/'models/revision'):
                with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                    with m.run_context('prepare', 10):
                        raise RuntimeError('deliberate')
                state = m.json.loads((root/'artifacts/m0/home5090/prepare-latest.json').read_text())
                self.assertFalse(state['success'])
                self.assertEqual(state['error_type'], 'RuntimeError')
                with m.run_context('probe', 10):
                    pass

    def test_cleanup_targets_only_owned_group(self):
        m = self.module()
        child = Mock(pid=987654)
        with patch.object(m, 'group_exists', side_effect=[True, False]), patch.object(m.os, 'killpg') as kill:
            m.stop_group(child)
            kill.assert_called_once_with(child.pid, m.signal.SIGTERM)

    def test_unknown_existing_environment_refused(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'claim_paths'), 'managed path ownership check missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(m, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'unknown'):
                    m.claim_paths(root/'ownership.json', {'environment': True})

    def test_model_requires_config_and_weights(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'model_manifest'), 'model content validator missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root/'random.txt').write_text('not a model')
            with patch.object(m, 'ROOT', root), patch.object(m, 'MODEL', root):
                with self.assertRaises(ValueError):
                    m.model_manifest(m.time.monotonic()+10)

    def test_model_response_identity(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'validate_models'), 'model API identity validator missing')
        for result in ({}, {'data': [{'id': 'other', 'root': str(m.MODEL)}]}):
            with self.assertRaises(ValueError):
                m.validate_models(result)

    def test_prepare_source_mismatch_rejected(self):
        m = self.module()
        state = {'success': True, 'revision': m.SETTINGS['revision'], 'lock_sha256': 'lock', 'source_git_sha': 'old'}
        with self.assertRaises(ValueError):
            m.validate_prepared(state, 'lock', 'new')

    def test_gitkeep_skeleton_is_cold_but_unknown_file_is_not(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'has_runtime_content'), 'cold skeleton detection missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'.gitkeep').touch()
            self.assertFalse(m.has_runtime_content(root))
            (root/'unknown').touch()
            self.assertTrue(m.has_runtime_content(root))

    def test_new_lock_preserves_owned_old_environment(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root/'ownership.json'
            old = root/'envs/old'
            with patch.object(m, 'ROOT', root), patch.object(m, 'MODEL', root/'model'), patch.object(m, 'VENV', old):
                m.claim_paths(marker, {})
                with patch.object(m, 'VENV', root/'envs/new'):
                    m.claim_paths(marker, {'environment': False, 'model': True, 'cache': True})
            self.assertIn(str(old), m.json.loads(marker.read_text())['paths'])

    def test_foreign_listener_rejected(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'assert_listener_owned'), 'socket ownership check missing')
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc/'net').mkdir()
            (proc/'net/tcp').write_text('header\n 0: 0100007F:4935 00000000:0000 0A 0:0 00:0 0 1000 0 1234\n')
            with self.assertRaises(ValueError):
                m.assert_listener_owned(987654, proc)

    def test_scripts_disable_source_bytecode_writes(self):
        m = self.module()
        for name in ('prepare_environment.py', 'qwen3_4b_m0_probe.py'):
            text = (m.SOURCE/'scripts'/name).read_text()
            self.assertIn('sys.dont_write_bytecode = True', text)

    def test_owned_listener_is_accepted(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc/'net').mkdir()
            (proc/'net/tcp').write_text('header\n 0: 0100007F:4935 00000000:0000 0A 0:0 00:0 0 1000 0 1234\n')
            (proc/'42/fd').mkdir(parents=True)
            (proc/'42/fd/3').symlink_to('socket:[1234]')
            with patch.object(m.os, 'getpgid', return_value=99):
                m.assert_listener_owned(99, proc)

    def test_disk_usage_captures_three_managed_paths(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'asset_usage'), 'actual disk usage missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(m, 'ROOT', root), patch.object(m, 'VENV', root/'env'), patch.object(m, 'MODEL', root/'model'), patch.object(m, 'command', return_value='1024\tpath\n') as command:
                usage = m.asset_usage(m.time.monotonic()+10, Mock())
                self.assertEqual(usage, {'environment': 1024, 'model': 1024, 'cache': 1024})
                self.assertEqual(command.call_count, 3)

    def test_lock_failure_does_not_replace_latest(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base = root/'artifacts/m0/home5090'
            base.mkdir(parents=True)
            latest = base/'prepare-latest.json'
            latest.write_text('{"previous": true}')
            with (base/'run.lock').open('a') as lock:
                m.fcntl.flock(lock, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
                with patch.object(m, 'ROOT', root), patch.object(m, 'VENV', root/'env'), patch.object(m, 'MODEL', root/'model'):
                    with self.assertRaises(BlockingIOError):
                        with m.run_context('prepare', 10):
                            self.fail('contender acquired locked run')
            self.assertEqual(latest.read_text(), '{"previous": true}')
            self.assertEqual(len(list(base.glob('prepare-*/state.json'))), 1)

    def test_monitor_join_failure_still_stops_child(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'cleanup_child'), 'protected child cleanup missing')
        child, monitor, stop = Mock(), Mock(), Mock()
        monitor.join.side_effect = RuntimeError('join failed')
        with patch.object(m, 'stop_group') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'join failed'):
                m.cleanup_child(child, monitor, stop)
            terminate.assert_called_once_with(child)

    def test_cleanup_disables_deadline_and_defers_term(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'cleanup_child'), 'protected child cleanup missing')
        child = Mock()
        original = m.signal.getsignal(m.signal.SIGTERM)
        def during_cleanup(owned):
            self.assertEqual(m.signal.getitimer(m.signal.ITIMER_REAL)[0], 0)
            m.signal.getsignal(m.signal.SIGTERM)(m.signal.SIGTERM, None)
        with patch.object(m, 'stop_group', side_effect=during_cleanup) as terminate:
            with self.assertRaisesRegex(RuntimeError, 'SIGTERM'):
                m.cleanup_child(child)
            terminate.assert_called_once_with(child)
        self.assertEqual(m.signal.getsignal(m.signal.SIGTERM), original)

    def test_non5090_identity_is_rejected(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'validate_gpu_identity'), 'GPU object gate missing')
        with self.assertRaises(ValueError):
            m.validate_gpu_identity('GPU-abc, NVIDIA H100, 595.0, 80000')
        self.assertEqual(m.validate_gpu_identity('GPU-abc, NVIDIA GeForce RTX 5090, 595.0, 32607')['total_memory_mib'], 32607)

    def test_source_identity_rejects_branch_checkout(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'source_identity'), 'detached clean source check missing')
        with patch.object(m, 'command', return_value='main\n'):
            with self.assertRaises(ValueError):
                m.source_identity(m.time.monotonic()+10, Mock())


if __name__ == '__main__':
    unittest.main()
