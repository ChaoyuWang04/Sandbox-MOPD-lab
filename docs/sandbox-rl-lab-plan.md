# Sandbox RL Lab · 沙箱化 Agentic RL + 多师 OPD 实施计划书

> 当前执行入口：[M0 实施计划](plans/2026-09-07-m0-environment-plan.md)；独立边界：[README](../README.md)。2026-09-07 已批准并执行 M0-A（目录、只读清点与官方接口核对），完整 M0 Gate 尚未运行。物理根目录统一为 `sandbox-rl-MOPD-lab/`，下文实验命名 `sandbox-rl-lab` 保留用于 W&B。
> 2026-09-07 用户已排除 RunPod，优先按 HOME-5090 手册调用现有资源，允许沙箱位于 home-5090、Modal 或 Daytona；取消沙箱必须与 GPU 同机的要求。三端表仍是候选设计，未启动付费运行。平台与控制器现场见 [HARBOR_NOTES](HARBOR_NOTES.md)。提交/tag/push 仍按用户明确授权执行。

> 目标：以最小成本在真沙箱（容器）环境里跑通长程 agentic RL 全链路，并完成两个有原创价值的实验：
> ① **Runtime 稳定性三臂消融**——定量回答"不稳定的执行环境到底给 RL 训练带来多大伤害、以何种机制伤害"；
> ② **Multi-teacher OPD**——两个 RL 专家教师 → 一个学生的在线蒸馏能力融合。
>
> 参考范式：Mercor/SkyRL《397B RL training guide》的六步法（前三步 de-risk 不烧算力）、Weco AIDE² 的 public-private 分数分离与固定预算选择压力。
>
> 本文档供 coding agent 执行。**严格按里程碑推进，每个 Gate 全部通过并记录进 `docs/EXPERIMENTS.md` 后才进入下一阶段。**

---

## 0. 全局约定（agent 必读）

### 0.1 三端分工

| 端 | 角色 | 用途 |
|---|---|---|
| Mac (24GB) | 指挥部 | 代码编写、任务生成、结果分析、wandb 看板；不跑训练 |
| 5090 服务器 (32GB, sm_120) | 筛选与调试 | vLLM 推理做任务筛选（pass@8）、本地 docker 沙箱、管线冒烟 |
| Modal GPU（卡型与容量待 M2 计划冻结） | 云训练候选 | rollout(vLLM) 与 LoRA 训练摆放实测；工具沙箱可独立使用 Modal/Daytona |

home-5090 的筛选任务经已注册 hlab recipe 执行；没有 recipe 时先接入，不使用临时 SSH 后台训练。后文 pod 一词泛指云训练资源，不代表 RunPod，也不要求内嵌 Docker。

### 0.2 执行纪律

1. **门禁制**：M0→M1→M2→M3→M4→M5。每个 Gate 的验收命令真实运行，数字写入 `docs/EXPERIMENTS.md`（模板见 0.5）。
2. **de-risk 优先**：M0–M2 是 Mercor 六步法的前三步，只允许花小钱（5090 为主，pod 冒烟 ≤2 小时）。大额 pod 时间只准花在 M3/M4。
3. **单一变量**：所有对比实验先写预测再跑；同臂内除受控变量外配置逐字节相同（含种子集合）。
4. **预算账本**：`docs/BUDGET.md` 记录每次 pod 会话的时长×单价。全项目硬上限 **$500**；单里程碑超预估 50% 时暂停并复盘。
5. **框架事实以官方文档为准**：本文写的是**行为契约**（要达到什么效果）；SkyRL/Harbor 的具体 flag 名以当前版本文档为准，agent 把实际使用的配置全文存入 `configs/` 并在 EXPERIMENTS.md 标注版本号。
6. 每里程碑打 git tag：`m1-taskpool` / `m2-pipeline` / `m3-chaos` / `m4-opd` / `m5-report`。
7. 所有训练曲线上 wandb（project: `sandbox-rl-lab`），run 命名 `{milestone}-{arm}-{seed}`。

### 0.3 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 模型 | **Qwen3-4B**（bf16）| 降级预案：任务学不动 → Qwen3-8B；升级预案：都太容易 → 加难任务而非换模型 |
| RL 框架 | **SkyRL + Harbor**（主）| 起点：Mercor 的 ApexAgents-SkyRL-Recipe 仓库结构；**fallback：verl colocate**（触发条件见 M2-G1 失败分支） |
| 微调 | LoRA rank 32, alpha 64, 全线性层 | 优化器只吃 LoRA 参数；具体卡型容量和 colocate 可行性由 M2 实测 |
| 沙箱 | Harbor Docker（home-5090 优先调查）或原生 Modal/Daytona provider | 沙箱可与 GPU 分离；同一 M3 三臂保持 provider 不变 |
| Rollout | vLLM ≥0.8，同 pod | `gpu_memory_utilization` 训练/推理分割按 M2 实测定 |
| 算法 | GRPO + 组内 baseline；配方见 0.4 | dense 模型，无需 GSPO |
| 蒸馏 | 自写 OPD 脚本（peft + vLLM + HF forward）| OPD 无需 RL 机器，teacher 只前向 |
| 观测 | wandb + EXPERIMENTS.md | 复用 harness-lab 的记录纪律 |

### 0.4 算法配方（M2 起全程锁定，M3/M4 不再动）

- GRPO：group size **8**，batch **16 prompts/step**（=128 轨迹/step），同步训练（单卡不搞 fully-async）
- Loss 聚合：**prompt_mean**（每组等权、组内 token 等权；Mercor 实测优于 token_mean +3.9，因轨迹长度方差大）
- **动态采样**：组内奖励零方差（全 0 或全 1）的组丢弃不进 loss，并在 wandb 记 `zero_variance_group_rate`
- **Tool observation loss masking**：工具返回 token 只做 context 不进 policy loss（TITO 管道保证）
- **Context nudge**：生成预算剩 20% 时注入"请收尾并给出最终答案"系统提示
- 超长处理：超预算轨迹整条 reward=0（不做 OLF 掩码——与 Mercor 结论一致）
- 采样：temperature 1.0 / top-p 1.0（rollout）；评测 temperature 0.6 × 3 遍取均值
- TIS：挂 token 级截断 C=2.0 保险；wandb 记 `fraction_high`（w 触顶比例）
- LoRA lr 1e-5，cosine，warmup 10 步；KL 正则不加（LoRA 本身是约束）
- 上下文上限 **16k**，最大 **10 轮**，单轨迹生成预算 **4k tokens**（成本控制的核心：任务必须是"微型长程"）

### 0.5 EXPERIMENTS.md 记录模板

```markdown
## [M3-EXP-B-seed1] 噪声计入奖励臂
- 时间/pod规格/单价: ...
- 配置文件: configs/m3_arm_b.yaml @ commit abc123
- 预测: <跑前写下>
- 关键数字: final clean pass@1 = __, 步数 = __, 熵终值 = __, env方差占比均值 = __
- 曲线: wandb link
- 结论: PASS/FAIL + 一句话
```

---

## M0 · 三端环境就绪（0.5-1 天，成本 ≈ $5）

### 步骤

1. **Mac**：维护本 Lab 源码与文档；Python 3.12/uv 已清点，依赖在 Lab 独立环境中准备。按 HOME-5090 手册审查并同步明确 commit；W&B 配置使用 Lab 路径。
2. **5090**：
   ```bash
   # 先通过 hlab doctor/projects/recipes 核对，注册并审查 Lab 入口。
   # 用 Lab 独立环境、固定模型 revision 与 commit 启动 Qwen3-4B。
   # 16k 上下文、4k 输出与工具调用需实际验收；不在共享 venv 临时安装。
   ```
   补齐 Docker 前置后从 1/4/16 并发验证资源限制与回收；云 provider 则用相同任务验证，不混用结果。
3. **执行环境模板**：先选定并冻结 GPU/沙箱拓扑、镜像与依赖，之后编写固定准备入口（暂定 `scripts/pod_setup.sh`，尚未实现）。home-5090 走 hlab；Modal 由 Mac 直接控制。保持真实容器隔离，不降级为普通进程。30 分钟有界冒烟的具体资源和停止线须先登记。
4. **Harbor 熟悉**：本地（Mac 或 5090）跑通 Harbor 官方示例 task 一个 trial 的完整生命周期（env start → agent.run → verify → teardown），记录其 task 目录格式（prompt / config / verifier 的确切文件约定）到 `docs/HARBOR_NOTES.md`。

### Gate M0

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 5090 推理 | Qwen3-4B 在 16k 上下文下可服务，单请求 4k 生成 < 90s |
| G2 | 沙箱并发 | 选定 provider 的 16 并发容器创建+销毁总耗时 < 60s；冷启动单列，不能将预热结果冒充冷启动 |
| G3 | Harbor 生命周期 | 官方示例 trial 端到端跑通，HARBOR_NOTES.md 完成 |
| G4 | 执行环境模板 | 选定拓扑的固定入口从零到全依赖就绪 ≤20 分钟，成本记入 BUDGET.md；目前入口未实现 |

---

## M1 · 任务池构建与筛选：解决零奖励问题（1-2 天，成本 ≈ $0，全在 5090）

**核心原则**：训练集必须落在 pass@8 ∈ **[12.5%, 75%]** 的"梯度甜区"（即 8 次采样至少 1 次过、至多 6 次过）。全 0 组无梯度，全 1 组无信息。

### 1.1 任务来源（三路并行，目标候选池 ≥ 300）

**来源 A · 自建微型终端任务（主力，目标 200+）**：用 Claude 批量生成"文件系统/文本处理/数据加工"类任务，每个任务一个 Harbor task 目录：

- `prompt.md`：任务描述（如"仓库里有 logs/ 目录若干 .log 文件，统计所有 ERROR 行按小时分布，结果写入 report.csv，列为 hour,count"）
- `setup.sh`：构造初始世界（生成带随机内容的文件树——**内容随机化保证任务不可背题**）
- `verify.py`：**私有判分脚本**（agent 不可见），检查终态文件/输出，返回 0/1
- 任务模板族（每族 ≥25 个变体）：日志统计、CSV 清洗合并、JSON 重排、git 操作序列、文本抽取重组、目录整理、简单 pandas 计算、正则批量替换
- **公私分离**：prompt 里可给 1 个示例输入输出（public），verify.py 用不同的随机数据实例判分（private）——AIDE² 纪律
- 生成后人工抽查 20 个：任务可解、verifier 正确、无歧义

**来源 B · Terminal-Bench easy 子集（外部效度锚，目标 ~50）**：从 Terminal-Bench 仓库导入标记为简单的任务，转成 Harbor 格式。这批**只进评测集不进训练集**（保持外部基准纯净）。

**来源 C · SWE-smith/SWE-Gym 短程子集（可选加餐，目标 ~50）**：筛"单文件、改动 <30 行、测试运行 <60s"的实例。若导入成本超 0.5 天则放弃，在 EXPERIMENTS.md 记录放弃理由。

### 1.2 域划分（为 M4 OPD 预埋）

自建任务打 `domain` 标签：**Domain-FS**（文件/文本/git 系）与 **Domain-DATA**(csv/json/pandas 计算系）。两域任务量各 ≥100。

### 1.3 筛选流程

```bash
# scripts/screen_tasks.py
# 对候选池每个任务: Qwen3-4B, temperature 1.0, 跑 8 个 rollout(走完整 Harbor trial)
# 输出: task_id, domain, pass@8 通过数 k, 平均轮数, 平均生成 token, 失败模式标签
```

- 5090 上 vLLM 服务 + 本地 docker 沙箱并发 16；300 任务 × 8 rollout ≈ 2400 trial，预计 8-16 小时（过夜跑）
- 产出四个集合：
  - **train_pool**：k ∈ [1,6] 的自建任务，目标 **≥ 96 个**（两域各 ≥48）
  - **eval_clean**：train_pool 同分布但不重叠的 40 个（两域各 20，冻结）
  - **eval_external**：Terminal-Bench easy 子集（冻结）
  - **overfit_16**：从 train_pool 挑 k ∈ [2,5] 的 16 个（M2 用）
- 同时产出**基线报告**：base 模型在 eval_clean / eval_external 上的 pass@1（temperature 0.6 × 3 遍均值）——这是全项目的 0 号数字

### Gate M1

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 池规模 | train_pool ≥ 96（两域各 ≥48）；不足则回 1.1 补造任务，**禁止放宽甜区区间** |
| G2 | 甜区分布 | train_pool 的 k 分布直方图落在 [1,6]，中位数 ∈ [2,5] |
| G3 | verifier 可靠性 | 随机抽 15 个任务人工核对判分：误判 ≤ 1 个（≈93% 准确）；flaky 检查——同一成功轨迹重判 3 遍结果一致率 100% |
| G4 | 基线冻结 | base 在 eval_clean 与 eval_external 的 pass@1 数字写入 EXPERIMENTS.md |
| G5 | 非模型错误率 | 筛选全程沙箱/harness 侧错误（超时、容器失败、MCP断连等非模型原因）占 trial 比例 **< 2%**（Mercor 纪律：训练前把非模型错误压到接近零）|

---

## M2 · 训练管线 bring-up + 过拟合验证（1-2 天，成本 ≈ $30-60）

**目标**：Mercor Step 1-3 的缩微复刻——TITO 对账、训推 logprob 对齐检查、16 任务过拟合跑。**这是花大钱前的最后一道保险。**

### 2.1 仓库结构

```
sandbox-rl-MOPD-lab/
├── configs/                     # 每个实验一份完整 yaml（版本随 git）
├── tasks/                       # M1 产出的 Harbor task 目录树 + 集合清单(json)
├── recipe/
│   ├── generator.py             # SkyRL GeneratorInterface 实现: 每 trial 一个 Harbor Trial
│   ├── agent.py                 # 多轮 tool-calling agent(BaseAgent 子类): bash/read/write 工具
│   ├── tito.py                  # token-in-token-out 对账(参照 Mercor recipe 方案一: /completions)
│   ├── chaos.py                 # M3 故障注入中间件(本阶段空壳)
│   └── nudge.py                 # context nudge 注入
├── opd/                         # M4 蒸馏脚本(本阶段空)
├── scripts/
│   ├── pod_setup.sh / screen_tasks.py / eval_checkpoint.py / logprob_diff_check.py
└── docs/EXPERIMENTS.md, BUDGET.md, HARBOR_NOTES.md
```

Agent 工具面刻意最小：`bash`（在沙箱内执行）、`read_file`、`write_file`、`submit`（宣告完成触发 verify）。工具 schema 严格 Pydantic。

### 2.2 关键正确性检查（顺序执行）

1. **TITO 对账**：随机 20 条多轮轨迹，断言"训练侧拼接的 token 序列 == rollout 侧逐轮生成/输入 token 序列"逐位相等；任何 re-tokenize 即 FAIL。
2. **Loss mask 可视化**：dump 3 条轨迹的 mask，人工确认工具返回段全 0、assistant 段全 1、nudge 注入段为 0。
3. **训推 logprob 对齐**（Mercor Step 2.4）：跑 3 个训练 step，比较 trainer 重算 logprob 与 vLLM 记录值，`mean |Δlogprob|` 记入 wandb。**< 0.03 健康**；≥0.05 时先查 lm_head 精度与 LoRA 权重同步路径再继续。
4. **权重同步验证**：更新一步后，vLLM 端采样分布确实变化（同 prompt 同 seed 的输出 logprob 位移 > 0）。

### 2.3 过拟合跑（overfit_16）

- 配置：0.4 节配方，batch=16（即每 step 全池一遍），**同步**训练，跑 ≤ 60 步
- 期望形态：训练 reward 在 **≤30 步内从基线升至 ≥ 0.85**（Mercor：小任务集应在少量步内看到清晰学习信号）
- 若失败，按 Mercor 的回溯顺序排查：verifier 判分逻辑 → harness 缺陷（读轨迹！按工具统计失败率找模式）→ 奖励方差分布 → 最后才怀疑算法超参
- **顺手复刻一个 Mercor 发现**：过拟合前先用 20 条 base 轨迹做"harness 体检"——读轨迹统计：工具解析失败率、因缺包/缺工具浪费的轮数占比、上下文爆掉率。能修的先修（这就是"零训练白捡分数"的来源），修完把 eval_clean 基线**重测一遍**（harness 修复会抬基线，M3 必须用修复后的基线）。

### Gate M2

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 框架可用 | SkyRL+Harbor 在 pod 上单卡跑通完整 RL step 循环。**失败分支**：折腾 > 1.5 天仍未通 → 切 verl colocate 方案（agent-loop + 自接 Harbor trial），在 EXPERIMENTS.md 记录切换理由与两框架对比笔记 |
| G2 | TITO | 20/20 轨迹逐位相等 |
| G3 | logprob 对齐 | mean |Δlogprob| < 0.03 |
| G4 | 过拟合成功 | overfit_16 训练 reward ≤30 步达 0.85+，曲线截图入档 |
| G5 | 吞吐画像 | 实测单 step 墙钟时间与瓶颈归属（rollout / verify / 训练三段计时），推算 M3 单臂成本并更新 BUDGET.md；**若单 step > 6 分钟**，先缩任务(轮数/生成预算)再进 M3 |
| G6 | harness 体检 | 修复清单 + 修复前后 eval_clean 基线对比数字（这本身是一条可讲的实验结果）|

---

## M3 · Runtime 稳定性三臂消融（2-3 天，成本 ≈ $100-180，本项目主实验）

**科学问题**：执行环境的非模型故障，(a) 计入奖励 与 (b) 过滤丢弃，各自以什么机制、多大幅度伤害训练？

### 3.1 故障注入中间件 `recipe/chaos.py`

包在工具执行与 verifier 外层，env 控制，**注入点打标**（每条轨迹带 `env_error_injected: bool/type`，这是后续所有分析的地基）：

```
CHAOS_TOOL_TIMEOUT_RATE=0.10    # 工具调用挂起至超时(模拟 MCP 断连/慢工具)
CHAOS_SANDBOX_KILL_RATE=0.05    # trial 中途容器被杀(模拟 OOM/驱逐)
CHAOS_VERIFIER_FN_RATE=0.10     # verifier 假阴性: 实际通过判 0(模拟 flaky 判分)
```

（合计期望 ~22% 轨迹受影响；假阳性不注入——假阴性是现实主导且机制更干净。）

### 3.2 三臂设计（唯一差异 = 故障处理策略）

| 臂 | 沙箱 | 故障处理 | 模拟的现实 |
|---|---|---|---|
| **A · 干净** | 无注入 | — | 加固到位的环境（Mercor 推荐态）|
| **B · 计入** | 注入 | 受影响轨迹照常记 reward（多为 0）| "没发现环境在坏"的团队 |
| **C · 过滤** | 注入 | 检测到 env_error 的轨迹**整条丢弃**，组内按剩余轨迹算 advantage | "mask 错误"路线（Mercor 警告不要过度依赖的那条）|

- 每臂 **2 seeds**，各 **120 步**，每 20 步在 eval_clean 上评一次（temperature 0.6 × 2 遍）
- 共 6 个 run；按 M2-G5 实测成本执行，超预算则先砍到每臂 1 seed + 加跑关键臂第 2 seed

### 3.3 观测指标（wandb 全程）

**主指标**：eval_clean pass@1 学习曲线 + 120 步终点值（三臂对比，带 2-seed 区间）
**机制指标**（实验的灵魂）：
1. `env_variance_share`：每组 (Var_all − Var_[非注入轨迹]) / Var_all 的批均值 —— B 臂的病灶定量
2. `advantage_corruption`：被注入且本可通过的轨迹拿到的平均 advantage（应为负——这就是打向好策略的错误惩罚）
3. **选择偏差三件套**（C 臂的病灶，Syncopate 假设的复现场）：被丢弃 vs 保留轨迹的{长度、工具调用数、轮数}分布对比（均值差 + KS 检验）；策略平均轨迹长度随步数的漂移曲线（三臂同图）
4. 熵曲线、`zero_variance_group_rate`、有效样本量（每 step 实际进 loss 的轨迹数）
5. `fraction_high`（TIS 触顶率）

### 3.4 预注册预测（跑前写入 EXPERIMENTS.md，跑后对照）

- P1：终点 pass@1 排序 A > C > B
- P2：B 臂 `advantage_corruption` 显著为负，学习曲线更晚起跳或更低平台
- P3：C 臂被丢弃轨迹显著更长/工具调用更多（KS p<0.05），且 C 臂策略轨迹长度向下漂移快于 A——**方向性选择偏差实锤**
- P4：C 臂有效样本量 ≈ A 的 78%，其 vs A 的差距部分可由样本效率解释、部分不能（不能的部分=偏差的代价）
- （预测错了更有价值——如实记录并分析。）

### 3.5 分析产出

`docs/M3_REPORT.md`：三臂学习曲线主图、机制指标四联图、预测核对表、一段"给生产团队的建议"（把结果翻译成'环境错误率每 X% 折算多少训练效率/终点损失'的经验换算）。

### Gate M3

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 完整性 | 6 run 全部跑完 120 步，无中途配置改动（wandb config diff 校验）|
| G2 | A 臂有效 | A 臂终点 pass@1 比 M2-G6 修复后基线提升 ≥ **10 个百分点**（绝对值）——证明管线真的在学习，消融才有意义 |
| G3 | 机制指标齐全 | 3.3 全部指标有曲线；选择偏差三件套的统计检验有 p 值 |
| G4 | 报告成文 | M3_REPORT.md 完成，预测核对表逐条判定 |

---

## M4 · Multi-teacher OPD（1.5-2 天，成本 ≈ $60-100）

**科学问题**：两个不相交技能域的 RL 专家，能否经 on-policy 蒸馏融进一个学生；重叠/域外区域发生什么。

### 4.1 训练两个教师（复用 M3-A 臂配置）

- **Teacher-FS**：只用 Domain-FS 训练池，120 步（或 eval_clean-FS 提升 ≥10pt 即可早停）
- **Teacher-DATA**：同理，Domain-DATA
- 交叉体检（关键前提验证）：每个教师在**对方域** eval 上应无提升甚至微降——确认"专家确实是偏科的"，否则融合实验无意义

### 4.2 OPD 学生训练 `opd/train_student.py`

自写脚本，不走 RL 框架（OPD 只需：学生采样 → 教师前向 → 反向KL 更新学生）：

- 三方同底座：base(Qwen3-4B, 冻结) + adapter_FS(冻结) + adapter_DATA(冻结) + adapter_student(可训)。**单进程热切 LoRA adapter 做教师前向**——权重只载一份，96GB 十分从容
- 循环：混合两域任务采样 prompt → 学生策略（vLLM 挂 student adapter）rollout 完整多轮轨迹 → 按任务 `domain` 标签路由教师 → 教师对学生轨迹的 assistant token 前向取 logits → **token 级 reverse KL**（学生分布 vs 教师分布，只在 assistant token、工具返回段 mask）→ 更新 student adapter
- 每 rollout 批后学生 adapter 同步回 vLLM
- 步数 ~80，lr 5e-6；wandb 记两域各自的 KL 下降曲线

### 4.3 评测矩阵（全部 temperature 0.6 × 3 遍）

| 模型 \ 评测集 | eval-FS | eval-DATA | eval-external |
|---|---|---|---|
| base | M1 基线 | M1 基线 | M1 基线 |
| Teacher-FS | 应高 | 应≈base | ? |
| Teacher-DATA | 应≈base | 应高 | ? |
| **Student** | 目标≥Teacher-FS−5pt | 目标≥Teacher-DATA−5pt | **最有趣的格子** |

附加分析：找 10 个"两域技能都需要"的混合任务（从 external 或手造），对比 Student vs 两教师 vs base——检验融合是否产生 1+1>max 的组合泛化。

### Gate M4

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 教师偏科确认 | 两教师本域 +10pt 以上、对方域 |Δ| < 3pt |
| G2 | 学生双域保持 | Student 在两域各不低于对应教师 −5pt（绝对值）|
| G3 | 无灾难遗忘 | Student 在 eval_external 不低于 base −3pt |
| G4 | 矩阵完整 | 4×3 评测矩阵全部数字入档 + 混合任务对比小节 |

---

## M5 · 汇总与产出物（0.5-1 天，成本 $0）

1. `docs/FINAL_REPORT.md`：项目总图（管线架构）、M3 主结果、M4 矩阵、总预算实际值 vs 上限
2. **与简历/研究线对照表**：M3 结果 ↔ "异步与训推一致性/环境稳定性"叙事；选择偏差发现 ↔ Syncopate 研究线（同一偏差机制在新场景的复现证据）；M4 ↔ OPD 叙事的实证扩展
3. 开源整理（可选）：脱敏后 push 仓库，README 放三张主图
4. 复盘会（与 Claude）：哪些预测错了、下一步是把 M3 写成 workshop 短文还是扩展 Syncopate

### Gate M5

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 报告 | FINAL_REPORT.md 完成，三张主图可直接用于面试/文章 |
| G2 | 预算 | BUDGET.md 总额 ≤ $500，逐会话可查 |
| G3 | 可复现 | 任一实验可由 configs/ + 固定 seed 重跑（抽查 1 个 run 重跑 20 步曲线重合）|

---

## 附录 A · 降级/升级决策树

| 情况 | 动作 |
|---|---|
| SkyRL 单卡跑不通(>1.5天) | → verl colocate 自接 Harbor（M2-G1 分支）|
| 4B 过拟合都学不动 | → 先查 verifier/harness；仍不行 → 简化任务（减轮数）；最后才 → 8B |
| 任务太容易(k 中位数>6) | → 加难任务变体（多步依赖、更大文件、复合条件），**不换更大模型** |
| pod 单 step >6 min | → 生成预算 4k→3k、轮数 10→8、并发调优；仍不行 → 训练任务池瘦身到 64 |
| Modal 所选 GPU 不可用 | → 重新登记替代卡型并验证容量/正确性，不默认硬件等价 |
| 预算逼近 $500 | → 砍 M4 至单教师 OPD 验证管线，M3 保全 |

## 附录 B · 每日开工/收工清单

```bash
# home-5090：hlab doctor/projects/recipes → 精确 commit/plan → 获批运行 → 保存 run_id
# 云端：Mac 直接控制 Modal，明确函数与 Sandbox ID、超时和清理
# 收工：checkpoint/证据保存 → 记录费用 → 精确停止并查询确认
# 断线后按 run_id 恢复；不通过 tmux/nohup 绕过 hlab
```

## 附录 C · 本计划刻意不做的事（防范围蔓延）

- 不做 SFT 冷启（与 Mercor 同款选择：RL 是最难搞对的部分，直接攻它）
- 不做 fully-async（单卡无意义；staleness 研究属于 Syncopate 线）
- 不使用 RunPod；允许按实测选择 home-5090 Docker 或 Modal/Daytona 托管沙箱
- 不训 MoE（GSPO 话题留在简历项目一的叙事里）
- 不追 Terminal-Bench 榜分（external 集只当外部效度温度计）
