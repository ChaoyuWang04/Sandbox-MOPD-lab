# M0 环境实施计划

## 当前批准范围

2026-09-07 用户批准更正目录拼写并执行 M0-A。原目录只有总计划；更名为 sandbox-rl-MOPD-lab，保持文件内容，之后就地补充当前执行边界。只读查询允许；安装、下载模型、Pod 分配和运行 Gate 留给后续批次。

## M0-A 顺序与完成判据

1. 更名并建立 README、忽略规则、配置模板、实验/预算/Harbor 记录和预留目录。成功判据：旧路径不存在，新路径唯一，文件均在 Lab 内。
2. 清点 Mac 与 5090。保存实际命令、版本、内存/显存、磁盘、Docker 可用性；命令找不到时核对常见绝对路径。成功判据：每项有读数或明确缺口，不将清点当 Gate。
3. 核对上游安装说明、Harbor 格式和 SkyRL 接口，记录来源与查询日期。将公开最新版、上游约束、实际安装版分别记录；未解锁与实跑前不生成假锁文件。
4. 检查候选云平台能否容纳训练、推理和真正 Docker 沙箱。平台未选定时不编造 Pod 硬件清点或单价；有直接平台限制则记录阻断。
5. 检查忽略规则、文档本地链接与目录边界，落机器汇总。M0-A 可以完成但必须保留完整 M0 未通过状态。

## M0-B（执行中）

### 网络实测后续装（2026-09-07用户已授权自适应安装）

用户明确要求先比较实际网络/镜像，再调整限制继续安装，不重复询问安装参数。该授权仅用于本Lab安装恢复，不修改共享DNS/VPN/驱动、不清缓存、不启动GPU。安装预算与G4冷启动验收分离：最多7200秒工作预算、控制器7260秒；G4仍只认cold_start且完整准备≤1200秒，缓存恢复只能算环境就绪。

方案：阿里PyPI为本次短测最快候选，HF官方与镜像同落AWS CDN，保留HF官方。原uv.lock保持不变；`uv export --frozen --offline --no-dev --no-emit-project --format requirements-txt`将带hash要求写入唯一run目录，再对原锁ID专属venv执行`uv pip sync --require-hashes --no-build --default-index https://mirrors.aliyun.com/pypi/simple/`，随后`uv pip check`。镜像仅负责传输，不能改变允许的版本或hash；不用`uv sync --frozen`加index环境变量假装已改其锁内下载URL。UV读取120秒、并发4；HF读取120秒、元数据30秒、2文件并发，禁用Xet以使用本次实测的HTTP路径（不是Xet故障结论）。可复用已登记缓存，不删失败见证。

- [x] tests/test_home5090.py先RED：下载环境/模型worker契约、安装7200与G4判据1200分离；tests/test_home5090_run.py先RED：冻结export、hash约束sync、目标venv与依赖检查。
- [x] 修改lab_runtime/home5090.py与home5090_run.py最小实现；保持无参脚本、模型revision、锁、目录和GPU参数不变。prepare状态记录下载策略、导出requirements hash、实际耗时。
- [x] 用Lab .venv运行全套40项tests、245包/511hash离线冻结export与两份锁检查、diff检查；独立规格及代码review无阻断。测速摘要放artifacts/m0/network-source-probe.json，结论更新EXPERIMENTS/BUDGET。
- [x] 共享设施负责人调整prepare超时并部署；续装唯一run成功，245个版本与锁一致、13个模型文件hash复验通过、进程已退出。精确身份和34分53秒缓存恢复结果见EXPERIMENTS，不重投或重装。

最新状态：G2授权8并发降级与G3官方NOP/oracle正负对照、正常阶段隔离均有真实结果，全部沙箱独立确认回收。5090受控环境及模型已续装成功并复验，未启动GPU；G1未运行，G4冷启动20分钟未通过。精确身份与证据只维护在EXPERIMENTS，不触碰他人进程、不自动重投。

下一步GPU计划已生成但未提交：`plan-sandbox-rl-mopd-20260907t150221z-d7c07fa5`，固定Lab SHA `cf91c2395f32ead8fbfa8f07f85bf8e12ca697ed`，recipe `qwen3-4b-m0-probe`，argv `/usr/bin/python3 scripts/qwen3_4b_m0_probe.py`。1张RTX5090 GPU0，工作预算600秒/控制器660秒，仅loopback18741；12,288输入+4,096实际输出构成16k上下文，输出<90秒并验证工具调用。显存比例0.70，按秒采样本次进程组>24GiB停止自身（非硬限额）；准入GPU空闲≥26GiB、MemAvailable≥18GiB、磁盘≥80GiB，内存16GiB仅规划值。日志与缓存仅Lab data根，不新增云资源、不停他人进程。按HOME-5090手册等待用户对首次GPU计划明确批准，不复用prepare批准说明。

Mac本地容器trial已因用户内存边界取消；当前只运行短时云控制进程。Modal历史失败不阻断已验证的Daytona路径，两家证据不能混用。三端资源唯一预算见BUDGET，G1/G4准备与共享设施接入仍待完成。

2026-09-07 用户进一步批准继续下一步，并授权与 interviewprep 的“服务器改造”任务直接协调通用设施。Lab 当前先准备 Python 3.12、固定 Harbor 0.22.0 及 CLI/官方示例核对；使用 Lab 自有 .venv/cache，原始证据进入 artifacts/m0。服务器共享设施由对方核对，不在本 Lab 实现控制器特化功能。此步骤不启动 GPU、批量筛选或付费沙箱。

用户已要求优先按 HOME-5090 手册调查现有服务器，排除 RunPod，允许 Modal/Daytona 沙箱。调查与共享设施核对已完成，详情见 HARBOR_NOTES。Mac Harbor 环境及依赖锁已准备，远端接入与容器后端尚未就绪；不以清除 OASIS 失败记录或安装主机 Docker 为默认前置。第一项有运行意义的验证是官方 Harbor 示例 trial：环境启动 → agent/参考解 → verifier → teardown，保存完整轨迹、奖励文件和资源回收结果。先用参考解与故意错误的终态验证判分，再测模型。

| Gate | 原定判据 | 需要的证据 | 当前 |
|---|---|---|---|
| M0-G1 | 5090 Qwen3-4B，16k 上下文，单请求 4k 生成 <90s | 模型 revision、原始请求、实际 token 数、耗时及 tool_calls 内容 | 未运行 |
| M0-G2 | 原16并发<60s；用户授权失败后8并发 | 单容器状态、总墙钟、清理后容器列表 | 16失败；8降级PASS，4.97秒、全部回收 |
| M0-G3 | 官方 Harbor 示例完整 trial | 固定源码/版本、指令、轨迹、verifier 奖励和 teardown | NOP=0、oracle=1、阶段隔离和回收PASS；非强对抗隔离证明 |
| M0-G4 | 选定执行环境从零准备 ≤20min | 已选平台与镜像、依赖锁、计时、账单及停止确认 | 冷prepare失败；缓存续装34分53秒成功，不算冷启动PASS |

G1 必须区分 4k 输出上限与实际生成满 4k，避免短回答误过速度门槛。G2 先从低并发测资源再到 16；不得影响正在运行的 harness-lab。G3 必须证明 agent 看不到私有判分材料，文档格式存在不能替代隔离测试。

## 实施前需要解决的计划问题

- 原计划允许“进程级隔离”作为 fallback，却又以真实容器故障为 M3 测量对象；降级会改变实验含义，不能静默采用。
- 版本对应的 Harbor 格式以 HARBOR_NOTES 为准，原 prompt.md/setup.sh/verify.py 仅为职责示意。
- M1 的 k/8 是八次采样成功比例，不能和“至少一次成功”的 pass@8 指标混用；后续筛选以 k ∈ [1,6] 保持原门槛。
- M3 的“6 run 必须齐全”和缩 seed 预案冲突；进入 M3 前冻结预算与完整性标准。注入概率的计量单位、Var_all=0、过滤后不足两条、被杀轨迹的反事实成功如何测量也必须预注册。
- M4 热切 adapter 的前向/梯度归属、KL 方向与内存需求均需实测；原文“显存从容”不是容量证据。

上述 M1–M4 项只登记风险，不在 M0-A 改算法或验收数值。

## hlab 接入（已注册，prepare续装成功；GPU待批准）

### 固定入口实施批次（用户已批准继续完整M0）

共享设施负责人已回报部署52b327d398b461d0e36b617c19ebf5c1c5c12b1c，通用设施不再阻断；本Lab仍须补真实入口，旧部署调查是历史值。以下是当前施工计划，不是运行通过声明。

- [x] 在environments/home5090/固定独立Linux/Python3.12 serving依赖，uv生成真实锁，Mac不安装GPU包。M0环境包括vLLM+Harbor/Daytona；SkyRL训练栈属于M2另行冻结。
- [x] tests/test_home5090.py先验证固定路径/参数、满4096 token与90秒边界、工具调用内容和不满足时失败；再实现lab_runtime/home5090.py共享契约及两个scripts固定入口。
- [x] prepare入口使用/usr/bin/python3、/home/samwang/.local/bin/uv，所有环境/模型/cache/artifacts固定在/home/samwang/data/sandbox-rl-MOPD-lab；source_repo只读。环境按锁SHA键控，frozen/no-build sync，固定模型revision，幂等目录、互斥锁、原子状态文件、最长1200秒。
- [x] GPU入口固定Qwen3-4B BF16、16384上下文、4096实际输出、loopback18741；显存规划0.70，1秒采样本次进程组>24GiB停止，不宣称硬限额；只读资源不足即拒绝不杀人。只终止本次Popen精确进程组。最长600秒，耗时/输出/失败保存独立证据及固定latest状态索引。上述为代码已实现，尚未目标实跑。
- [x] 本地离线测试、独立评审、提交推送并完成控制器注册；prepare续装已成功，GPU计划已生成未提交，见本页最新状态。

机器可读结果固定为data根下artifacts/m0/home5090/prepare-latest.json和probe-latest.json；每次原始日志/请求/结果保存在同目录下唯一run目录。G4从零计时与缓存复用明确区分。禁止自由shell字符串、用户可变模型/预算参数或调用父项目代码。注册时控制器先不可覆盖创建data根供disk_path准入；recipe超时1260/660秒分别覆盖工作预算和清理余量。

本批本地36项tests、Mac/Linux两份锁离线检查通过，规格与代码质量独立review均无剩余阻断。最终命令为`/usr/bin/python3 scripts/prepare_environment.py`与`/usr/bin/python3 scripts/qwen3_4b_m0_probe.py`；工作目录为detached worktree根。当前锁ID=5431fdaf92e6，持久解释器为data根下envs/m0-serving-5431fdaf92e6/bin/python，probe实际直接读取data根下固定revision模型（没有宣称systemd只读binding）。成功前后模型hash核对；失败可记录not_checked。清理自身monitor/进程组最多另约13秒，控制器留60秒余量。prepare与probe均登记risk=training并逐plan要求用户批准。

建议先准备两个有界入口：CPU 环境/Harbor oracle 验证、Qwen3-4B 单请求验证。每个入口需固定 argv、Lab Python 绝对路径、工作目录、cache/data/artifacts 路径、超时、风险类与退出检查。长时任务筛选单独登记 training 类 recipe，不能用 control-smoke 替代。

接入需要在控制器开发副本中注册新的 Lab project ID，完成测试、独立评审和同 SHA 部署；禁止改当前安装副本。Lab 已独立 Git 管理，用户已授权提交推送；hlab 接入需提供该仓库的完整 commit SHA。控制器管理的 mirror/worktree/run 元数据属于工具目录例外，业务资产仍归 Lab。

注册批次控制器负责人回报两端为fc3b7282cb05c04ed32127242ac54839fb7c6b2c，主任务已用doctor/projects/recipes核对两个固定入口。首次真实运行暴露runs索引不接受finished_at=null，已交负责人处理；精确status/logs可用，不影响按run-id恢复。不得清除OASIS或本Lab失败状态。GPU运行仍必须在prepare成功后展示精确plan并取得用户approval note。
