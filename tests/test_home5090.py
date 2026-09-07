import unittest

from lab_runtime.home5090 import SETTINGS, validate_completion, validate_tool_call, isolated_env, check_host, server_argv


class Home5090ContractTest(unittest.TestCase):
    def test_download_policy_is_explicit_and_bounded(self):
        env = isolated_env({"UV_HTTP_TIMEOUT": "1", "HF_ENDPOINT": "https://wrong.invalid"})
        self.assertEqual(env.get("UV_HTTP_TIMEOUT"), "120")
        self.assertEqual(env.get("UV_CONCURRENT_DOWNLOADS"), "4")
        self.assertEqual(env.get("HF_HUB_DOWNLOAD_TIMEOUT"), "120")
        self.assertEqual(env.get("HF_HUB_ETAG_TIMEOUT"), "30")
        self.assertEqual(env.get("HF_HUB_DISABLE_XET"), "1")
        self.assertEqual(env.get("HF_ENDPOINT"), "https://huggingface.co")

    def test_install_budget_does_not_relax_cold_acceptance(self):
        from lab_runtime import home5090 as contract
        self.assertEqual(getattr(contract, "PREPARE_SECONDS", None), 7200)
        self.assertTrue(contract.qualifies_cold_prepare(True, 1199))
        self.assertFalse(contract.qualifies_cold_prepare(True, 1201))
        self.assertFalse(contract.qualifies_cold_prepare(False, 10))
        self.assertFalse(contract.qualifies_cold_prepare(True, -1))

    def test_hf_download_uses_fixed_revision_and_two_workers(self):
        from lab_runtime import home5090 as contract
        self.assertTrue(hasattr(contract, "model_download_argv"))
        argv = contract.model_download_argv()
        self.assertEqual(argv[argv.index("--max-workers")+1], "2")
        self.assertEqual(argv[argv.index("--revision")+1], SETTINGS["revision"])
        self.assertNotIn("--force-download", argv)

    def test_mac_and_free_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            check_host(["entry.py"], "Darwin", "arm64")
        with self.assertRaises(ValueError):
            check_host(["entry.py", "--model=other"], "Linux", "x86_64")
        check_host(["entry.py"], "Linux", "x86_64")

    def test_server_is_local_and_fixed(self):
        argv = server_argv()
        self.assertIn("127.0.0.1", argv)
        self.assertIn("bfloat16", argv)
        self.assertIn("16384", argv)
        self.assertNotIn("--trust-remote-code", argv)

    def test_fixed_identity_and_resource_bounds(self):
        self.assertEqual(SETTINGS["revision"], "1cfa9a7208912126459214e8b04321603b3df60c")
        self.assertEqual(SETTINGS["max_model_len"], 16384)
        self.assertEqual(SETTINGS["output_tokens"], 4096)
        self.assertLessEqual(SETTINGS["gpu_memory_utilization"], 0.70)

    def test_short_output_does_not_pass(self):
        with self.assertRaises(ValueError):
            validate_completion({"usage": {"completion_tokens": 30}}, 1)

    def test_full_output_and_strict_time_boundary(self):
        result = {"usage": {"prompt_tokens": 12288, "completion_tokens": 4096},
                  "choices": [{"finish_reason": "length", "token_ids": [1] * 4096}]}
        validate_completion(result, 89.99)
        with self.assertRaises(ValueError):
            validate_completion(result, 90)

    def test_usage_without_real_output_ids_is_not_enough(self):
        result = {"usage": {"prompt_tokens": 12288, "completion_tokens": 4096},
                  "choices": [{"finish_reason": "length", "token_ids": [1] * 20}]}
        with self.assertRaises(ValueError):
            validate_completion(result, 30)

    def test_short_context_does_not_pass_full_context_gate(self):
        result = {"usage": {"prompt_tokens": 100, "completion_tokens": 4096},
                  "choices": [{"finish_reason": "length", "token_ids": [1] * 4096}]}
        with self.assertRaises(ValueError):
            validate_completion(result, 30)

    def test_actual_tool_name_and_arguments(self):
        reply = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"type": "function", "function": {
            "name": "add_numbers", "arguments": '{"a":17,"b":25}'}}]}}]}
        validate_tool_call(reply)
        reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"a":17,"b":26}'
        with self.assertRaises(ValueError):
            validate_tool_call(reply)

    def test_tool_arguments_must_be_integers_not_floats(self):
        reply = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"type": "function", "function": {
            "name": "add_numbers", "arguments": '{"a":17.0,"b":25}'}}]}}]}
        with self.assertRaises(ValueError):
            validate_tool_call(reply)

    def test_implicit_credentials_are_not_forwarded(self):
        env = isolated_env({"PATH": "/usr/bin", "HF_TOKEN": "secret", "DAYTONA_API_KEY": "secret"})
        self.assertNotIn("HF_TOKEN", env)
        self.assertNotIn("DAYTONA_API_KEY", env)
        self.assertEqual(env["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")

    def test_library_caches_are_explicitly_scoped(self):
        env = isolated_env({})
        for name in ("VLLM_CONFIG_ROOT", "XDG_CONFIG_HOME", "FLASHINFER_WORKSPACE_BASE"):
            self.assertTrue(env.get(name, "").startswith("/home/samwang/data/sandbox-rl-MOPD-lab/cache/"))
        self.assertEqual(env.get("CUDA_VISIBLE_DEVICES"), "0")


if __name__ == "__main__":
    unittest.main()
