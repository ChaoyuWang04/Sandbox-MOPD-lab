# M2 Binary-Reward Scale Implementation Plan

> **For agentic workers:** Follow this plan task-by-task. Use test-driven development for behavior changes and preserve the approved scope.

**Goal:** Close the runnable M1 boundary, prove a real Qwen3-4B binary-reward RL update/recovery chain, and conditionally scale the frozen training pool from 16 to 40 to 80 tasks without exposing final-test data.

**Architecture:** Mac commits immutable source and plans; M1 inference remains an allowlisted `hlab` job on shared home-5090, while M2 training runs on the selected Modal L40S fallback with a Lab Volume. SkyRL owns training and colocated vLLM; the Lab custom generator runs Harbor trials against Modal CPU sandboxes and returns exact token IDs, per-token rollout logprobs, masks, rewards, and weight identities. EvoCodeBench is a separate evaluation-only panel.

**Tech Stack:** SkyRL锁定venv的Python 3.12.12、SkyRL v0.2.0、Harbor 0.4.0、Modal SDK 1.4.1、SkyRL custom `GeneratorInterface`、PyTorch/FSDP、vLLM、Qwen3-4B LoRA、`hlab`/systemd-user、JSON audit artifacts。Mac侧Modal控制器保持Lab自身锁定的1.5.5；Daytona和Harbor 0.22只属于M1已有隔离路径，不是M2训练runtime身份。

---

## File responsibility map

- `docs/sandbox-rl-lab-plan.md`: current M1/M2 gates, 16->40->80 promotion, Evo intended-use boundary.
- `docs/plans/2026-09-08-m1-200-case-plan.md`: remaining M1 execution state only.
- `docs/EXPERIMENTS.md`, `docs/BUDGET.md`, `README.md`: observed evidence, actual budget, concise status.
- `configs/m2-stack-lock.json`: complete dependency, model, hardware, source, and entrypoint identities.
- `configs/m2-bringup.json`, `configs/m2-overfit16.json`, `configs/m2-scale40.json`, `configs/m2-scale80.json`: immutable per-stage workloads and limits.
- `lab_runtime/m2_contract.py`: strict config, trajectory, invalid/reward, identity, and promotion validation.
- `recipe/generator.py`: SkyRL custom generator and Harbor trial bridge.
- `recipe/tito.py`: exact token/span/mask construction and assertions.
- `recipe/checkpoint.py`: adapter/update/inference/checkpoint identity evidence.
- `recipe/entrypoint.py`: fixed prepare/bring-up/scale commands and durable summary writing.
- `scripts/m2_run.py`: minimal module launcher only.
- `tests/test_m2_contract.py`, `tests/test_m2_tito.py`, `tests/test_m2_generator.py`, `tests/test_m2_checkpoint.py`, `tests/test_m2_entrypoint.py`: offline behavior and fail-closed regression tests.
- External `homelab-control`: project-specific allowlisted recipes only; owned and reviewed in the `服务器改造` task.

## Task 1: Freeze the design, source facts, and initial configs

**Files:** modify the five current status documents; create `configs/m2-stack-lock.json` and `configs/m2-bringup.json`; create `tests/test_m2_contract.py` and `lab_runtime/m2_contract.py`.

1. Add failing tests that reject missing source SHAs, mutable refs, unregistered splits, final-test training IDs, non-binary numeric rewards, absent invalid reasons, unbounded run limits, a scale config that does not extend its predecessor in frozen order, missing start-checkpoint identity, non-strict TITO, home-5090 training, spot training, and a non-colocated/shared cloud-GPU topology.
2. Run `python -m unittest tests.test_m2_contract -v` and record the expected missing-module/API failure.
3. Implement the smallest pure validation and canonical digest helpers; do not import GPU or provider packages.
4. Populate the source/stack lock only from complete official files and current server facts; unknown compatible versions remain explicit and block GPU execution.
5. Re-run the focused tests, full tests, and `git diff --check`.
6. Rewrite the current docs in place and commit `docs: freeze staged M2 binary-reward design`.

## Task 2: Complete the M1 runnable boundary

**Files:** extend `lab_runtime/m1_report.py`, `scripts/m1_run.py`, focused tests, M1 config and current docs as needed.

1. Write failing tests for deterministic A8+B8 selection from train-only valid evidence, within-prompt reward variance under a frozen sample count, task-byte identity, explicit sampling contract, and refusal to consume dev/final for selection.
2. Write failing tests for a bounded Terminal-Bench representative control result that proves task-source identity, oracle=1, NOP=0, verifier execution, isolation, and cleanup without claiming all TB tasks are certified.
3. Run the focused tests and confirm feature-specific failures.
4. Implement minimal reporting/config support by reusing current lifecycle and failure-classification code.
5. Run all offline tests. Commit and sync the exact source.
6. Register a bounded M1-close plan, run only the preregistered representative controls and train/dev screen on home-5090 plus Daytona, and persist task-level evidence.
7. Select and freeze `overfit_16`; M1 must not reveal final100 scores. Update M1 G1-G5 honestly, commit, and push the verified batch.

## Task 3: Implement exact trajectory and TITO contracts

**Status 2026-09-09:** dependency-light builder/validator and five fail-closed tests are implemented; real generator consumption remains Task 4 and the stack lock stays not execution-ready.

**Files:** create `recipe/tito.py`, `tests/test_m2_tito.py`; extend `lab_runtime/m2_contract.py`.

1. Add failing tests for `/chat/completions + return_token_ids` step-wise prompt/generated token preservation, one finite rollout logprob per generated token, position/mask/weight-version alignment, contiguous per-trajectory turns and exact terminal markers, bounded tool-observation context, EOS/stop handling, context truncation, no transcript re-tokenization, and all-ones assistant response masks.
2. Confirm RED with `python -m unittest tests.test_m2_tito -v`.
3. Implement a dependency-light typed record builder and machine assertions; do not use the current M1 text trace as training input.
4. Add corruption tests proving a one-token difference, shifted logprob/position/mask, stale weight version, or changed template digest fails closed.
5. Run focused and full tests; commit `feat: add exact M2 trajectory contracts`.

## Task 4: Implement the Harbor-to-SkyRL generator bridge

**Status 2026-09-09:** 依赖注入式纯桥接层和5项fail-closed测试已实现：消费精确chat-completion token/logprob、保持逐步顺序、整组剔除无效轨迹、记录mixed group和usage，并在取消或失败时只按已知trajectory ID回收。真实Harbor 0.4 `ModalEnvironment`适配与冻结SkyRL `GeneratorInterface`进程内兼容仍未运行；本项代码完成不清除这两个真实入口门槛。

**Files:** create `recipe/generator.py`, `tests/test_m2_generator.py`; reuse `lab_runtime/m1_agent.py`, SWE hooks, and cleanup helpers without copying policy logic.

1. Add failing tests using fake inference/trial adapters for endpoint discovery, exact model name, input order, frozen group ID and `n_samples_per_prompt`, valid reward 0/1, invalid exclusion, mixed-within-group detection, stop reasons, cancellation, late usage, and known-ID cleanup.
2. Confirm RED, then implement the smallest custom-generator adapter compatible with the frozen SkyRL contract.
3. Assert that generator config fields that SkyRL does not enforce for custom generators are explicitly consumed or rejected; do not accept silent no-op flags.
4. Verify `GeneratorOutput` shape, exact token IDs, loss masks, and reward alignment without importing CUDA in setup workers.
5. Run focused and full tests; commit `feat: bridge Harbor trajectories into SkyRL`.

## Task 5: Implement update, weight-sync, and recovery evidence

**Files:** create `recipe/checkpoint.py`, `tests/test_m2_checkpoint.py`.

1. Add failing tests for base/adapter identity, optimizer parameter allowlist, nonzero effective loss tokens/advantage/pre-step policy-gradient norm, pre/post update digest, finite update norm, inference-side loaded-tensor receipt, next-rollout version binding, echoed/stale adapter rejection, atomic checkpoint manifest, and fresh-process reload receipt.
2. Confirm RED and implement pure identity/receipt validation around injectable framework hooks.
3. Ensure output changes alone cannot pass weight-sync; base-model mutation fails.
4. Run focused/full tests and commit `feat: verify M2 weight handoff and recovery`.

## Task 6: Build fixed cloud-training entrypoints and environment preparation

**Files:** create `recipe/entrypoint.py`, `scripts/m2_run.py`, `tests/test_m2_entrypoint.py`; finalize stack and run configs.

1. Add failing tests for exact provider/config identity, controller-only credential loading, provider-secret scoping, no secret propagation into task/model records, no Mac training, source-lock verification before writes, dedicated cloud-GPU and persistent-volume receipts, exact sandbox cleanup, durable summary phases, attempt-ledger exactly-once semantics, and no duplicate submission.
2. Implement `prepare`, `capacity-canary`, `bringup`, and `scale` subcommands with explicit config paths; scripts only dispatch `python -m recipe.entrypoint`. Failed mutating runs are not auto-resumed or overwritten.
3. Freeze an isolated Modal image plus Lab-specific durable Volume. Do not alter the existing M0/M1 environment on home-5090.
4. Preserve the rejected one-shot Daytona result and its no-create cleanup proof; use the completed Modal L40S/CUDA/Volume probe as platform evidence without rerunning it.
5. Run CPU/interface tests, commit, push, then execute the approved probe. Persist dependency versions and no-GPU-import setup evidence before the real capacity canary.

## Task 7: Execute and accept M2-A

**Files:** `configs/m2-bringup.json`, current docs, remote audit artifacts.

1. Generate and inspect an immutable Modal GPU plan with one L40S, exact timeout, Lab-only durable Volume, nested Modal CPU creation cap, and stop conditions.
2. Submit once using the user's approved design note; save provider sandbox/App/call identities.
3. Monitor by run ID. Never kill or adopt unknown processes.
4. Validate a real 2-4 task chain: after invalid filtering at least one group retains >=2 samples with both reward 0 and 1; exact token/logprob/position/mask/weight-version alignment; finite loss/backward/optimizer; nonzero effective training tokens, advantage, pre-step policy gradient, and update; inference-side content receipt and next-rollout version binding; fresh-process reload; typed errors; and complete cleanup.
5. On failure, preserve artifacts and fix with a new failing regression test before a new committed plan. Do not expand the task count.
6. After fresh verification and review, update docs and commit/push the M2-A result.

## Task 8: Execute overfit16 binary-reward learning

**Files:** `configs/m2-overfit16.json`, current docs, remote artifacts.

1. Freeze nested task IDs, start-checkpoint digest/continuation policy, group size, sampling, maximum updates, generated/training token budgets, evaluation points, resource envelope, paired repeats, machine-readable positive/bounded-negative/invalid outcomes, broad-dev20 regression rule, and exact stop rules based on M2-A measurements.
2. Capture the base overfit16/dev20 panel without final-test feedback.
3. Run the approved durable overfit job; report all-zero/mixed/all-one groups, reward, valid rate, KL/entropy/grad/update norms, logprob deltas, stage timings, and cost.
4. Repeat the fixed comparison once within the registered budget.
5. Classify the result as positive, bounded negative, or invalid. Do not lower correctness gates to manufacture a positive result.
6. Verify, review, document, commit, and push.

## Task 9: Conditional scale40 and train80

**Files:** `configs/m2-scale40.json`, `configs/m2-scale80.json`, current docs and remote artifacts.

1. Generate deterministic nested 40/80 sets with A/B balance and leakage-group checks; RED tests precede selection implementation.
2. Freeze each stage only from a **positive** prior-stage classification plus timing/reward evidence and dev20; final100 remains sealed from the selector.
3. Run scale40 from the explicitly bound checkpoint. Promote only if the frozen positive rule and all hard correctness/resource/regression gates hold.
4. Run train80 under the same rule. A bounded-negative or invalid result stops promotion.
5. Before final100 access, write an immutable terminal decision proving all training/promotion/checkpoint selection is over. Then evaluate frozen base and the single terminal candidate together. Any visible final100 result permanently closes training for this study.
6. Verify, independently review, update M2 status/budget, commit, push, and tag only the actually completed milestone.

## Task 10: Add EvoCodeBench10 as an external evaluation panel

**Files:** future `configs/evocode10-source.json`, importer/validator tests, separate task manifest, docs.

1. Keep EvoCodeBench outside all training and checkpoint-selection paths. Training would require documented author permission and a separate license/distribution review.
2. Pin the post-2026-06-20 clean release and exact task-only archive SHA; validate the archive with bounded extraction and no trajectories.
3. Select 10 whole tasks by registered categories and resource limits. Never split rounds of one task across purposes.
4. Test patched `/tests` and `/logs/verifier` clearing before every agent phase or use separate-verifier mode.
5. Keep all 10 in an external frozen panel and preserve CC-BY-NC attribution/noncommercial-use metadata.
6. Run representative oracle/NOP before any model workload; preserve per-step binary reward and case score only as diagnostics.

## Task 11: Final verification and handoff

1. Run the full local suite, focused remote acceptance readers, `git diff --check`, config/hash validation, and exact Git status.
2. Use the verification-before-completion checklist against every claimed M1/M2 gate.
3. Request independent code review over the final commit range; resolve all Critical/Important findings and re-run verification.
4. Push the independent repository after each verified logical batch, as previously authorized.
5. Report exact commits, plans, runs, units, artifact paths/hashes, actual costs, resource cleanup, passed gates, stopped gates, and unverified boundaries.
