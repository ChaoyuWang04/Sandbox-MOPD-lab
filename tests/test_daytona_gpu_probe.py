import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from daytona import GpuType

from lab_runtime import daytona_gpu_probe as probe


class DaytonaGpuProbeTests(unittest.TestCase):
    def test_params_are_one_on_demand_5090_with_ttl_and_volume(self):
        params = probe.build_params("volume-id", "run-123")
        self.assertEqual(params.image, "pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime")
        self.assertEqual(params.resources.gpu, 1)
        self.assertEqual(params.resources.gpu_type, [GpuType.RTX_5090, GpuType.RTX_4090])
        self.assertEqual(params.ttl_minutes, 10)
        self.assertFalse(params.spot)
        self.assertTrue(params.ephemeral)
        self.assertEqual(params.auto_delete_interval, 0)
        self.assertEqual(params.volumes[0].volume_id, "volume-id")
        self.assertEqual(params.volumes[0].mount_path, "/home/daytona/lab")
        self.assertEqual(params.labels, {"project": "sandbox-rl-mopd", "campaign": "m2-gpu-probe-v1",
                                         "run_id": "run-123"})

    def test_probe_creates_once_validates_cuda_and_confirms_exact_cleanup(self):
        output = {"torch": "2.11.0", "cuda_runtime": "12.8", "cuda_available": True,
                  "device_count": 1, "device_name": "NVIDIA GeForce RTX 5090",
                  "volume_writable": True, "volume_bytes_free": 100_000_000_000,
                  "memory_bytes": 64_000_000_000}
        sandbox = SimpleNamespace(id="sandbox-1", process=SimpleNamespace(
            exec=Mock(return_value=SimpleNamespace(exit_code=0, result=json.dumps(output)))))
        volume = SimpleNamespace(id="volume-1", name=probe.VOLUME_NAME, state="ready")
        client = SimpleNamespace(
            volume=SimpleNamespace(get=Mock(return_value=volume)),
            create=Mock(return_value=sandbox), delete=Mock(),
            get=Mock(side_effect=probe.DaytonaNotFoundError("gone")),
            list=Mock(return_value=iter(())))
        receipt = probe.execute_probe(client, "run-123")
        self.assertEqual(client.create.call_count, 1)
        client.delete.assert_called_once_with(sandbox, timeout=60, wait=True)
        self.assertEqual(receipt["phase"], "complete")
        self.assertTrue(receipt["cleanup_confirmed"])
        self.assertNotIn("command", receipt)

    def test_create_rejection_is_typed_and_never_retried(self):
        volume = SimpleNamespace(id="volume-1", name=probe.VOLUME_NAME, state="ready")
        client = SimpleNamespace(volume=SimpleNamespace(get=Mock(return_value=volume)),
                                 create=Mock(side_effect=RuntimeError("may contain secrets")))
        receipt = probe.execute_probe(client, "run-123")
        self.assertEqual(client.create.call_count, 1)
        self.assertEqual(receipt["phase"], "provider_rejected")
        self.assertEqual(receipt["error_type"], "RuntimeError")
        self.assertNotIn("may contain secrets", json.dumps(receipt))

    def test_runtime_accepts_registered_4090_fallback(self):
        output = {"torch": "2.11.0", "cuda_runtime": "12.8", "cuda_available": True,
                  "device_count": 1, "device_name": "NVIDIA GeForce RTX 4090",
                  "volume_writable": True, "volume_bytes_free": 1, "memory_bytes": 1}
        self.assertEqual(probe.validate_output(json.dumps(output))["device_name"],
                         "NVIDIA GeForce RTX 4090")

    def test_ledger_refuses_a_second_provider_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = probe.authorize_once(root, "run-123")
            self.assertTrue(first.is_file())
            with self.assertRaisesRegex(FileExistsError, "already authorized"):
                probe.authorize_once(root, "run-456")


if __name__ == "__main__":
    unittest.main()
