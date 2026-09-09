# M2 Binary-Reward Scale Design

**Date:** 2026-09-09

**Status:** Approved for the existing 200-task pool. EvoCodeBench is evaluation-only: its official dataset card is CC-BY-NC-4.0 and says the dataset is not intended for model training. Adding it to training would require author permission and a separate license/distribution review, not only project approval.

## Objective

Prove that the Lab can train Qwen3-4B from real Harbor agent trajectories with strict binary task rewards, then expand only when the smaller stage provides valid, attributable learning evidence. The target progression is M2-A on 2-4 tasks, M2-B on a frozen 16-task overfit set, then conditional 40-task and 80-task training. The 20-task development split is evaluation-only and the 100-task final split never enters training.

## Current boundary

- The 200-task asset set is assembled as train80/dev20/final100, but M1 is not closed: a representative Terminal-Bench control, `overfit_16`, and frozen model baselines remain.
- A four-task Qwen3-4B to Daytona pilot proved the model/tool/verifier/cleanup path and produced valid rewards `0/1/0/0`; it performed no optimizer step.
- `recipe/` contains no trainer implementation. SkyRL, the GPU dependency lock, TITO, loss masks, weight synchronization, and checkpoint recovery are unimplemented.
- Mac remains the source, Git, review, and short CPU-test control plane. M1 inference remains on shared home-5090 alongside Ollama, guarded by live free-memory admission and owned-process cleanup. M2 training does not use home-5090. The one-shot Daytona GPU request was rejected, so Modal now owns the single-GPU trainer and Harbor CPU task sandboxes. No Mac model, container, trainer, or durable service is introduced.
- The bounded Modal L40S/CUDA/Volume probe passed. Harbor 0.4 Modal task compatibility, the complete-stack capacity canary, and the strict step-wise bridge remain hard blockers before training.

## Architecture

### Control and execution

```text
Mac committed source/config
  -> bounded Modal API submission
  -> dedicated cloud GPU sandbox + durable Lab volume
       -> SkyRL trainer + colocated vLLM on one GPU
       -> Harbor Trial controller
       -> Modal CPU sandbox per rollout
       -> verifier reward (0/1 or invalid)
       -> token/mask/per-token rollout-logprob training batch
       -> LoRA optimizer step
       -> inference weight identity update
       -> checkpoint + audit JSON
```

The first implementation target is a single coherent released SkyRL custom-generator contract, using the upstream Harbor-generator pattern as a reference without importing the parent Syncopate environment or business code. Exact SkyRL, PyTorch, vLLM, Transformers, Ray, Harbor, CUDA, and driver identities must be frozen together before GPU execution. A dependency being latest does not establish compatibility.

The one-GPU topology is also part of the lock: a dedicated Modal L40S, `colocate_all=true`, `run_engines_locally=true`, vLLM sleep/wake enabled, LoRA policy under FSDP, and reference/critic/reward-model components disabled. Before any task sandbox is created, the training Function must mount the Lab Volume and complete one no-task sleep/train/wake/inference capacity canary. The rejected Daytona probe is not retried and does not change home-5090.

### Trajectory contract

Every accepted rollout has a versioned record with:

- task and split identity, source revision, task-tree hash, attempt ID, seed, and sampling parameters;
- exact prompt token IDs and generated token IDs returned by the inference engine;
- one finite rollout logprob for every generated token, with identical position, mask, and rollout-weight-version lengths;
- role/span boundaries and a loss mask generated from those boundaries;
- tool-call requests, bounded tool observations, stop reason, usage completeness, and truncation state;
- verifier outcome: `valid_reward` with numeric 0/1, or a typed `invalid` reason with no numeric reward;
- rollout weight version and the trainer weight version used for the next inference phase.

M2 uses SkyRL's documented step-wise route: every turn calls `/chat/completions` with vLLM `return_token_ids` and logprobs, and every turn becomes one training sample. Training consumes the returned prompt IDs, generated IDs, and per-token rollout logprobs directly. Re-tokenizing transcript text is diagnostic only and cannot satisfy TITO; the existing M1 text trace and upstream HarborGenerator's re-tokenized output are not training trajectories. The prompt template is immutable. Tool observations appear only in the next turn's returned prompt context; every returned assistant completion token has loss mask 1. Step samples remain contiguous by trajectory, terminal-step markers are exact, and prefix merging stays disabled until separately verified. A one-token or one-position mismatch fails before backward.

### Update and weight identity

M2-A uses LoRA over the fixed Qwen3-4B base. The optimizer receives only registered adapter parameters. Each step records pre/post adapter digests, nonzero effective loss-token count, nonzero group-relative advantage, and the policy gradient norm measured before the optimizer step. Weight decay or numerical drift alone cannot satisfy the update gate.

The trainer publishes an adapter version plus content digest. The inference loader must produce a content receipt that the request cannot self-report, using the loaded adapter manifest and a registered tensor digest sample; the next rollout response must bind to that loaded version. Merely echoing the requested version or observing changed text fails the weight-sync gate.

A checkpoint is accepted only after a new process loads the frozen base plus saved adapter, reports the expected identities, and completes inference. Base-model files are read-only and their hashes must remain unchanged.

## Experimental progression

### M1 closeout

1. Re-run the complete local regression suite.
2. Complete a bounded representative Terminal-Bench NOP/oracle execution path without claiming all 50 images are platform-certified.
3. Freeze one model/sampling/resource contract for train/dev screening.
4. Screen only train/dev and deterministically select A8+B8 whose frozen sampling protocol contains usable within-prompt reward groups; cross-prompt 0/1 variance is only descriptive.
5. Persist base train/dev results separately from task validation. M1 must not reveal final100 outcomes.

### M2-A: 2-4 task bring-up

Run the smallest set with a frozen `n_samples_per_prompt`. After invalid outcomes are removed, at least one prompt group must retain two or more valid samples and contain both reward 0 and reward 1. One real run must demonstrate rollout, reward, nonzero group-relative advantage, nonzero effective policy-loss tokens, loss, backward, nonzero pre-step policy gradient, optimizer, nonzero adapter update, content-proven weight handoff, and fresh-process checkpoint recovery. TITO/logprob/mask assertions, finite-number checks, typed invalid handling, exact Modal object cleanup, GPU process ownership, and artifact identities are hard gates.

Logprob difference, timing, throughput, and cost are observations, not pre-run pass thresholds.

### M2-B: frozen overfit16

Use strict final reward 0/1 and no partial-reward modification. Report all-zero, mixed, and all-one prompt groups because group-relative training has no learning signal when every sample in a group receives the same reward. Freeze total generated/training token budgets, sampling parameters, maximum updates, evaluation points, and stop conditions before launch.

Before launch, the stage freezes machine-readable positive, bounded-negative, and invalid outcomes; paired base/candidate repeats; and what constitutes repeated broad dev20 regression. The result is positive learning evidence only when improvement repeats on that protocol and cannot be explained by invalid runs, verifier exposure, task-byte drift, or a stale inference adapter.

### Conditional scale

Promote from 16 to 40 and then train80 only if the prior stage is classified **positive** by the frozen machine rule, preserves every M2-A correctness gate, remains within its registered resource budget, and does not meet the frozen repeated broad-dev20-regression rule. A bounded negative or invalid stage stops scaling; it is not an automatic promotion.

Every scale stage gets a new immutable config and run ID. The task lists are nested and deterministic. Each config binds its start-checkpoint content digest and states whether it continues the preceding checkpoint or restarts from the same base; comparisons must not confuse more data with more training time.

The final100 split is a one-time terminal evaluation. Before unsealing it, the controller writes an immutable decision that training, promotion, checkpoint selection, prompting, and hyperparameter changes are finished. Base and the one terminal candidate are evaluated in the same frozen batch. Once any final100 result is visible, this study cannot resume training or promotion; a later study needs a new holdout.

## EvoCodeBench quarantine

EvoCodeBench is structurally useful because it supplies persistent Harbor tasks with 5-15 cumulative binary-scored steps. Its task-only release is about 235 MB for 26 tasks, but the dataset card marks it CC-BY-NC-4.0, limits intended use to evaluation, and says it is not intended for model training. The repository also documents a historical shared-verifier leak and requires either the patched clearing behavior or separate-verifier mode.

Current rule:

- do not download its task assets until the eval panel importer is preregistered;
- do not add it to train/dev/final counts;
- keep a future selection contract of 10 whole tasks, never splitting steps from one task across purposes;
- freeze 10 tasks as a separate external evaluation panel;
- do not create a training branch without documented author permission plus license, attribution, noncommercial-use, and derivative-model distribution review.

## Failure handling and stop conditions

- Any environment, model-HTTP, test-collection, verifier-protocol, missing-reward, cleanup-unknown, NaN/Inf, TITO, mask, weight-identity, or checkpoint-reload failure is not a model reward zero.
- A cleanup-unknown result stops new Modal object creation for that campaign. A cloud GPU or task sandbox with uncertain ownership or deletion state stops the run without adopting or deleting unrelated objects.
- The plan stops scaling when reward groups have no usable variance, dev repeatedly degrades, the registered wall-time/cost envelope is exceeded, or task/harness identities drift.
- Failed runs keep immutable evidence and are read-only. Mutating phases do not auto-resume: a new attempt requires a new run/attempt identity, while the ledger prevents replay of provider creation or optimizer actions. A submitted `hlab` plan is never submitted twice after an uncertain response.
- Modal uses its runtime identity for nested Harbor CPU sandboxes; no Daytona credential is copied into cloud training. Provider identity must not enter committed configs, task sandboxes, model prompts, trajectories, or logs. Training and task objects must be destroyed and confirmed by exact object identity.

## Documentation and artifacts

- Versioned experiment configs live in `configs/`.
- Reusable Python implementation lives in `recipe/` and `lab_runtime/`; `scripts/` contains fixed entrypoints only.
- Small summaries and manifests are committed. M1 assets remain under `/home/samwang/data/sandbox-rl-MOPD-lab/`; M2 environments, trajectories, checkpoints, caches, and large logs stay in the selected cloud provider's Lab-specific durable volume and never depend on the Mac filesystem.
- `docs/EXPERIMENTS.md` records observed runs and conclusions; `docs/BUDGET.md` records preregistered and actual resources; the total plan is rewritten in place rather than accumulating conflicting status.

## Acceptance boundary

M2-A completion means the real update and recovery chain is proven. M2-B completion means a frozen 16-task binary-reward experiment has repeatable learning evidence or a bounded negative result. Scale40/train80 are conditional extensions, not implied successes. Neither task availability, a green unit-test suite, nor a successful model-only pilot is an M2 acceptance claim.
