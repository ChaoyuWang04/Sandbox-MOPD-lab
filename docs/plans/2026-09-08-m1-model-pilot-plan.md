# M1 小规模真实模型 Pilot 实施计划

> **For agentic workers:** Follow this plan task-by-task. Use test-driven development for behavior changes and preserve the approved scope.

**Goal:** 用当前Qwen3-4B在四种不同自建代码修复任务上完成一次有界真实链路，确认模型响应、工具执行、Daytona判分和回收可以串联，并为M2提供观测基线。

**Architecture:** Mac只完成代码、测试、Git和hlab控制；5090固定recipe启动并独占本次vLLM进程，逐个调用Daytona沙箱。复用现有模型、环境、M1Agent、Harbor trial与清理逻辑，新campaign不依赖或修改旧32题campaign。

**Tech Stack:** Python 3.12、Harbor 0.22.0、Daytona SDK 0.210.0、Qwen3-4B固定revision、vLLM固定home5090环境、hlab/systemd-user。

---

## 已批准范围与终态

- 用户已明确批准持续执行到小规模真实模型pilot完成；这包括本次一个5090 GPU plan和最多4个串行Daytona trial，不包括RL参数更新、M2训练、外部全池基线或故障注入。
- 固定任务：self-v2-config_precedence-00、self-v2-resource_lifetime-00、self-v2-async_dependencies-00、self-v2-atomic_replace-00。全部由跟踪的规则生成器在远端worktree内重建，无需传输ignored题目资产；每题trial ID、seed与temperature同时在唯一配置冻结。
- 模型与运行预算沿用已验M0身份：Qwen/Qwen3-4B revision 1cfa9a7208912126459214e8b04321603b3df60c、16k上下文、总输出4k token、最多10轮；单trial agent 180秒、整批5090工作3600秒、hlab 3660秒。Daytona每次1 vCPU/1 GiB/3 GiB、无GPU、禁止出站、TTL5分钟，串行1。
- 费用只登记四个沙箱实际存活估算；按每个跑满300秒的公开费率上界约$0.0222，另保留$0.10小额余量。用户报告此前credits显示消耗$0.03，该值不代替平台明细。
- 每个trial都保存模型请求/响应、工具观测、usage完整性、reward或invalid原因、sandbox ID和清理终态。每次清理必须同时按本批精确labels和所有已知sandbox ID确认不存在；仅凭列表暂时为空不能证明已知对象回收。reward=0是有效模型结果；环境、模型HTTP、测试未执行、缺reward或清理不明不是模型0分。
- “pilot执行完成”要求四个登记尝试均进入终态并分类，所有已知对象回收、GPU自有进程组退出、模型文件复核不变。至少一条实际模型响应→shell工具观测→有效reward链路，才能称“真实agent链路通过”；不设成功率/reward高低门槛。若没有该链路，pilot仍保存为失败证据，不冒充通过。

## 文件职责

- configs/m1-pilot-v2.json：唯一任务、模型、资源、时限和创建上限登记。
- lab_runtime/m1_pilot_v2.py：构建self48、核对固定manifest/任务字节、创建新campaign、向trial循环显式注入新ledger的attempt登记、调用现有模型服务与trial循环、生成分层汇总；不得让共享循环回写旧campaign硬编码路径。清理复用或提取controls_v2的“labels+已知ID”实现，清理不确定就停止后续创建。
- lab_runtime/daytona_pilot.py：只为本次pilot在最终provider创建边界固定5分钟TTL、私有、禁出站和单次创建，并把creating/created/rejected/uncertain事件同步写入新ledger。
- scripts/m1_run.py：现有hlab recipe的固定薄入口，切换到v2 pilot；旧commit仍可复现旧入口。
- tests/test_m1_pilot_v2.py：配置漂移、任务选择、持久准入、reward/invalid分类及最终汇总。
- docs/EXPERIMENTS.md、docs/BUDGET.md、docs/plans/2026-09-08-m1-200-case-plan.md、README.md：真实结果、费用、M1状态与下一步。

## Task 1：更新M2设计与pilot预注册

1. 就地改写总计划M2：固定阈值改为观测项；拆成M2-A工程链路和M2-B学习可行性；删除1.5天自动换框架和6分钟裁题门槛。
2. 新增本计划和固定pilot配置；配置需绑定任务生成manifest身份与上述资源。
3. 验证文档无矛盾、JSON可解析、Git diff只含Lab路径；提交并推送。

## Task 2：TDD实现v2 pilot入口

1. 先写失败测试：拒绝配置漂移、重复/错任务、未冻结或重复trial ID/seed、旧campaign复用、创建前未持久化、旧campaign字节发生变化、known-ID仍存在却判清理完成、累计第5次provider创建、无效reward混入有效分母、没有工具链却误报通过。
2. 运行Lab Python -m unittest tests.test_m1_pilot_v2，确认因入口缺失/行为缺失而红。
3. 最小实现m1_pilot_v2.py和薄入口切换；复用M1Agent、managed_server、Trial配置与现有清理，不复制判分或provider SDK。若复用run_batch，必须把attempt登记改为显式ledger路径/回调，旧调用保持原行为并有回归测试。
4. 运行focused、全套测试和git diff --check；审阅后提交、推送。

## Task 3：生成并提交唯一真实计划

1. hlab doctor/projects/recipes/runs复核无活动本Lab run；不处理他人进程。
2. 同步Task 2已验证commit，生成m1-small-pool不可变plan，核对argv、commit、3660秒、磁盘、recipe和registry digest。该recipe登记`exclusive_gpu=false`，因此提交前时点检查不等于资源预留；运行器仍须在启动模型前重新检查至少26 GiB空闲，只停止自身进程组。
3. 使用本轮用户批准原意作为真实批准来源，但approval note只准确描述本次“四题真实Qwen3-4B pilot、无RL更新”；plan只提交一次。
4. 保存plan/run/unit；按run ID等待。网络断线不重复提交。

## Task 4：验收、诊断与收尾

1. 读取hlab status/logs/artifacts和固定summary；核对四个尝试、每条failure category、有效reward、tool calls、usage、sandbox回收与GPU进程组终态。
2. 若代码/接口错误，先写回归测试并保存诊断；旧run保留。本次已批准范围累计最多4次provider创建，已创建的失败尝试不补采样、不为提高reward重跑；若修复后会产生第5次创建，则停止并单独报告超界，而不是自行扩量。
3. 只读使用精确labels/IDs确认没有本批Daytona对象；不删除或停止别人对象。
4. 更新权威文档，明确“pilot执行完成”和“链路是否通过”是两项结论；全套测试、提交并推送。M2-A仍需另行实现真实RL更新。

## 停止边界

- 未知创建/清理、GPU准入不足、现有非本Lab GPU负载冲突或模型/环境身份漂移：不启动新的trial，保存状态；不杀别人的进程。
- 累计预计超过4次provider创建、$0.10 Daytona余量、3660秒5090 plan，或需要RL更新/新模型/新平台：超出本计划。无provider创建的本地/远端前置失败可修复后生成新plan，但每个plan仍不可重复提交。
- reward走势只记录，不据此修改题、阈值或追加尝试。

## 执行结果（2026-09-08）

- Task 1-4已完成。计划提交`5067c79`、实现提交`02a444596f11d0e0699d10024238e9a00d953d0c`均已push；focused 13项、全套231项离线测试通过（2项外部ignored证据skip），独立复核无剩余问题。
- hlab plan `plan-sandbox-rl-mopd-20260908t145623z-64c1f995`只提交一次；run `run-sandbox-rl-mopd-20260908t145649z-fd9f7a29` succeeded/exit0。四题均为有效reward且usage完整：reward 0/1/0/0；合计18次模型响应、15次真实shell执行，真实agent链路通过。
- 四个known sandbox ID逐个GET和精确labels list均收敛为空，结束后的独立只读campaign label list为`[]`；自有GPU进程组退出，模型内容复验不变。Daytona生命周期估算合计$0.003914，账单未核实。
- summary SHA256 `b113819c97bc739ed9b93a39014c93d04e16b8184dbcd84af03c6329e26ae613`，远端路径见EXPERIMENTS。`pilot_execution_complete=true`、`real_agent_chain_passed=true`、`m2_a_passed=false`；本计划完成不等于M1整体或M2-A完成。
