import asyncio
import copy
import unittest


TEMPLATE = "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101"


class FakeInference:
    proxy_url = "http://127.0.0.1:8000"
    model_name = "Qwen3-4B"

    def __init__(self):
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(copy.deepcopy(request))
        turn = len(self.requests)
        prompt = [10, turn]
        response = [20 + turn, 30 + turn]
        return {
            "model": self.model_name,
            "weight_version": "policy-v0",
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(response)},
            "choices": [{
                "prompt_token_ids": prompt,
                "token_ids": response,
                "finish_reason": "tool_calls" if turn % 2 else "stop",
                "message": {"role": "assistant", "content": f"turn-{turn}"},
                "logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]},
            }],
        }


class FakeTrials:
    def __init__(self, rewards=None, invalid=None, cleanup=True):
        self.rewards = rewards or {}
        self.invalid = invalid or {}
        self.cleanup = cleanup
        self.reconciled = []

    async def run(self, *, prompt, trajectory_id, generate):
        key = (trajectory_id["instance_id"], trajectory_id["repetition_id"])
        await generate([{"role": "user", "content": prompt}], observation_token_count=0)
        await generate([{"role": "user", "content": prompt},
                        {"role": "tool", "content": "bounded"}],
                       observation_token_count=1)
        return {"reward": self.rewards.get(key, key[1] % 2),
                "invalid_reason": self.invalid.get(key),
                "terminal_reason": "stop"}

    async def reconcile(self, trajectory_id):
        self.reconciled.append(dict(trajectory_id))
        return self.cleanup


class GeneratorBridgeTests(unittest.TestCase):
    def input_batch(self):
        return {
            "prompts": ["task-a", "task-a", "task-b", "task-b"],
            "trajectory_ids": [
                {"instance_id": "task-a", "repetition_id": 0},
                {"instance_id": "task-a", "repetition_id": 1},
                {"instance_id": "task-b", "repetition_id": 0},
                {"instance_id": "task-b", "repetition_id": 1},
            ],
            "sampling_params": {"temperature": 1.0, "top_p": 1.0,
                                "max_tokens": 4096, "seed": 920001},
            "env_classes": ["harbor"] * 4,
            "env_extras": [{"task_id": task} for task in
                           ("task-a", "task-a", "task-b", "task-b")],
            "batch_metadata": {"global_step": 0, "training_phase": "train"},
        }

    def bridge(self, inference=None, trials=None):
        from recipe.generator import HarborSkyRLBridge
        return HarborSkyRLBridge(
            inference=inference or FakeInference(), trials=trials or FakeTrials(),
            expected_proxy_url="http://127.0.0.1:8000",
            expected_model_name="Qwen3-4B", template_sha256=TEMPLATE,
            expected_weight_version="policy-v0", group_size=2,
            max_model_len=16384, max_observation_tokens=128)

    def test_exact_chat_payload_order_and_stepwise_generator_output(self):
        inference = FakeInference()
        bridge = self.bridge(inference=inference)
        output = asyncio.run(bridge.generate(self.input_batch()))
        self.assertEqual(len(inference.requests), 8)
        for request in inference.requests:
            body = request["json"]
            self.assertEqual(body["model"], "Qwen3-4B")
            self.assertIs(body["return_token_ids"], True)
            self.assertIs(body["logprobs"], True)
            self.assertEqual(body["top_logprobs"], 0)
            self.assertEqual(body["temperature"], 1.0)
            self.assertEqual(body["top_p"], 1.0)
        self.assertEqual(len(output["response_ids"]), 8)
        self.assertEqual(output["is_last_step"], [False, True] * 4)
        self.assertEqual(output["loss_masks"], [[1, 1]] * 8)
        self.assertEqual(output["rollout_metrics"]["m2/valid_trajectories"], 4)
        self.assertEqual(output["rollout_metrics"]["m2/mixed_valid_groups"], 2)
        self.assertEqual([row["instance_id"] for row in output["trajectory_ids"]],
                         ["task-a", "task-a", "task-a", "task-a",
                          "task-b", "task-b", "task-b", "task-b"])
        self.assertEqual(len(bridge.last_audit["step_receipt_sha256"]), 8)

    def test_invalid_member_or_uncertain_cleanup_excludes_whole_group(self):
        invalid = {("task-a", 0): "verifier_incomplete"}
        trials = FakeTrials(invalid=invalid)
        bridge = self.bridge(trials=trials)
        output = asyncio.run(bridge.generate(self.input_batch()))
        self.assertEqual({row["instance_id"] for row in output["trajectory_ids"]}, {"task-b"})
        self.assertEqual(output["rollout_metrics"]["m2/invalid_trajectories"], 2)
        self.assertEqual(len(trials.reconciled), 4)

        bridge = self.bridge(trials=FakeTrials(cleanup=False))
        with self.assertRaisesRegex(ValueError, "no valid trajectory groups"):
            asyncio.run(bridge.generate(self.input_batch()))
        self.assertEqual(bridge.last_audit["phase"], "invalid_exclusion")
        self.assertEqual(bridge.last_audit["valid_groups"], 0)
        self.assertEqual(len(bridge.last_audit["invalid_records"]), 4)
        self.assertTrue(all(row["invalid_reason"] == "cleanup_unconfirmed"
                            for row in bridge.last_audit["invalid_records"]))

    def test_accepts_nonnegative_training_step_and_audits_it(self):
        batch = self.input_batch()
        batch["batch_metadata"]["global_step"] = 3
        bridge = self.bridge()
        asyncio.run(bridge.generate(batch))
        self.assertEqual(bridge.last_audit["batch_metadata"],
                         {"global_step": 3, "training_phase": "train"})

        batch["batch_metadata"]["global_step"] = -1
        with self.assertRaisesRegex(ValueError, "batch metadata"):
            asyncio.run(self.bridge().generate(batch))

    def test_rejects_input_sampling_identity_and_response_drift(self):
        cases = []
        batch = self.input_batch()
        batch["prompts"].pop()
        cases.append(batch)
        batch = self.input_batch()
        batch["sampling_params"]["n"] = 2
        cases.append(batch)
        batch = self.input_batch()
        batch["trajectory_ids"][1]["repetition_id"] = 0
        cases.append(batch)
        for index, batch in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                asyncio.run(self.bridge().generate(batch))

        class BadInference(FakeInference):
            async def chat_completion(self, request):
                response = await super().chat_completion(request)
                response["choices"][0]["logprobs"]["content"].pop()
                return response

        with self.assertRaises(ValueError):
            asyncio.run(self.bridge(inference=BadInference()).generate(self.input_batch()))

    def test_cancellation_still_reconciles_owned_trial(self):
        class SlowInference(FakeInference):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.completed = False

            async def chat_completion(self, request):
                self.started.set()
                await self.release.wait()
                response = await super().chat_completion(request)
                self.completed = True
                return response

        class OneCallTrials(FakeTrials):
            async def run(self, *, prompt, trajectory_id, generate):
                await generate([{"role": "user", "content": prompt}],
                               observation_token_count=0)
                return {"reward": 0, "invalid_reason": None,
                        "terminal_reason": "stop"}

        async def scenario():
            inference = SlowInference()
            trials = OneCallTrials()
            bridge = self.bridge(inference=inference, trials=trials)
            task = asyncio.create_task(bridge.generate(self.input_batch()))
            await inference.started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            inference.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return inference, trials, bridge

        inference, trials, bridge = asyncio.run(scenario())
        self.assertTrue(inference.completed)
        self.assertEqual(len(trials.reconciled), 1)
        record = bridge.last_audit["trajectory_records"][0]
        self.assertIs(record["cleanup_confirmed"], True)
        self.assertEqual(record["inflight_status"], "finished_after_cancel")
        self.assertEqual(record["late_usage"],
                         {"prompt_tokens": 2, "completion_tokens": 2})


if __name__ == "__main__":
    unittest.main()
