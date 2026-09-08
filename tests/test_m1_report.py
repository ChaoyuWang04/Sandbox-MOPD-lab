import importlib.util
import unittest
import hashlib
import copy
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
        self.assertEqual(result['eval_clean']['invalid_attempts'], 1)

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

    def test_second_pilot_seed_is_explicit_diagnostic_not_formal_duplicate(self):
        m = self.module()
        manifest, summaries = self.fixtures()
        row = summaries[1]['records'][0]
        pilots = [dict(row, mode='pilot', diagnostic_index=i, seed=row['seed']+i*100000000) for i in (0,1)]
        summaries.append(dict(summaries[1], mode='pilot', records=pilots))
        result = m.audit(manifest, summaries, 'pool')
        self.assertEqual(result['duplicate_attempts'], [])
        for field, value in (('model_revision', 'wrong'), ('serving_lock_sha256', 'wrong'), ('runtime_limits', {})):
            manifest, summaries = self.fixtures()
            summaries[-1][field] = value
            with self.assertRaisesRegex(ValueError, 'identity'):
                m.audit(manifest, summaries, 'pool')


class M1CloseV2Test(unittest.TestCase):
    def manifest(self):
        records = []
        for skill in ('A', 'B'):
            for index in range(10):
                task_id = f'{skill.lower()}-{index:02d}'
                records.append({'id': task_id, 'split': 'train', 'source': 'self',
                    'primary_skill': skill, 'task_files_sha256': {'instruction.md': f'{index:064x}'}})
        records.extend([
            {'id': 'dev-a', 'split': 'dev', 'source': 'self', 'primary_skill': 'A',
             'task_files_sha256': {'instruction.md': 'a' * 64}},
            {'id': 'final-a', 'split': 'final', 'source': 'terminal-bench', 'primary_skill': 'A',
             'task_files_sha256': {'instruction.md': 'b' * 64}},
        ])
        return {'records': records}

    def config(self):
        return {'schema_version': 1, 'candidate_task_ids':
            [f'{skill}-{index:02d}' for skill in ('a', 'b') for index in range(10)],
            'group_size': 4, 'temperature': 1.0, 'top_p': 1.0, 'seed': 930000,
            'select_per_skill': {'A': 8, 'B': 8}}

    def screen(self):
        manifest = {row['id']: row for row in self.manifest()['records']}
        records = []
        for candidate_index, task_id in enumerate(self.config()['candidate_task_ids']):
            for repetition_id, value in enumerate((0, 1, 0, 1)):
                records.append({'task_id': task_id, 'group_id': f'm1-close/{task_id}',
                    'repetition_id': repetition_id,
                    'seed': 930000 + candidate_index * 4 + repetition_id,
                    'temperature': 1.0, 'top_p': 1.0, 'reward': value,
                    'invalid_reason': None,
                    'task_files_sha256': manifest[task_id]['task_files_sha256']})
        return records

    def test_selects_first_eight_mixed_train_tasks_per_skill(self):
        from lab_runtime import m1_report
        result = m1_report.select_overfit16_v2(self.manifest(), self.screen(), self.config())
        self.assertEqual(result['overfit_16'],
            [f'a-{i:02d}' for i in range(8)] + [f'b-{i:02d}' for i in range(8)])
        self.assertEqual(result['mixed_group_count'], 20)
        self.assertEqual(result['invalid_attempts'], 0)

    def test_zero_variance_and_invalid_groups_do_not_fake_eligibility(self):
        from lab_runtime import m1_report
        records = self.screen()
        for record in records:
            if record['task_id'] == 'a-00':
                record['reward'] = 0
            if record['task_id'] == 'b-00' and record['repetition_id'] > 0:
                record.update(reward=None, invalid_reason='sandbox_timeout')
        result = m1_report.select_overfit16_v2(self.manifest(), records, self.config())
        self.assertNotIn('a-00', result['overfit_16'])
        self.assertNotIn('b-00', result['overfit_16'])
        self.assertEqual(result['invalid_attempts'], 3)

    def test_split_hash_sampling_and_attempt_set_fail_closed(self):
        from lab_runtime import m1_report
        mutations = []
        cfg = self.config()
        bad_split = copy.deepcopy(cfg)
        bad_split['candidate_task_ids'][0] = 'dev-a'
        mutations.append((self.screen(), bad_split))
        bad_final = copy.deepcopy(cfg)
        bad_final['candidate_task_ids'][0] = 'final-a'
        mutations.append((self.screen(), bad_final))
        for key, value in (('task_files_sha256', {}), ('temperature', 0.6), ('seed', 1)):
            records = self.screen()
            records[0][key] = value
            mutations.append((records, cfg))
        records = self.screen()
        records.append(copy.deepcopy(records[0]))
        mutations.append((records, cfg))
        records = self.screen()[1:]
        mutations.append((records, cfg))
        for records, config in mutations:
            with self.subTest(config=config), self.assertRaises(ValueError):
                m1_report.select_overfit16_v2(self.manifest(), records, config)

    def test_terminal_bench_control_requires_both_poles_and_lifecycle(self):
        from lab_runtime import m1_report
        manifest = self.manifest()
        task = next(row for row in manifest['records'] if row['id'] == 'final-a')
        controls = [{'task_id': 'final-a', 'agent': agent, 'reward': reward,
            'invalid_reason': None, 'task_files_sha256': task['task_files_sha256'],
            'verifier_complete': True, 'private_files_absent_during_agent': True,
            'cleanup_confirmed_empty': True} for agent, reward in (('nop', 0), ('oracle', 1))]
        self.assertEqual(m1_report.validate_tb_representative(manifest, 'final-a', controls),
                         {'task_id': 'final-a', 'status': 'representative_control_passed'})
        for field in ('verifier_complete', 'private_files_absent_during_agent',
                      'cleanup_confirmed_empty'):
            broken = copy.deepcopy(controls)
            broken[0][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                m1_report.validate_tb_representative(manifest, 'final-a', broken)
