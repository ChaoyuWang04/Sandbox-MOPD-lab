import copy
import math
import unittest


TEMPLATE = "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101"


class TitoTests(unittest.TestCase):
    def step(self, prompt, response, *, stop_reason, observations=0,
             truncated=False, dropped=0):
        return {
            "prompt_token_ids": prompt,
            "response_token_ids": response,
            "rollout_logprobs": [-0.1 * (index + 1) for index in range(len(response))],
            "response_position_ids": list(range(len(prompt), len(prompt) + len(response))),
            "stop_reason": stop_reason,
            "observation_token_count": observations,
            "context_truncated": truncated,
            "dropped_prompt_tokens": dropped,
            "weight_version": "policy-v0",
        }

    def trajectories(self):
        return [
            {
                "trajectory_id": {"instance_id": "task-a", "repetition_id": 0},
                "reward": 1,
                "steps": [
                    self.step([1, 2], [3, 4], stop_reason="tool_call"),
                    self.step([1, 2, 3, 4, 5], [6], stop_reason="stop", observations=1),
                ],
            },
            {
                "trajectory_id": {"instance_id": "task-b", "repetition_id": 1},
                "reward": 0,
                "steps": [self.step([7], [8, 9], stop_reason="eos")],
            },
        ]

    def build(self, trajectories=None):
        from recipe.tito import build_stepwise_batch
        return build_stepwise_batch(
            trajectories or self.trajectories(), template_sha256=TEMPLATE,
            expected_weight_version="policy-v0", max_model_len=16,
            max_observation_tokens=4)

    def validate(self, batch):
        from recipe.tito import validate_stepwise_batch
        return validate_stepwise_batch(
            batch, expected_template_sha256=TEMPLATE,
            expected_weight_version="policy-v0", max_model_len=16,
            max_observation_tokens=4)

    def test_builds_exact_contiguous_stepwise_output(self):
        batch = self.build()
        self.assertEqual(batch["prompt_token_ids"], [[1, 2], [1, 2, 3, 4, 5], [7]])
        self.assertEqual(batch["response_ids"], [[3, 4], [6], [8, 9]])
        self.assertEqual(batch["loss_masks"], [[1, 1], [1], [1, 1]])
        self.assertEqual(batch["rewards"], [[0.0, 0.0], [1.0], [0.0, 0.0]])
        self.assertEqual(batch["is_last_step"], [False, True, True])
        self.assertEqual([row["instance_id"] for row in batch["trajectory_ids"]],
                         ["task-a", "task-a", "task-b"])
        self.assertEqual(batch["weight_versions"], ["policy-v0"] * 3)
        self.assertTrue(self.validate(batch))

    def test_one_token_logprob_position_mask_weight_or_template_drift_fails(self):
        mutations = []
        for field, value in (
            ("response_ids", [[30, 4], [6], [8, 9]]),
            ("rollout_logprobs", [[-9.0, -0.2], [-0.1], [-0.1, -0.2]]),
            ("response_position_ids", [[3, 4], [5], [1, 2]]),
            ("loss_masks", [[1, 0], [1], [1, 1]]),
            ("weight_versions", ["policy-v1", "policy-v0", "policy-v0"]),
        ):
            batch = self.build()
            batch[field] = value
            mutations.append(batch)
        batch = self.build()
        batch["template_sha256"] = "0" * 64
        mutations.append(batch)
        for index, batch in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.validate(batch)

    def test_rejects_retokenized_text_nonfinite_or_empty_generated_tokens(self):
        cases = []
        trajectories = self.trajectories()
        trajectories[0]["steps"][0]["response_text"] = "not an engine token field"
        cases.append(trajectories)
        trajectories = self.trajectories()
        trajectories[0]["steps"][0]["rollout_logprobs"][0] = math.nan
        cases.append(trajectories)
        trajectories = self.trajectories()
        trajectories[0]["steps"][0].update(
            response_token_ids=[], rollout_logprobs=[], response_position_ids=[])
        cases.append(trajectories)
        for index, trajectories in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.build(trajectories)

    def test_context_truncation_observation_and_terminal_contracts(self):
        trajectories = self.trajectories()
        trajectories[0]["steps"][1].update(
            context_truncated=True, dropped_prompt_tokens=7,
            prompt_token_ids=[2, 3, 4, 5], response_position_ids=[4])
        batch = self.build(trajectories)
        self.assertEqual(batch["context_truncation"][1],
                         {"truncated": True, "dropped_prompt_tokens": 7})
        self.assertTrue(self.validate(batch))

        bad_cases = []
        for update in (
            {"context_truncated": True, "dropped_prompt_tokens": 0},
            {"observation_token_count": 5},
            {"stop_reason": "stop"},
        ):
            candidate = self.trajectories()
            candidate[0]["steps"][0].update(update)
            bad_cases.append(candidate)
        candidate = self.trajectories()
        candidate[0]["steps"][-1]["stop_reason"] = "tool_call"
        bad_cases.append(candidate)
        for index, candidate in enumerate(bad_cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.build(candidate)

    def test_duplicate_or_noncontiguous_identity_is_rejected_after_mutation(self):
        batch = self.build()
        batch["trajectory_ids"] = [batch["trajectory_ids"][0],
                                   batch["trajectory_ids"][2],
                                   batch["trajectory_ids"][0]]
        with self.assertRaises(ValueError):
            self.validate(batch)


if __name__ == "__main__":
    unittest.main()
