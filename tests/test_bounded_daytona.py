import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from daytona import CreateSandboxFromImageParams, Resources
from lab_runtime.daytona import BoundedDaytonaEnvironment


class BoundedDaytonaTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_create_is_not_resubmitted(self):
        env = object.__new__(BoundedDaytonaEnvironment)
        env._auto_labels = False
        env._user_labels = {}
        env.task_env_config = SimpleNamespace(build_timeout_sec=60)
        client = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("failed")))
        with self.assertRaises(RuntimeError):
            await env._create_sandbox(CreateSandboxFromImageParams(image="ubuntu:24.04"), client)
        self.assertEqual(client.create.await_count, 1)

    def test_registered_config_supports_provider_policies(self):
        from harbor.environments.factory import EnvironmentFactory
        from harbor.models.trial.config import EnvironmentConfig
        config = json.loads(Path("configs/m0-harbor-oracle.json").read_text())
        EnvironmentFactory.validate_resource_policies(
            EnvironmentConfig.model_validate(config["environment"])
        )

    async def test_ttl_reaches_actual_client_create(self):
        env = object.__new__(BoundedDaytonaEnvironment)
        env._auto_labels = False
        env._user_labels = {"run": "unit-test"}
        env.task_env_config = SimpleNamespace(build_timeout_sec=60)
        client = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id="test")))
        params = CreateSandboxFromImageParams(
            image="python:3.12-slim", resources=Resources(cpu=1, memory=1, disk=3)
        )
        await env._create_sandbox(params, client)
        wire = client.create.call_args.kwargs["params"]
        self.assertEqual(wire.ttl_minutes, 5)
        self.assertFalse(wire.public)
        self.assertEqual(wire.labels, {"run": "unit-test"})
        self.assertEqual(wire.resources.memory, 1)
        self.assertEqual(env._sandbox.id, "test")

    async def test_m1_policies_and_create_evidence_reach_client(self):
        env = object.__new__(BoundedDaytonaEnvironment)
        env._auto_labels = False
        env._user_labels = {'lab': 'sandbox-rl-mopd', 'm1_run': 'unit'}
        env.task_env_config = SimpleNamespace(build_timeout_sec=60)
        client = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id='m1-unit')))
        params = CreateSandboxFromImageParams(image='python:3.12-slim', resources=Resources(cpu=1, memory=1, disk=3))
        await env._create_sandbox(params, client)
        actual = client.create.call_args.kwargs['params']
        self.assertTrue(actual.network_block_all)
        self.assertEqual(actual.auto_stop_interval, 1)
        self.assertEqual(actual.auto_delete_interval, 0)
        self.assertEqual(env.m1_create_events[0]['sandbox_id'], 'm1-unit')


if __name__ == "__main__":
    unittest.main()
