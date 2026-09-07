"""Campaign accounting and prerequisite tests, using only local fake evidence."""
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class CampaignTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_campaign'), 'campaign ledger missing')
        return importlib.import_module('lab_runtime.m1_campaign')

    def identity(self):
        return {'pool_manifest_sha256': 'a'*64, 'agent_source_sha256': 'b'*64,
                'model_revision': 'c'*40, 'serving_lock_sha256': 'd'*64}

    def plan(self, mode='pilot', index=0):
        return dict(trial_id=f'{mode}-t{index}', instance=f't{index}', family=f'f{index%4}',
                    mode=mode, agent='m1' if mode != 'controls' else 'nop',
                    repetition=0, seed=100+index)

    def test_active_run_and_identity_changes_fail_closed(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                m.reserve_run(path, self.identity(), 'run1', 'pilot', [self.plan()])
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'run2', 'pilot', [self.plan()])
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, dict(self.identity(), agent_source_sha256='e'*64), 'run3', 'pilot', [self.plan()])

    def test_release_only_unstarted_and_latch_uncertainty(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                plans = [self.plan(index=i) for i in range(4)]
                reserved = m.reserve_run(path, self.identity(), 'run1', 'pilot', plans)
                m.mark_attempt(path, self.identity(), 'run1', reserved[0]['trial_id'])
                m.finish_run(path, self.identity(), 'run1', {'records': [], 'cleanup_confirmed_empty': False, 'gpu_run': False})
                ledger = json.loads(path.read_text())
                self.assertEqual(ledger['attempted_trials'], 1)
                self.assertEqual(ledger['reserved_trials'], 0)
                self.assertTrue(ledger['unresolved_cleanup'])
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'run2', 'pilot', plans)

    def test_formal_duplicate_and_pilot_caps(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                for index in range(2):
                    plans = m.reserve_run(path, self.identity(), f'run{index}', 'pilot', [self.plan(index=i) for i in range(4)])
                    for plan in plans:
                        m.mark_attempt(path, self.identity(), f'run{index}', plan['trial_id'])
                    m.finish_run(path, self.identity(), f'run{index}', {'records': [dict(plan, create_events=[], cleanup={'confirmed_empty': True}) for plan in plans], 'cleanup_confirmed_empty': True, 'gpu_run': False})
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'run3', 'pilot', [self.plan()])

    def test_screen_requires_pilot_and_all_controls(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(root/'campaign.json', self.identity(), 'screen', 'screen', [self.plan('screen')])

    def test_complete_evidence_unlocks_screen_but_duplicate_control_refused(self):
        m = self.module()
        from lab_runtime import m1_run
        import hashlib
        manifest_path = m1_run.SOURCE/'configs/m1-pool-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        identity = dict(self.identity(), pool_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                for mode, count in [('pilot', 4), ('controls', 64)]:
                    plans = m1_run.select_trials(dict(mode=mode, start=0, count=count, max_trials=count, concurrency=1), manifest)
                    plans = m.reserve_run(path, identity, mode, mode, plans)
                    records = []
                    for plan in plans:
                        m.mark_attempt(path, identity, mode, plan['trial_id'])
                        records.append(dict(plan, valid_for_denominator=True, usage={'complete': True},
                            rewards={'reward': 0 if plan['agent'] == 'nop' else 1},
                            cleanup={'confirmed_empty': True}, create_events=[], exception_type=None, exception_info_type=None))
                    m.finish_run(path, identity, mode, dict(records=records, cleanup_confirmed_empty=True,
                        gpu_run=mode == 'pilot', gpu_cleanup_confirmed=True,
                        stopping_reason='planned_slice_completed', model_content_verified_after=True))
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, identity, 'duplicate-control', 'controls', plans[:1])
                screen = m1_run.select_trials(dict(mode='screen', start=0, count=64, max_trials=64, concurrency=1), manifest)
                self.assertEqual(len(m.reserve_run(path, identity, 'screen', 'screen', screen)), 64)

    def test_unknown_ledger_not_silently_reset(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                path.write_text('{}')
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'run', 'pilot', [self.plan()])
                self.assertEqual(path.read_text(), '{}')

    def test_total_creation_and_batch_caps_are_persistent(self):
        m = self.module()
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(m.runtime, 'ROOT', root):
                path = root/'campaign.json'
                for batch in range(4):
                    plans = [self.plan('controls', batch*64+index) for index in range(64)]
                    plans = m.reserve_run(path, self.identity(), f'run{batch}', 'controls', plans)
                    for plan in plans:
                        m.mark_attempt(path, self.identity(), f'run{batch}', plan['trial_id'])
                    m.finish_run(path, self.identity(), f'run{batch}', dict(records=[dict(plan, cleanup={'confirmed_empty': True}) for plan in plans], cleanup_confirmed_empty=True, gpu_run=False))
                ledger = json.loads(path.read_text())
                self.assertEqual(ledger['attempted_trials'], 256)
                self.assertLessEqual(ledger['reserved_budget_usd'], 10)
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'too-many', 'controls', [self.plan('controls', 256+i) for i in range(9)])
                for index in range(2):
                    run = f'unstarted{index}'
                    m.reserve_run(path, self.identity(), run, 'controls', [self.plan('controls', 256)])
                    m.finish_run(path, self.identity(), run, dict(records=[], cleanup_confirmed_empty=True, gpu_run=False))
                with self.assertRaises(m.CampaignBlocked):
                    m.reserve_run(path, self.identity(), 'seventh-batch', 'controls', [self.plan('controls', 256)])
