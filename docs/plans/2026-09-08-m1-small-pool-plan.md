# M1：小池先行实施计划

> 实施使用subagent-driven-development：逐项TDD，规格审阅后再质量审阅。

**目标**：可复查的双域小任务池与真实agent基线。**架构**：Harbor管理Daytona生命周期，5090本地模型服务驱动工具循环，实例清单与运行证据分离。**栈**：Python3.12、Harbor0.22.0、vLLM0.28.0、Daytona0.210.0。

2026-09-08用户批准执行至M1完成。本文替代总计划旧M1数量、甜区与外部评测硬闸，不授权M2训练。M0已完成，不恢复冷准备检查。

## 目标与边界

交付真实Qwen3-4B多轮agent→Daytona→verifier→回收链路、可信的双域任务、overfit_16和独立冻结评测基线。使用M0固定模型revision、serving锁、私有Python头文件；按内容身份复用，不伪造新源码的prepare记录。Mac仅短时测试/控制；5090一张共享GPU，只管自身进程，准入与24GiB采样停止线沿用M0。Daytona每实例1CPU/1GiB/3GiB、5分钟TTL、无秘密/宿主挂载；先并发1，证据支持后最多8。无RunPod、无Modal新负载。

## 预注册验收

| Gate | 通过所需证据 |
|---|---|
| G1 真实链路 | 模型请求/响应、工具命令/观测、终态reward、provider ID、清理终态按trial串联，至少一条真实成功轨迹；不以oracle替代 |
| G2 任务正确性 | 每个纳入实例独立reference成功、NOP失败、针对性错误输出失败；边界样例、初始化/判分重复一致；所有已知错误修复或隔离 |
| G3 数据身份 | family/domain/instance/seed及文件SHA清单；训练和评测实例无交集；评测在模型筛选前冻结且不按分数选题；明确只是同族新实例，不声称跨族泛化 |
| G4 M2输入 | overfit_16为16个已验证实例、FS/DATA各8；保存真实筛选轨迹，集合含成功和失败；逐任务k/n如实展示，不强制每题混合或中位数甜区 |
| G5 基线及归因 | 冻结eval_clean 16实例（每域8）以temperature=0.6各3次，报告成功/有效分母及全部48次含infra错误分母；同时报告长度耗尽、工具错误、环境错误、token/轮数/耗时/沙箱存活与费用估算。存在未解决系统性错误不晋级，不用重复重试稀释错误率 |

16个评测实例是本次有界起点，不是统计精度保证。外部Terminal-Bench与大池扩展明确延期；M1不宣称外部效度、学习收益或强对抗隔离。k/8表示8次采样成功次数，不叫pass@8。

若16训练实例全部0/8或全部8/8，先用已登记的诊断余量排查真实输入、工具和判分；仍无混合证据则M1未完成，不能无限调题或修改评测。训练题难度修订最多一版且只用余下诊断额度验证；超出现有范围再同步。正常阶段隔离必须在真实agent开始和结束时执行read/list探针，确认/tests、/solution不可访问且镜像内无答案；记录verifier上传发生在agent结束之后。这不是恶意后台进程跨阶段隔离证明。

## 任务与运行配置

先构建4个模板族：FS日志聚合、FS目录清单；DATA去重聚合CSV、DATA嵌套JSON汇总。每族4训练候选+4冻结评测，共32实例。各族包含明确的过滤、去重、排序和边界规则；stdlib可解，不需要下载包。Harbor真实格式为instruction.md/task.toml/environment/solution/tests。私有verifier检查实际展示的输入，不暗换数据；oracle实现与预期值构造采用独立算法。模型只能收到任务描述和工具观测，不能读取本机任务源码或私有答案。

固定model为Qwen/Qwen3-4B revision 1cfa9a7208912126459214e8b04321603b3df60c；enable_thinking=false，最多10次模型调用，总输出4096token，上下文16384，单条工具命令≤20秒、观测有界且记录截断；agent≤180秒，verifier≤30秒。筛选temperature=1.0，评测0.6，固定登记seed。无自动trial重试。首批4候选各1次，再每候选累计8次；不能因结果不理想删掉失败记录。0/8、8/8不证明不可能/必然；必要的难度修订另版本登记，只调整训练候选，原评测保持不变。

## 有界预算与停止

首pilot最多8个模型trial（含诊断余量），CPU费用预留$1；确认链路后筛选16×8及评测16×3，总计176个模型trial，另32×2正负云对照、诊断至多16个，总创建请求上限264。2026-09-08重新查[官方价目](https://www.daytona.io/pricing)：vCPU $0.0504/小时、内存$0.0162/GiB小时、存储$0.000108/GiB小时。保守计3GiB全部收费，264×300秒的运行资源估计$1.4723；旧M0单实例估算不沿用。另预留构建/存储余量，总规划$10，并非平台账单硬限额；若预计超过$10或发现收费口径未知导致无法有界估计，先暂停扩量。credits余额未验证，不能写成免费。

每次5090固定recipe工作≤3600秒、控制器≤3660秒，批间核对自身清理与共享资源。实现审阅纠正原“4批”的算术：pilot4一批、controls64一批、screen128两批、eval48一批，至少5批；另保留第二pilot4诊断，最多6批，总创建264/$10预留不增加。controls不启动GPU，其余模式累计GPU时间保守预留不超过21600秒，实际按运行记录统计；这不是额外GPU已获逐plan批准的声明。首云运行前须展示精确计划，按HOME-5090的逐plan人工门控执行。

Lab持久campaign账本按冻结pool/agent身份累计预留与实际尝试、pilot≤8、创建≤264、最多6批；正式重复采样不能静默补位，上一run未收尾或清理不确定会锁住后续创建。pilot须有4条有效完整轨迹、双端清理确认且至少1条模型成功；首4全0只允许第二4诊断，仍无成功不扩量。screen/eval前实际消费32实例NOP0/oracle1控制证据。实际API故障先诊断，连续3个创建失败暂停本批且清理已知对象，不盲重派。

控制进程显式读取0600私有Daytona配置，密钥不进入vLLM、模型prompt、沙箱、产物或Git。原始证据在远端Lab artifacts/m1/home5090/<run-id>，Mac只取小摘要。服务必须在finally回收自身进程组；所有云对象按本批身份删除并列表核对，清理不确定则停止新建。新trial必须额外预留至少150秒覆盖Harbor取消等待与云/GPU清理，不得用满3600秒才开始回收。

## 实施清单

- [x] 隔离worktree、原43项测试通过。
- [x] T1：lab_runtime/task_pool.py、tests/test_task_pool.py：先写失败测试，再实现确定性任务生成、独立oracle/反例、清单与Harbor格式；输出只到指定空目录，拒绝覆盖；规格与质量审阅通过。
- [x] T2：lab_runtime/m1_agent.py、tests/test_m1_agent.py：真实Harbor自定义agent，严格工具schema、预算和完整轨迹，错误分层；10项mock接口测试与双阶段审阅通过，不能冒充G1。
- [ ] T3：lab_runtime/m1_run.py、lab_runtime/m1_campaign.py、scripts/m1_run.py、configs/m1.json及测试：复用共享受控server安全函数，持久批次、前置证据账本、预算/清理/聚合；新增固定recipe经服务器负责人登记。先只读资源检查，不能任意SSH后台启动。
- [x] T4：离线32实例所有正负/变异测试，冻结清单及SHA；独立审阅verifier与split通过，清单configs/m1-pool-manifest.json SHA256为66a844924f96d3de135e8e3040792a4abf084b37e3c21234f7834132c7b830fc。
- [ ] T5：真实pilot、云正负对照、筛选和冻结评测；逐批记录实际费用估算/错误/清理。若资源不足保留进度报告用户，不停止他人。
- [ ] T6：lab_runtime/m1_report.py/tests/test_m1_report.py汇总明确run清单，核对身份、矩阵和全部分母（8项离线测试及双阶段审阅已通过）；真实基线和overfit_16仍待运行。逐Gate证据复核；更新README、总计划、EXPERIMENTS、BUDGET；审阅、测试、合并并push独立仓库。

本地命令：从本worktree用Lab主目录.venv/bin/python -m unittest discover -s tests -v。远端命令仅hlab sync/plan/submit/status/logs；精确recipe与源码SHA在T3落定后登记，不预编造run ID。M1完成后停止，不自动启动M2。
