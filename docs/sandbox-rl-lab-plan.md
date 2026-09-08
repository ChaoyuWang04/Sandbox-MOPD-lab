# Sandbox RL Lab · 沙箱化 Agentic RL + 多师 OPD 实施计划书

> 当前执行入口：[M1 200题扩池计划](plans/2026-09-08-m1-200-case-plan.md)；独立边界：[README](../README.md)。M0已完成：G1推理、G2授权8并发降级、G3正常阶段链路通过；2026-09-08用户取消M0-G4冷准备速度门槛，非将历史失败改成通过。M1扩池设计已获批准，尚未建成或验收。物理根目录统一为 `sandbox-rl-MOPD-lab/`，下文实验命名 `sandbox-rl-lab` 保留用于 W&B。
> 用户已排除 RunPod，Mac 仅编辑/控制/短时检查、不启动实际长期服务；优先 HOME-5090 模型计算与 Daytona 大规模 CPU 沙箱，Modal 为小规模对照和后续 GPU 候选。用户报告 Daytona $200 credits，余额/有效期/账户配额尚未核对。两家 smoke 见 [EXPERIMENTS](EXPERIMENTS.md)，运行预算见 [BUDGET](BUDGET.md)。用户已授权独立仓库每批验证后提交推送。

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
| Mac (24GB) | 指挥部 | 代码编写、结果分析、短时控制；不跑模型、沙箱或常驻服务 |
| 5090 服务器 (32GB, sm_120) | 筛选与调试 | vLLM 推理与 agent 调度；当前不要求安装 Docker，任务工具交给远端沙箱 |
| Daytona CPU Sandbox | 大规模工具执行首选 | 先通过小规模 smoke 与配额检查，再按计划扩并发；不自动等同 GPU 训练平台 |
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
| 沙箱 | Harbor 原生 Daytona 优先，Modal 做有界对照 | 沙箱与 GPU 分离；同一 M3 三臂保持 provider 不变 |
| Rollout | vLLM ≥0.8，同 pod | `gpu_memory_utilization` 训练/推理分割按 M2 实测定 |
| 算法 | GRPO + 组内 baseline；配方见 0.4 | dense 模型，无需 GSPO |
| 蒸馏 | 自写 OPD 脚本（peft + vLLM + HF forward）| OPD 无需 RL 机器，teacher 只前向 |
| 观测 | wandb + EXPERIMENTS.md | 复用 harness-lab 的记录纪律 |

### 0.4 算法配方（M2 bring-up候选，实测并预注册后锁定）

以下是待验证配方，不是200题已兼容的默认值。外部任务的上下文、时限和资源先盘点，不为适配4k/10轮而裁剪最终测试题。M3同工作量对照、M4各训练目标的差异另行登记；不在看到最终测试结果后调参。

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
   云沙箱已按最新授权实测16再8；采用8并发降级，原16失败证据保留。不混用provider结果，不要求安装主机Docker。
3. **执行环境准备**：home-5090已通过固定prepare/probe完成依赖、模型与真实推理验证；工具沙箱使用Daytona。2026-09-08用户取消首次从零准备≤20分钟检查，不再为此重装或重下模型；依赖版本、模型身份与运行正确性仍需验证。
4. **Harbor 熟悉**：实际沙箱位于 Daytona/Modal，完整 trial 控制与 agent loop 最终落 5090；Mac 只允许短时接入 smoke。跑通官方示例 env start → agent.run → verify → teardown，确切 task 格式见 `docs/HARBOR_NOTES.md`。

### Gate M0

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 5090 推理 | Qwen3-4B 在 16k 上下文下可服务，单请求 4k 生成 < 90s |
| G2 | 沙箱并发 | 原16并发创建+销毁<60s；2026-09-07用户明确批准16不满足后采用8，实测8通过。冷启动单列，不能将预热结果冒充冷启动 |
| G3 | Harbor 生命周期 | 官方示例 trial 端到端跑通，HARBOR_NOTES.md 完成 |

---

## M1 · 200题试验池：来源、能力和用途分别控制

2026-09-08用户批准以下设计，替代旧32题小池作为最终M1交付目标。200是首轮规模，不是统计充分性或学习成功门槛；当前只有32题自建产物，外部任务尚未真实接入。实施进度、资产身份和逐步验证见[扩池计划](plans/2026-09-08-m1-200-case-plan.md)，旧pilot失败证据不改写。

### 1.1 能力定义与来源配额

- **A：数据处理与数据逻辑**——ETL、过滤/去重/聚合、解析/序列化、数据语义及数值边界；修复pandas数据语义也可属于A。
- **B：系统操作与软件运行逻辑**——文件/Git状态、构建/依赖/配置、异常与资源管理、异步/服务运行。
- **A×B：组合**——完成目标必须依赖两种能力；使用终端或读取文件本身不构成组合任务。
- **OOD：域外**——本轮未专门训练的安全、科学或专业ML等能力。

| 来源 | A | B | A×B | OOD | 总计 |
|---|---:|---:|---:|---:|---:|
| 自建 | 24 | 24 | 0 | 0 | 48 |
| SWE-smith | 26 | 26 | 0 | 0 | 52 |
| SWE-Gym | 20 | 20 | 10 | 0 | 50 |
| Terminal-Bench | 10 | 10 | 10 | 20 | 50 |
| 总计 | 80 | 80 | 20 | 20 | 200 |

这是逐题标注前的目标配额，不是已验证分布。候选不满足时报告缺口，不伪造类别或用不可运行题补数。SWE-smith采用已发布实例，覆盖程序化/模型生成等来源，不自行启动大规模造bug工程；SWE-Gym提供真实issue任务，不用SWE-bench替代。Terminal-Bench只评测，不训练或调参；导入前冻结具体版本，不能依赖漂移的latest。

自建48保留原32题字节与ID：logs/CSV/JSON共24题在新元数据中归A，inventory8题归B；新增16题采用Git、配置或运行行为等新B模板，不只给旧聚合模板加seed。旧FS/DATA标签及frozen-v1清单保留，新分类写入v2。32实例不代表32种独立能力。

### 1.2 数据切分与泄漏隔离

| 用途 | A | B | A×B | OOD | 总计 |
|---|---:|---:|---:|---:|---:|
| train（教师与学生prompt） | 40 | 40 | 0 | 0 | 80 |
| dev（选择checkpoint与调参） | 10 | 10 | 0 | 0 | 20 |
| final-test（冻结最终测试） | 30 | 30 | 20 | 20 | 100 |
| 总计 | 80 | 80 | 20 | 20 | 200 |

TB50全在final-test；组合20和OOD20首轮不进入训练。旧16题冻结eval保留在final-test中，明确属于同模板新实例诊断，不冒充模板族完全隔离。其余任务按预先登记的repo/function/PR/patch/template泄漏组分配；跨SWE来源去重，同函数变异与同issue不跨split。分别报告同仓库与仓库留出、同模板与族留出面板；同仓库不等于相同函数/issue，不能冒充仓库留出。旧16题是唯一已批准的同模板跨split诊断例外；其余候选与其预注册泄漏组隔离要求冲突时替换或报告缺口，不能改贴诊断标签凑数。

先用任务结构、来源和组信息定split，再做模型筛选；难度适配只看train/dev。final-test不用于筛题、挑教师、早停、超参或训练轨迹。M1可记录预注册base最终基线；后续只在预注册终点评测冻结模型。harness改变时版本化并重跑可比基线，不能用测试分数挑修法。最终测试已反馈并影响后续扩池时，下一轮需新增留出集或明确声明已适应该benchmark。

### 1.3 每题元数据与真实接入

清单记录：source、revision、license及原始ID/路径/哈希；主次skill；机制（操作/实现/定位修复/恢复/优化）；生成方式（真实issue/程序化/LM/手写）；定位范围、跨文件依赖和交互长度；CPU/RAM/GPU/网络/测试时间画像；split、leakage_group、标注依据及teacher_route。教师按能力路由，不按来源路由。任务描述不混入答案patch、参考解或可能泄漏答案的hints。

“记录已下载”“Harbor可加载”“环境可启动”“参考解/NOP已验证”“模型真实基线”分别计数。每题都必须保留原验证契约，检查参考解通过、NOP负对照失败且不是依赖错误、agent阶段答案/测试隔离、销毁确认。若上游负对照语义不适用，先登记等价的已知错误解对照，不直接豁免。不可用候选在冻结前显式替换；冻结后改题需新版本与可比模型重测。

### 1.4 200题是否足够：以学习信号而不是题数回答

目前不能保证80训练题足以训练4B/8B专家，更不能保证MOPD产生泛化收益。它适合作为首轮工程与学习可行性试验，小池反复采样仍有明显记忆化风险。参数量本身不能决定需要多少题。

case是独立问题；trajectory是一次完整尝试；真正参与loss的assistant token又是另一种数量。举例：80题每题8次是640条尝试，不是640道新题；若每条平均2k assistant token则是128万原始输出token，仅为算术示例，不是实测预算。工具观测、错误轨迹、截断和零方差组会影响有效信号。长任务终局奖励更稀疏、归因更难，不能把长度直接换算成更多监督；SFT专家动作监督和RL终局判分不可按条数等价比较。OPD token级教师监督更密集，也不能补出缺失的任务多样性。

先记录train/dev的有效轨迹率、奖励混合组比例、有效训练token、独立repo/族覆盖、截断率、训练与开发集差距、教师互补性和每条有效轨迹成本。全零先查环境/能力匹配；训练涨而dev不涨优先补新机制/仓库，不只加seed。可在后续另行登记20/40/80嵌套训练子集学习曲线（两域均分、固定dev、明确token/算力对照），本次不自动授权三轮训练。根据这些证据再决定是否超出200题。

官方[SWE-Gym](https://github.com/SWE-Gym/SWE-Gym)报告过不足500条专家交互轨迹微调32B模型获得收益；这说明小规模高质量轨迹可以有价值，不是80题、4B直接RL充分性的证明。来源方法见[SWE-smith](https://github.com/SWE-bench/SWE-smith)。两者于2026-09-08核对，具体导入SHA另在清单冻结。

### Gate M1（替代旧小池验收）

| # | 验收对象 | 必须提供的证据 |
|---|---|---|
| G1 | 200题资产与分布 | 逐题来源、哈希、分类依据、切分与去重报告；目标缺口为未完成 |
| G2 | 可运行与可信判分 | 全池逐题真实环境、参考解与负对照、正常阶段隔离和回收证据；不以mock替代 |
| G3 | 真实模型链路 | 有效多轮工具交互及完整成功轨迹；基础设施错误、模型失败、未知usage分列 |
| G4 | 训练准备 | 从train选择A/B各8的overfit_16，保留train/dev筛选原始结果；final不筛选 |
| G5 | 冻结基线与审计 | 预注册预算内的base四列测试基线、逐题结果/重复次数/不确定性、费用和清理终态 |

数量、链路通过和学习收益是不同声明；完成M1不意味着M2过拟合成功或M4融合有效。扩池资源画像和精确批次预算通过后才运行，旧32题campaign不直接执行200题。


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
- overfit_16仅从train抽取A/B各8题。过拟合前用20条train/dev base轨迹检查工具解析、缺包浪费与截断；修复后重测dev基线。harness版本改变时最终测试的可比基线按1.2统一重测，不能反复查看final-test来指导修复。

### Gate M2

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 框架可用 | SkyRL+Harbor 在 pod 上单卡跑通完整 RL step 循环。**失败分支**：折腾 > 1.5 天仍未通 → 切 verl colocate 方案（agent-loop + 自接 Harbor trial），在 EXPERIMENTS.md 记录切换理由与两框架对比笔记 |
| G2 | TITO | 20/20 轨迹逐位相等 |
| G3 | logprob 对齐 | mean |Δlogprob| < 0.03 |
| G4 | 过拟合成功 | overfit_16 训练 reward ≤30 步达 0.85+，曲线截图入档 |
| G5 | 吞吐画像 | 实测单 step 墙钟时间与瓶颈归属（rollout / verify / 训练三段计时），推算 M3 单臂成本并更新 BUDGET.md；**若单 step > 6 分钟**，先缩任务(轮数/生成预算)再进 M3 |
| G6 | harness 体检 | 修复清单 + 修复前后dev基线，版本与最终测试重测策略见1.2 |

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

- 每臂 **2 seeds**，各 **120 步**，每20步仅在dev上评一次（temperature 0.6 × 2遍）；final-test只评预注册终点，不用于选checkpoint
- 共 6 个 run；按 M2-G5 实测成本执行，超预算则先砍到每臂 1 seed + 加跑关键臂第 2 seed

### 3.3 观测指标（wandb 全程）

**主指标**：dev pass@1学习曲线与预注册终点final-test四能力面板。报告两seed原始值和任务级配对差异，不把两个训练seed当作充分的总体不确定性估计。
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
| G2 | A 臂有效 | 训练更新、dev学习曲线与终点配对结果共同检验是否学习；小样本无法区分时记不确定，不能以完成运行冒充有效学习或消融结论 |
| G3 | 机制指标齐全 | 3.3 全部指标有曲线；选择偏差三件套的统计检验有 p 值 |
| G4 | 报告成文 | M3_REPORT.md 完成，预测核对表逐条判定 |

---

## M4 · Multi-teacher OPD（1.5-2 天，成本 ≈ $60-100）

**科学问题**：两个能力侧重不同的RL专家能否经on-policy多教师蒸馏融进一个学生，保留两域能力并产生组合迁移；相对于直接混合数据训练是否有增益。MOPD不是adapter权重平均，两域也不假定完全不相交。

### 4.1 训练两个教师（复用 M3-A 臂配置）

- Teacher-A只用train-A40题，Teacher-B只用train-B40题；轮数/停止规则在M2吞吐实测后预注册，checkpoint仅凭dev选择。
- 专家互补前提看两者在相应领域的相对优势与逐题差异；允许跨域正迁移，不再要求对方域无提升或退步。若未形成可测互补，报告前提不足，不继续包装成已验证的融合收益。
- 必须训练一个相同底座的joint baseline：直接混合train-A/B训练单模型。它与MOPD比较，避免把“见过两域数据”误判为多教师机制收益。

### 4.2 OPD 学生训练 `opd/train_student.py`

自写脚本，不走 RL 框架（OPD 只需：学生采样 → 教师前向 → 反向KL 更新学生）：

- 同底座base + 冻结adapter_A/B + 可训adapter_student；LoRA热切教师前向是候选实现，必须验证身份、容量和与vLLM同步，不能预先保证某显存容量足够。
- 循环：混合train-A/B prompt → 学生策略完整多轮rollout → 按能力元数据teacher_route路由 → 教师对学生轨迹assistant token给出logits → token级reverse KL更新学生（工具返回mask）→ 同步学生adapter。组合与OOD不参与学生训练。
- 每 rollout 批后学生 adapter 同步回 vLLM
- ~80步、lr 5e-6只作待验证起点；记录两域KL、有效assistant tokens、真实updates、rollout数、教师训练/前向成本与总算力。joint与MOPD匹配或明确报告预算差异，不能只匹配步数。所有正式参数在运行前冻结。

### 4.3 评测矩阵（全部 temperature 0.6 × 3 遍）

| 模型 / final-test | A（30） | B（30） | A×B（20） | OOD（20） |
|---|---|---|---|---|
| Base | 基线 | 基线 | 基线 | 基线 |
| Teacher-A | 本域收益 | 迁移 | 组合 | 域外 |
| Teacher-B | 迁移 | 本域收益 | 组合 | 域外 |
| Joint单模型 | 联合训练 | 联合训练 | 组合 | 域外 |
| MOPD Student | 对Teacher-A保留 | 对Teacher-B保留 | 对两教师及Joint | 对Base保持 |

所有模型使用同版harness/任务、同题资源和解码/重复次数；按来源、组隔离层另列结果。主域A/B宏平均与组合/OOD分开，不能靠混合总分隐藏退化。每域30题，单题即3.33个百分点，因此取消原−3/−5pt固定硬闸；报告逐题配对差异、任务/族聚类不确定性与重复采样波动，三次尝试不算三个独立任务。数据不能区分时结论为不确定；Student超过单个教师也不足以证明组合协同。

### Gate M4

| # | 指标 | 通过标准 |
|---|---|---|
| G1 | 教师前提 | 两域相对优势和互补证据；允许正迁移，无法区分则明确前提不足 |
| G2 | 学生保持与组合 | 双域对对应教师、组合对两教师及Joint的配对结果和不确定性 |
| G3 | 域外变化 | OOD对Base逐题变化，退化/无变化/不确定均如实记录 |
| G4 | 完整与公平 | 5×4矩阵、来源/隔离分层、全部训练与推理成本、固定输入及真实OPD更新证据 |

G2/G3是必交科学结果，不强迫结果为正。实验交付完整与“证实融合有效”分别结论；不能通过放宽阈值来宣称成功。

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
| pod 单 step >6 min | → 先查瓶颈；更改任务/生成预算或训练规模须另行预注册并获批，不自动缩80训练题，也不裁剪冻结最终测试 |
| Modal 所选 GPU 不可用 | → 重新登记替代卡型并验证容量/正确性，不默认硬件等价 |
| 预算逼近 $500 | → 暂停并报告缩减方案；单教师OPD仅可另行批准为管线试验，不能替代当前双教师及5×4矩阵验收 |

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
