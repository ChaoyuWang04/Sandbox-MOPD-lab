"""Bounded model-pilot tests; all provider and model behavior is fake."""
import copy
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]


class PilotConfigTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('lab_runtime.m1_pilot_v2'),
                             'v2 model pilot runner missing')
        return importlib.import_module('lab_runtime.m1_pilot_v2')

    def config(self):
        return json.loads((SOURCE/'configs/m1-pilot-v2.json').read_text())

    def manifest(self):
        return json.loads((SOURCE/'data/m1/v2/self48/manifest.json').read_text())

    def test_registered_config_and_exact_four_trials(self):
        m = self.module()
        cfg = self.config()
        m.validate_config(cfg)
        trials = m.select_trials(cfg, self.manifest())
        self.assertEqual([row['instance'] for row in trials], [
            'self-v2-config_precedence-00',
            'self-v2-resource_lifetime-00',
            'self-v2-async_dependencies-00',
            'self-v2-atomic_replace-00'])
        self.assertEqual(len({row['trial_id'] for row in trials}), 4)
        self.assertEqual(len({row['seed'] for row in trials}), 4)
        self.assertTrue(all(row['agent'] == 'm1' and row['temperature'] == .6 for row in trials))

    def test_any_config_drift_is_rejected(self):
        m = self.module()
        cfg = self.config()
        mutations = []
        changed = copy.deepcopy(cfg)
        changed['trial']['tool_timeout_seconds'] = 21
        mutations.append(changed)
        changed = copy.deepcopy(cfg)
        changed['trials'][1]['seed'] = changed['trials'][0]['seed']
        mutations.append(changed)
        changed = copy.deepcopy(cfg)
        changed['trials'][1]['trial_id'] = changed['trials'][0]['trial_id']
        mutations.append(changed)
        changed = copy.deepcopy(cfg)
        changed['trials'].append(copy.deepcopy(changed['trials'][0]))
        mutations.append(changed)
        changed = copy.deepcopy(cfg)
        changed['extra'] = True
        mutations.append(changed)
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    m.validate_config(candidate)

    def test_wrong_or_duplicate_manifest_task_is_rejected(self):
        m = self.module()
        cfg, manifest = self.config(), self.manifest()
        missing = copy.deepcopy(manifest)
        missing['instances'] = [r for r in missing['instances'] if r['instance'] != cfg['trials'][0]['task_id']]
        with self.assertRaises(ValueError):
            m.select_trials(cfg, missing)
        duplicate = copy.deepcopy(manifest)
        duplicate['instances'].append(copy.deepcopy(next(
            r for r in duplicate['instances'] if r['instance'] == cfg['trials'][0]['task_id'])))
        with self.assertRaises(ValueError):
            m.select_trials(cfg, duplicate)

    def test_agent_constants_match_registered_limits(self):
        m = self.module()
        m.validate_agent_contract(self.config())

    def test_current_sdk_auth_rejections_are_definite(self):
        from lab_runtime import daytona_pilot
        self.assertIn('DaytonaAuthenticationError', daytona_pilot.DEFINITE_CREATE_REJECTIONS)
        self.assertIn('DaytonaAuthorizationError', daytona_pilot.DEFINITE_CREATE_REJECTIONS)
        self.assertNotIn('DaytonaUnauthorizedError', daytona_pilot.DEFINITE_CREATE_REJECTIONS)


class PilotLedgerTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('lab_runtime.m1_pilot_v2')

    def plans(self):
        cfg = json.loads((SOURCE/'configs/m1-pilot-v2.json').read_text())
        manifest = json.loads((SOURCE/'data/m1/v2/self48/manifest.json').read_text())
        return self.module().select_trials(cfg, manifest)

    def test_new_ledger_never_changes_old_campaign(self):
        m = self.module()
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old = root/'artifacts/m1/campaign.json'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'old-campaign-evidence\n')
            before = old.read_bytes()
            ledger = root/'artifacts/m1/v2/model-pilot/campaign.json'
            identity = {'source_git_sha': 'a'*40, 'config_sha256': 'b'*64}
            m.reserve_campaign(ledger, old, identity, 'run-1', self.plans())
            self.assertEqual(old.read_bytes(), before)
            self.assertTrue(ledger.is_file())
            with self.assertRaises(ValueError):
                m.reserve_campaign(ledger, old, identity, 'run-2', self.plans())
            m.finish_campaign(ledger, identity, 'run-1', '/evidence/summary.json', 'c'*64)
            next_identity = {'source_git_sha': 'd'*40, 'config_sha256': 'b'*64}
            saved = m.reserve_campaign(ledger, old, next_identity, 'run-2', self.plans())
            self.assertEqual(len(saved['prior_runs']), 1)
            self.assertEqual(saved['prior_runs'][0]['run_id'], 'run-1')
            self.assertEqual(old.read_bytes(), before)

    def test_attempt_is_durable_before_provider_and_fifth_is_rejected(self):
        m = self.module()
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            ledger = root/'campaign.json'
            old = root/'old.json'
            old.write_text('{}')
            identity = {'source_git_sha': 'a'*40, 'config_sha256': 'b'*64}
            plans = self.plans()
            m.reserve_campaign(ledger, old, identity, 'run-1', plans)
            for index, plan in enumerate(plans):
                m.mark_attempt(ledger, identity, 'run-1', plan, index)
                saved = json.loads(ledger.read_text())
                self.assertEqual(saved['attempts'][index]['state'], 'admitted')
                self.assertEqual(saved['provider_create_authorizations'], index + 1)
            with self.assertRaises(ValueError):
                m.mark_attempt(ledger, identity, 'run-1', plans[0], 4)

    def test_every_ledger_write_fsyncs_parent_directory(self):
        m = self.module()
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old, ledger = root/'old.json', root/'campaign.json'
            old.write_text('{}')
            identity = {'source_git_sha': 'a'*40, 'config_sha256': 'b'*64}
            with patch.object(m.os, 'fsync') as sync:
                m.reserve_campaign(ledger, old, identity, 'run-1', self.plans())
                self.assertGreaterEqual(sync.call_count, 2)

    def test_cleanup_is_the_known_id_checked_implementation(self):
        m = self.module()
        from lab_runtime.controls_v2 import cleanup
        self.assertIs(m.cleanup_sandboxes, cleanup)


class PilotSummaryTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('lab_runtime.m1_pilot_v2')

    def record(self, reward=0, *, tools=1, responses=1, usage=True, cleanup=True,
               exception=None, create_uncertain=False):
        return {'state': 'finished', 'reward': reward, 'tool_calls': tools,
                'tool_observations': tools, 'shell_executions': tools,
                'model_responses': responses,
                'usage_complete': usage, 'cleanup_empty': cleanup,
                'exception_type': exception, 'create_uncertain': create_uncertain,
                'classification': None}

    def test_invalid_never_enters_reward_denominator(self):
        m = self.module()
        records = [self.record(0), self.record(1),
                   self.record(None, exception='ProviderError'),
                   self.record(0, cleanup=False)]
        result = m.summarize_records(records)
        self.assertEqual(result['valid_reward_count'], 2)
        self.assertEqual(result['reward_counts'], {'0': 1, '1': 1})
        self.assertEqual(result['invalid_count'], 2)
        self.assertEqual(result['success_count_observation'], 1)

    def test_tool_chain_requires_response_observation_and_valid_reward(self):
        m = self.module()
        for broken in (self.record(1, tools=0), self.record(1, responses=0),
                       self.record(None), self.record(1, usage=False),
                       self.record(1, cleanup=False)):
            self.assertFalse(m.summarize_records([broken])['real_agent_chain_passed'])
        parse_error = self.record(0)
        parse_error['shell_executions'] = 0
        self.assertFalse(m.summarize_records([parse_error])['real_agent_chain_passed'])
        self.assertTrue(m.summarize_records([self.record(0)])['real_agent_chain_passed'])

    def test_four_terminal_classified_records_complete_execution(self):
        m = self.module()
        result = m.summarize_records([self.record(i % 2) for i in range(4)])
        self.assertTrue(result['attempts_terminal_and_classified'])
        result = m.summarize_records([self.record(0) for _ in range(3)])
        self.assertFalse(result['attempts_terminal_and_classified'])
        uncertain = [self.record(0) for _ in range(4)]
        uncertain[-1]['cleanup_empty'] = False
        self.assertFalse(m.summarize_records(uncertain)['attempts_terminal_and_classified'])
        uncertain = [self.record(0) for _ in range(4)]
        uncertain[-1]['create_uncertain'] = True
        self.assertFalse(m.summarize_records(uncertain)['attempts_terminal_and_classified'])

    def test_cost_is_timed_only_for_known_created_and_cleaned_sandbox(self):
        m = self.module()
        created = self.record(0)
        created.update(events=[{'status': 'creating', 'time': 100.0},
                               {'status': 'created', 'time': 100.0, 'sandbox_id': 'owned'}],
                       finished=160.0)
        cost = m.estimate_cost(created)
        rate = (.0504 + .0162 + 3*.000108)/3600
        self.assertAlmostEqual(cost['estimated_usd'], 60*rate)
        self.assertFalse(cost['billing_verified'])
        uncertain = dict(created, create_uncertain=True)
        self.assertIsNone(m.estimate_cost(uncertain)['estimated_usd'])
        missing_id = self.record(None, exception='ProviderProtocolError')
        missing_id.update(events=[{'status': 'creating', 'time': 100.0},
                                  {'status': 'created', 'time': 100.5,
                                   'sandbox_id': None}], finished=101.0)
        self.assertTrue(m.creation_uncertain(missing_id['events']))
        self.assertIsNone(m.estimate_cost(missing_id)['estimated_usd'])
        rejected = self.record(None, exception='DaytonaBadRequestError')
        rejected.update(events=[{'status': 'rejected', 'time': 100.0,
                                 'sandbox_id': None}], finished=101.0)
        self.assertEqual(m.estimate_cost(rejected)['estimated_usd'], 0)


if __name__ == '__main__':
    unittest.main()
