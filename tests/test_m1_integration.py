"""Construct real Harbor Trial objects without starting environments."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harbor.trial.trial import Trial
from harbor.environments.daytona.environment import DaytonaClientManager
from lab_runtime.m1_run import SOURCE, make_trial_config, select_trials
from lab_runtime.task_pool import build_pool
from lab_runtime.m1_agent import M1Agent
from lab_runtime.daytona import BoundedDaytonaEnvironment


class HarborConstructionTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_trial_factory_model_nop_oracle_without_provider_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve()
            manifest = build_pool(run/'pool')
            self.assertEqual(manifest, json.loads((SOURCE/'configs/m1-pool-manifest.json').read_text()))
            configs = [('pilot', 1), ('controls', 2)]
            with patch.object(DaytonaClientManager, 'get_client', side_effect=AssertionError('no provider access permitted')):
                for mode, count in configs:
                    items = select_trials(dict(mode=mode, start=0, count=count, max_trials=count, concurrency=1), manifest)
                    for item in items:
                        trial = await Trial.create(make_trial_config(run, item, {'lab': 'sandbox-rl-mopd', 'm1_run': 'offline-factory'}))
                        try:
                            self.assertIsInstance(trial.agent_environment, BoundedDaytonaEnvironment)
                            self.assertEqual(trial._agent_timeout_sec, 180)
                            self.assertEqual(trial._verifier_timeout_sec, 30)
                            if mode == 'pilot':
                                self.assertIsInstance(trial.agent, M1Agent)
                        finally:
                            trial._close_logger_handler()
