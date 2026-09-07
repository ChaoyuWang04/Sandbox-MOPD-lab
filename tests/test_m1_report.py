import importlib.util
import unittest
import hashlib
from pathlib import Path
from lab_runtime.home5090 import SETTINGS


class M1ReportTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_report'), 'M1 report missing')
        from lab_runtime import m1_report
        return m1_report

    def fixtures(self):
        instances = [{'instance': f'{split}-{i}', 'domain': 'FS' if i < 8 else 'DATA', 'family': ('fs_logs','fs_inventory','data_csv','data_json')[i//4], 'split': split, 'seed': 100+i+(100 if split=='eval' else 0)} for split in ('train', 'eval') for i in range(16)]
        manifest = {'instances': instances}
        records = []
        for row in instances:
            for agent, reward in (('nop', 0), ('oracle', 1)):
                records.append(dict(row, seed=row['seed']*100, temperature=None, mode='controls', repetition=0, agent=agent, rewards={'reward': reward}, valid_for_denominator=True, cleanup={'confirmed_empty': True}, exception_type=None, exception_info_type=None))
            for rep in range(8 if row['split'] == 'train' else 3):
                records.append(dict(row, seed=row['seed']*100+rep, temperature=1.0 if row['split']=='train' else 0.6, mode='screen' if row['split']=='train' else 'eval', repetition=rep, agent='m1', rewards={'reward': rep % 2}, valid_for_denominator=True, cleanup={'confirmed_empty': True}, exception_type=None, exception_info_type=None, usage={'input': 100, 'output': 50, 'complete': True}, termination='completed'))
        contract = {'model_revision': SETTINGS['revision'], 'serving_lock_sha256': hashlib.sha256((Path(__file__).resolve().parents[1]/'environments/home5090/uv.lock').read_bytes()).hexdigest(), 'runtime_limits': {'max_model_len':16384,'output_tokens':4096,'rounds':10,'tool_seconds':20,'agent_seconds':180}}
        agent_sha = hashlib.sha256((Path(__file__).resolve().parents[1]/'lab_runtime/m1_agent.py').read_bytes()).hexdigest()
        summaries = [dict(contract, mode=mode, pool_manifest_sha256='pool', agent_source_sha256=agent_sha, cleanup_confirmed_empty=True, records=[r for r in records if r['mode']==mode]) for mode in ('controls', 'screen', 'eval')]
        return manifest, summaries

    def test_complete_matrix_builds_overfit_and_baseline(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        result = m.audit(manifest, summaries, 'pool')
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(len(result['overfit_16']), 16)
        self.assertEqual(result['eval_clean']['attempts'], 48)
        self.assertAlmostEqual(result['eval_clean']['pass_at_1'], 1/3)

    def test_infra_zero_is_not_a_model_failure(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        summaries[-1]['records'][0]['exception_info_type'] = 'SandboxTimeout'
        result = m.audit(manifest, summaries, 'pool')
        self.assertTrue(result['summary_matrix_complete'])
        self.assertTrue(result['requires_error_review'])
        self.assertEqual(len(result['overfit_16']), 16)
        self.assertEqual(result['eval_clean']['valid_attempts'], 47)
        self.assertEqual(result['eval_clean']['infra_errors'], 1)

    def test_duplicates_drift_and_cleanup_fail_closed(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        summaries[-1]['records'].append(summaries[-1]['records'][0])
        result = m.audit(manifest, summaries, 'pool')
        self.assertTrue(result['duplicate_attempts'])
        self.assertEqual(result['eval_clean']['attempts'], 49)
        manifest, summaries = self.fixtures()
        summaries[0]['pool_manifest_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'identity'):
            m.audit(manifest, summaries, 'pool')
        summaries[0]['pool_manifest_sha256'] = 'pool'
        summaries[0]['cleanup_confirmed_empty'] = False
        self.assertFalse(m.audit(manifest, summaries, 'pool')['evidence_complete'])

    def test_empty_or_missing_evidence_never_passes(self):
        m = self.module()
        manifest, _ = self.fixtures()
        result = m.audit(manifest, [], 'pool')
        self.assertFalse(result['evidence_complete'])
        self.assertIsNone(result['eval_clean']['pass_at_1'])

    def test_false_usage_complete_and_bad_manifest_rejected(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        summaries[-1]['records'][0]['usage'] = {'complete': True}
        result = m.audit(manifest, summaries, 'pool')
        self.assertEqual(result['eval_clean']['usage_unknown_attempts'], 1)
        manifest['instances'][0]['family'] = 'unknown'
        with self.assertRaises(ValueError):
            m.audit(manifest, summaries, 'pool')

    def test_sampling_model_and_budget_drift_are_rejected(self):
        m = self.module()
        for field, value in (('temperature', 1.0), ('seed', 999), ('mode', 'screen')):
            manifest, summaries = self.fixtures()
            summaries[-1]['records'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'identity'):
                m.audit(manifest, summaries, 'pool')

    def test_conflicting_controls_are_order_independent(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        wrong = dict(summaries[0]['records'][0], rewards={'reward': 1})
        summaries[0]['records'].append(wrong)
        first = m.audit(manifest, summaries, 'pool')['overfit_16']
        summaries[0]['records'].reverse()
        second = m.audit(manifest, summaries, 'pool')['overfit_16']
        self.assertEqual(first, second)
        self.assertEqual(first, [])
        for field, value in (('model_revision', 'wrong'), ('serving_lock_sha256', 'wrong'), ('runtime_limits', {})):
            manifest, summaries = self.fixtures()
            summaries[-1][field] = value
            with self.assertRaisesRegex(ValueError, 'identity'):
                m.audit(manifest, summaries, 'pool')
