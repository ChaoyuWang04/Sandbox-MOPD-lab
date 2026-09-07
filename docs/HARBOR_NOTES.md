# Harbor 与依赖核对

核对日期：2026-09-07。Daytona8并发授权降级和官方Harbor NOP/oracle完整trial已验证，详情只维护在EXPERIMENTS。G1/G4未完成。上游main与网页会变化，不从历史调查直接继承运行身份。

## 任务格式与生命周期

[Harbor Task Structure](https://www.harborframework.com/docs/tasks) 使用 `instruction.md`、`task.toml`、`environment/`，并支持 `solution/` 和 `tests/`。Docker 环境可使用 Dockerfile、Compose 或配置中的预构建镜像。原总计划的 prompt.md/setup.sh/verify.py 只代表指令、初始化、判分职责，不能当作实际文件名约定。

[官方教程](https://www.harborframework.com/docs/tasks/task-tutorial) 提供任务环境启动示例。当前采用固定commit的hello-world，任务来源与差异见tasks/m0-hello-world/PROVENANCE.md；本地固定任务路径避免Harbor默认写HOME缓存。

普通agent阶段的私有判分脚本必须不可见；oracle是读取参考解的特殊正对照。NOP实际START/END均验证/tests和/solution不存在。Harbor随后将tests上传同一环境，这是阶段隔离，不是对恶意后台进程的强安全保证；宿主机目录、秘密和Docker socket始终不得暴露。奖励、判分轨迹和回收证据见EXPERIMENTS。

Lab适配器 lab_runtime/daytona.py 使用0.22.0最终_create_sandbox边界补5分钟TTL，并用上游tenacity retry_with关闭创建重试；不修改已安装包。CPU/memory策略为Daytona支持的request，实际cgroup另测；不能沿用Modal的guarantee。单元测试覆盖真实客户端调用边界的TTL和失败只调用一次。

## 依赖与真实接口

| 项目 | 本次查到的事实 | 冻结状态 |
|---|---|---|
| Python | Mac 已有 3.12.13；5090 系统 3.12.3 | Mac Lab .venv 为 3.12.13 |
| Harbor | [PyPI JSON](https://pypi.org/pypi/harbor/json) 返回 0.22.0，Python >=3.12 | Mac 已安装 0.22.0；uv.lock 离线一致性检查通过 |
| vLLM | [PyPI JSON](https://pypi.org/pypi/vllm/json) 返回 0.28.0，Python >=3.10,<3.15 | 已确认公开版本，未与 SkyRL 解锁 |
| SkyRL | [安装说明](https://docs.skyrl.ai/docs/getting-started/installation)：CUDA 13.0、驱动 r580+，推荐 Ray 2.57.0/Python 3.12；有对应 FSDP 镜像 | 镜像未拉取、digest 未固定 |
| SkyRL 依赖文件 | [main pyproject](https://github.com/NovaSky-AI/SkyRL/blob/main/pyproject.toml) 的 raw 请求仅收 4077/21017 bytes 后超时；片段含 Ray 2.57.0、vLLM 0.28.0、Torch 2.11.0 条目 | 不完整，不能据此断定组合兼容；重取完整文件后按 extra 检查 |
| Mercor recipe | [官方仓库](https://github.com/Mercor-Intelligence/ApexAgents-SkyRL-Recipe) 可查到；raw pyproject 请求连接超时 | commit、依赖约束待取回 |
| Qwen | [官方模型页](https://huggingface.co/Qwen/Qwen3-4B) 与模型API | 未取权重；本轮API revision=1cfa9a7208912126459214e8b04321603b3df60c，未部署 |

依赖“最新版”和“能一起安装”是两项检查。下一阶段需取回完整上游配置及锁文件，确定兼容的 extra、模型模板与 LoRA 路径，再生成 Lab 自己的环境。父项目的 Torch/verl 锁不得直接复制。

[SkyRL Agent Integration](https://docs.skyrl.ai/docs/tutorials/agent-integration) 明确自定义 Generator 实现 `generate(input_batch)` 并返回 token、loss mask、奖励等；TIS 还需原始 rollout logprob。端点从引擎客户端获取。采用 `/completions` 的 TITO 路径时要自己解析工具调用，不能假定 `/chat/completions` 返回的 tool_calls 能直接进入同一训练实现。M0 的 HTTP 能力探针与 M2 的 TITO 对账要分别验。

## 平台约束

用户于 2026-09-07 明确排除 RunPod，优先按 HOME-5090 手册使用现有服务器；Mac 不跑长期服务。用户最新选择是 Daytona 优先用于大规模沙箱，Modal 做有界对照；credits 余额/有效期尚未核实。真实 smoke 状态见 EXPERIMENTS，不能将官方支持等同实际接通。

| 路径 | 已确认能力 | 本项目代价/缺口 | 建议 |
|---|---|---|---|
| home-5090 vLLM + 本机 Docker 沙箱 | Linux GPU 可见；Harbor 有 Docker provider | Docker 未检出，且当前不要求共享设施为本实验安装 | 非当前首选 |
| home-5090 vLLM + Modal Sandbox | Harbor 原生 Modal 环境，平台支持 exec、文件操作、终止 | Mac 已认证与创建，但 exec/清理 RPC 有失败；服务器 API 通路未验 | 小规模对照、备用 |
| home-5090 vLLM + Daytona | SDK、8并发、Harbor正负对照与cgroup/回收已验证 | 5090模型与从零部署未完成；16原门槛失败 | CPU沙箱首选，按批准采用8并发 |
| Modal GPU + Modal Sandbox | GPU 与 Sandbox 分开由同一平台管理 | 需新 Lab 镜像/Volume、完整依赖、容量和端到端 trial 认证 | M2 训练候选，避免要求家庭服务器向公网开放工具端口 |

[Harbor Core Concepts](https://www.harborframework.com/docs/core-concepts) 明确 Modal、Daytona 等环境共用 BaseEnvironment。[Getting Started](https://www.harborframework.com/docs/getting-started) 给出 `--env daytona` 的调用方式。不能把 provider 名存在写成当前项目已经接通。

[Harbor Modal 实现 main](https://github.com/harbor-framework/harbor/blob/main/src/harbor/environments/modal.py) 可看到镜像构造、沙箱清理和 app/timeout 配置；[Modal Sandbox API](https://modal.com/docs/guide/sandboxes) 支持 exec、退出码与 terminate。使用原生 Sandbox，无须在训练容器里自行运行 dockerd。检查的是可变 main；Harbor 0.22.0 对应源码未完整取回，准确 kwargs 必须随安装版重新核对。

[Daytona Sandboxes](https://www.daytona.io/docs/en/sandboxes/) 支持创建、停止与 ephemeral 生命周期；自动停止不应替代显式清理。试验前先核对当前 Harbor 适配器如何处理 timeout、上传/下载与删除，不擅自继承 SDK 默认行为。

## SkyRL、Harbor 与平台分工

[SkyRL Agent Integration](https://docs.skyrl.ai/docs/tutorials/agent-integration) 描述训练侧 Generator 契约：收集轨迹 token、mask、reward 等交给训练。SkyRL 是 RL 训练框架，不是云平台。Harbor 是任务执行/评测框架，统一任务、agent、环境启动、verifier 和结果；[Harbor Agents](https://www.harborframework.com/docs/agents) 支持模型/agent 适配。两者不是开箱即可完成本项目全部算法，TITO/logprob/LoRA/OPD 接缝仍需 M2/M4 验证。

Modal 与 Daytona 都提供远端沙箱，不替代 SkyRL 或 Harbor。Modal 同时有云函数/GPU 与 Sandbox，适合把 GPU 计算和工具沙箱分别部署；Daytona 面向 agent 计算机，提供文件、进程、Git、快照和生命周期能力，也有 GPU/VM 产品，不能简单说 Daytona 不支持 GPU。本项目只评估 Daytona CPU 沙箱。

[Modal VM Sandbox](https://modal.com/docs/guide/vm-sandboxes) 说明默认 gVisor 与完整 VM 的区别；不能要求默认隔离环境暴露宿主机 cgroup，先前 cgroup 路径缺失不是限额无效的证明。改用 VM 是额外选型，不为读到一个文件擅自切换。

[Daytona Sandboxes](https://www.daytona.io/docs/en/sandboxes/) 提供 wall-clock TTL、auto-stop、ephemeral/auto-delete；停止不等于删除。[Daytona Limits](https://www.daytona.io/docs/en/limits/)本轮页面的两张Tier1表内存分别写10/20GiB，CPU均10vCPU、磁盘30GiB；不能据此确定账户额度，实际16/8结果见EXPERIMENTS。Modal physical core与Daytona vCPU不等价。

## home-5090 控制层实查

最新复核：Mac与服务器git HEAD都返回52b327d398b461d0e36b617c19ebf5c1c5c12b1c，hlab --help包含runs。负责人回报通用健康检查/资源准入升级已上线；Lab仍待自己的两个真实入口与project recipe注册。以下9e91671清点是升级前历史证据，不作为当前能力限制。

手册及其链接的 agent-runbook、architecture、README 已完整读取。只读命令：`hlab --help`、`hlab doctor`、`hlab projects`、`hlab recipes control`、`hlab recipes syncopate`；必要补充使用手册允许的原始 SSH 只读诊断。

- Mac 和服务器 HEAD 均为 `9e91671e226441460d06f90d4f6e73ca6d4d21ba`。未对安装目录作修改。
- 已部署 CLI 不支持 `runs`；没有尝试调用不存在的子命令，也没有安装手册提到的开发版。
- `ssh.status=ok`，`linger=yes`，但 `user_systemd=degraded`。唯一列出的失败 unit 是 `oasis-s7-modal-bundle-20260906.service`，状态 failed、Result=exit-code、ExecMainStatus=1。这只解释 degraded 来源，不推断 OASIS 实验状态，也没有 reset-failed/restart。
- 注册表只有 control/oasis/syncopate；syncopate 的 control-smoke 仅打印并睡眠 8 秒，不能用于新 Lab。Lab 没有项目或真实 recipe，按手册停在发现/接入设计阶段。
- 此次 doctor 读到显存空闲 31,437/32,607 MiB、磁盘空闲 771,110,895,616 bytes；计算进程查询只列 PID 2147 `/usr/bin/sunshine`（513 MiB），未处理该进程。不同查询时刻的显存数字不要求相等。
- rootless Docker 前置：user namespace 开关为 1，max_user_namespaces=125863；samwang 的 subuid/subgid 各 65,536。newuidmap/newgidmap/dockerd/podman 未在 PATH 检出，仍需确认或补齐 uidmap。参考 [Docker rootless 文档](https://docs.docker.com/engine/security/rootless/)。尚未检查所有 AppArmor/cgroup 限制，不能称 rootless 就绪。
- 网络探针：5090 对 `https://api.modal.com` 的 HEAD 返回 503；对 `https://app.daytona.io` 返回 200。前者说明收到 HTTP 响应，不证明 Modal SDK 不可用；后者只是网页可达，不证明认证 API 成功。没有输出密钥或访问账户资源。

## 调用与存储方案

建议让 Lab 自有 agent loop 和 vLLM 同处 5090，模型只访问 loopback；loop 调用 Docker 或通过 HTTPS SDK 操作云沙箱。这是针对自写 recipe/agent.py 的设计推论，需要 M0 验证；它不要求云沙箱主动访问家庭 vLLM 端口。若以后选择把整个 agent 安装进云沙箱的路径，则需另验模型可达性，不能沿用此结论。

转到 Modal GPU 训练时，由 Mac 直接控制 Modal。避免把 5090 变成云训练的中转控制机；home-5090 只承担实际本地计算/存储工作，符合手册第 2 节。

Lab 模型、环境、缓存、数据和业务产物仍位于服务器独立 Lab 根下。hlab 自动管理的 mirror/worktree/run manifest 和 systemd 元数据位于手册规定的控制器目录，是采用 hlab 必须明确的系统例外；使用独立 Lab project ID 分类，不共用 syncopate ID。控制器 registry 接入必须在其开发副本完成并评审部署，不能修改安装副本或只在 Lab 放个 TOML 就声称已注册。本轮仅把接入需求存于 Lab 文档。

Lab已独立Git管理；hlab只同步commit，正式接入使用完整已提交身份。uv用绝对路径，缓存全部指向Lab；云凭据不得放recipe明文env/manifest。本批prepare与GPU probe不消费Daytona key。新版设施已上线，接入前仍须核对具体recipe。

## 成本口径（2026-09-07 公开标价，仅估算）

[Modal pricing](https://modal.com/pricing) 中 Sandbox 使用独立费率：$0.00003942/物理核/秒、$0.00000667/GiB/秒，物理核按 2 vCPU 标注。按 1 vCPU（0.5 核）+1 GiB、未超请求使用量计算，约 $0.094968/沙箱小时；不要误用普通 Function 的低费率。[计费规则](https://modal.com/docs/guide/sandbox-resources) 为请求和实际用量取较大值。

[Daytona pricing](https://www.daytona.io/pricing)：$0.0504/vCPU/小时、$0.0162/GiB/小时，相同名义 1 vCPU+1 GiB 为 $0.0666/沙箱小时，另计适用存储。CPU 标称单位相近不证明真实性能相等。[生命周期计费](https://www.daytona.io/docs/billing) 也需考虑保留存储。

示例而非预测：2400 trial × 每个沙箱存活 180 秒 = 120 沙箱小时，以上纯 CPU/RAM 约 $11.40 / $7.99。并发 16 缩短墙钟，不把总沙箱小时再除以 16。模型思考期间仍存活的沙箱时间也计入；镜像构建、存储、GPU、地域加价、失败重试和账户折扣不含在此数中。

## 下一步与验收

已与interviewprep的“服务器改造”任务直接核对（01a06a06-efd0-7a62-99cd-454678f517c7）：通用升级已上线，两端SHA实查见上节；不安装Docker、不处理OASIS unit、不增加Harbor/RL控制器功能。当前精确阻断是Lab缺少prepare/GPU固定入口，正在Lab内实现；入口提交后对方负责不可覆盖source落位与最小静态recipe注册。

无容器后端的CPU检查仅验证安装/格式；完整Harbor trial已在Daytona执行。Mac不启动本地容器；home-5090拟承担模型服务，不因本实验安装主机Docker。三端预算只维护在BUDGET。

已安装 Harbor 官方 Modal extra，锁定 SDK 1.5.5，并补齐其官方 api-proxy-support extra。真实 `ModalEnvironment` 对象离线检查显示：默认 memory AUTO 返回标量请求，并非硬上限；显式 CPU/memory `guarantee` 分别返回 `(1,1)`、`(1024,1024)`，sandbox timeout 设为 300 秒。环境配置在 `configs/modal-m0-environment.json`，只是 Harbor EnvironmentConfig 片段，不是完整 job，也不证明云端 cgroup 已生效。Harbor 的 task/override CPU 字段只接受整数，0.5 被验证器拒绝；用户已确认 1 核规格。

Mac 的 SDK 读取已有系统代理，Lab 缺少 python-socks 时无凭据建连直接报 ImportError，外层重试表现为超时。补齐官方 extra 后两个官方端点均能建连，同步 Client.from_env/ClientHello 通过；异步外层超时检查仍曾失败，不推断两者差异根因。没有改代理或凭据。首次 provider 验证无法读取预期 `/sys/fs/cgroup/memory.max`，不能断言限额无效或已生效；下一步需核对该平台可观测机制，不直接改阈值或扩大实验。

Lab 已初始化独立 Git，父仓库忽略整个目录，远端为 ChaoyuWang04/Sandbox-MOPD-lab；后续 source_repo 可以使用 Lab Git 根，不再使用父仓库。server source checkout 和运行环境仍未部署，不能把本地拆分当成 hlab 已接入。仅在真实入口验证后提交固定 argv、timeout、risk、环境绝对路径及输出路径契约，不提前注册名不副实的 cpu-oracle-smoke。

已安装源码的 `harbor/agents/oracle.py` 明确把 solution 上传到 `/solution`；oracle 正例不能证明普通模型 agent 看不到参考解。wheel 自带 template-task 的 schema 1.4 能解析，但 solve/test 是占位内容，不能当可判分的完整官方示例。

Daytona采用已批准的8并发降级，16原目标未满足；当前功能正负对照见EXPERIMENTS。冷启动统计、账单及强对抗隔离仍须分别认证。普通timeout/退出与M3主动故障注入分开，任何一臂不满足适用隔离或回收门槛都停止晋级。

M3 三个臂必须固定同一 provider、镜像、区域与资源；换 provider 另做系统对照。平台 stop/terminate 与 Docker kill 的错误表现可能不同，要核对“工具超时、沙箱死亡、verifier 假阴性”的实际观察字段后再冻结注入协议。
