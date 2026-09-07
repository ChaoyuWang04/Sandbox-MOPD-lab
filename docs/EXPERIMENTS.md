# 实验记录

## Daytona 认证与单沙箱 · PASS（不是完整 M0）

- 用户已在本地受控 secrets/.env 配置 API key，并要求完整推进 M0。未打印、提交或上传该 key；它仅用于控制端认证，未注入沙箱。
- 认证：SDK 0.210.0 使用 ListSandboxesQuery 的列表查询 PASS。最初一次探针误用旧版 list 接口报 AttributeError，修正为当前接口后成功，不属于认证故障。
- 沙箱 `f02d8061-20ad-4a2f-b5a3-000be5734aba`：python:3.12-slim，1 vCPU、1 GiB、3 GiB 磁盘、5 分钟 TTL、1 分钟空闲停止、ephemeral、禁止出站网络。创建并就绪 3.09 秒，到 exec 结果 3.87 秒；文件字节往返 PASS，正例0/负例1，exec exit0。
- cgroup 实读 memory.max=1073741824、cpu.max="100000 100000"，配置读数符合本次1 GiB/1 CPU；不是OOM故障注入测试，也不推广为全部provider已认证。
- delete(wait=True) PASS，独立 get 返回 NOT_FOUND。没有留下本次沙箱。
- 组织额度查询：官方HTTP接口返回401，经成功认证SDK底层 OrganizationsApi 复查仍为 UnauthorizedException。不能确认账户tier/余额/16并发许可；不调整权限或强行创建16个沙箱。
- 证据：artifacts/m0/daytona-auth-smoke.json。首次成功不代表冷/热性能统计、G2或G3通过。

## M0 后续前置与已准备配置

- G1/G4：5090只读检查时主内存available=28531146752 bytes、显存free=31435 MiB、利用率0%；仅见sunshine计算进程。资源时点不等于预约，未启动任何GPU工作。hlab仍无Lab项目。共享设施协作者确认需要精确commit的source checkout落位、固定prepare入口、评审后的controller配置和上线授权；不能临时SSH安装后冒充受控接入。
- G3：官方Harbor v0.22.0 annotated tag已解析为commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276`，六个hello-world文件读取成功。候选配置 `configs/m0-harbor-oracle.json` 固定该源码，1次/1并发/无job重试，资源覆盖为1CPU/1GiB/3GiB。原示例为2GiB/10GiB，覆盖不是任务内容修改。
- 此配置尚未执行：官方verifier需要apt/curl/uvx下载依赖，不能继承前一smoke的全禁网；Harbor 0.22.0适配器提供auto-stop/auto-delete但未暴露Daytona新wall-clock TTL，不能静默将kwargs当作已生效TTL。先补齐有界执行/清理设计、固定依赖和缓存路径，再运行完整G3。官方oracle能读参考解，不替代普通agent的隔离负检查。

## 双平台功能 smoke · 预注册

2026-09-07 用户批准 Modal/Daytona 各做有界 smoke，后续大规模沙箱优先 Daytona（用户报告 $200 credits，账户余额/期限未核实）。每家最多一个、串行推进，不是性能排名；资源名义 1 CPU/1 GiB，但 Modal physical core 与 Daytona vCPU 不等价。Modal 最大存活 300 秒、空闲 60 秒；Daytona 必须在认证后先确认可设置服务端 wall-clock TTL/auto-stop/auto-delete 再创建。无 GPU、业务凭据、宿主机挂载或端口暴露。

本批仅验功能层：创建 → exec → 写入/读回固定字节 → 正确判据 exit 0 → 故意错误判据 exit 1 → 精确终止/删除 → 独立终态查询。记录创建返回与可执行分别耗时，不拿空闲单次读数排名；费用与主机资源控制仍单独未验。上次 cgroup 缺失的硬限额 Gate 保持未通过，本批不再拿它阻断其他功能测量，也不把功能成功升级为限额通过。清理失败须独立查精确 ID，禁止新建重试。Daytona 无凭据时记 BLOCKED，不用其他账户或把 key 写入报告。

### 本批结果

- Modal：ClientHello PASS；唯一新沙箱 `sb-hqjAFWCxQnCBj9uqsKay0F`，创建返回 0.86 秒；exec stdout 为空、exit=-1，至该读数 32.95 秒（含网络与就绪，不是纯执行耗时）。文件/正负检查未获结果，功能 smoke FAIL。terminate 调用 ConnectionError；独立新客户端 poll=0，确认远端结束，但不能认定终止 RPC 成功。无追加创建重试。
- Daytona：安装官方 extra，SDK 0.210.0；验证 CreateSandboxFromImageParams 配置接受 cpu=1、memory=1 GiB、disk=3 GiB、ttl_minutes=5、auto_stop_interval=1、ephemeral=True（auto_delete_interval=0）、public=False、network_block_all=True。离线检查 PASS，不是云端验证。环境变量未配置 DAYTONA_API_KEY，Lab secrets/ 尚无凭据，已请求用户通过本地受控文件提供；真实 smoke BLOCKED，未创建资源、未验证 $200 余额或账户配额。
- `uv lock --check --offline` PASS，132 个依赖解析；无 SkyRL/GPU 安装。Mac 无本地容器/模型/服务。

每批先登记目的、命令、预测、停止线与产物，再填写结果。证据位于本 Lab artifacts/，不引用父项目结果作为本实验通过依据。

## M0-A · 隔离与只读清点

- 日期：2026-09-07。
- 授权：用户要求更正拼写并开始上一轮提出的 M0-A。
- 预期：更名后仅有一个 Lab 根；记录 Mac、5090 的真实环境，缺失项及云端约束；不以清点替代运行 Gate。
- 操作：目录更名、文档与忽略规则建立；执行 uname/sw_vers、Python/uv 版本、df、Docker version/info；通过现有 SSH 别名查询 5090 的 nvidia-smi/free/df，并复核 uv、Docker、nvcc 常见绝对路径。公开文档及 PyPI 只读查询。
- 证据：`artifacts/m0/inventory.json`（工具返回值整理，非自动采集器）；详细命令见 `artifacts/m0/commands.md`。版本与来源见 HARBOR_NOTES。
- Mac：arm64、24 GiB RAM，Python 3.12.13 可用；默认 python3 为 3.14.7，后续必须显式指定 3.12。Docker client/server 29.7.2，Compose 5.5.0；Docker 分配 8,318,976,000 bytes、14 CPU；磁盘可用约 88 GiB。
- 5090：SSH 可达；RTX 5090 32,607 MiB，检查时已用 672 MiB，驱动 595.71.05；系统 Python 3.12.3；`/home/samwang/.local/bin/uv` 为 0.11.16，未在非交互 PATH 中；nvcc 13.2.78。Docker 命令及常见路径、默认 socket 未检出；不能声称已全盘确认未安装。磁盘可用约 719 GiB，RAM 可用约 28 GiB，swap 已用 6.8 GiB（时点值，未做性能归因）。
- 云端：普通 RunPod Docker 路径被官方限制阻断；Modal 内嵌 Docker 能力未证。没有选定 Pod，因此平台型号、单价、磁盘与实测版本留空。
- 网络：Mac 沙箱内部分只读系统/Docker 查询权限不足，升级权限后成功；直接请求 PyPI 完成。SkyRL raw 文件部分传输超时，Mercor raw 文件连接超时，保留为未完成的版本冻结项。
- 结论：M0-A 清点与文档完成；完整 M0 尚未通过。M0-G1/G2/G3 未执行；G4 先解决平台拓扑。
- 本地复核：旧目录已不存在；8 个 Markdown 本地链接全部可解析；inventory.json 通过 JSON 解析；7 类私有/生成路径均被 Git 忽略，README 与 config/.env.example 可跟踪；Lab 内没有新增 Python 实现或训练程序。未暂存或提交。

## 下一批

具体准备与开放问题只维护在 `docs/plans/2026-09-07-m0-environment-plan.md`。尚无模型/训练/故障注入运行结果。

## M0-B · Mac 独立 Harbor 环境

- 日期：2026-09-07；用户授权继续下一步，具体范围已登记实施计划。
- 安装：Lab 内 `UV_CACHE_DIR=$PWD/cache/uv TMPDIR=$PWD/cache/tmp uv sync --python /Users/samwong/.local/bin/python3.12`，生成 .venv 与 uv.lock；安装命令 exit 0，89 个包安装成功，不含训练栈或模型下载。
- 检查：`harbor --help` 与 `harbor run --help` exit 0；通过官方 `Task` 构造器及 `Task.is_valid_dir` 解析 wheel 自带 template-task，schema 1.4；断言 Python 3.12 和 Harbor 0.22.0；`uv lock --check --offline` exit 0。
- 证据：`artifacts/m0/harbor-environment.json`，由实际工具读数整理。模板解析不执行解答或判分；无容器启动，无 GPU/云费用。完整 M0-G1–G4 均未通过。
- 共享设施：已收到“服务器改造”直接回复并反馈 Git 根/无后端 oracle 两个边界。路线与未部署项统一见 HARBOR_NOTES；不改共享安装副本。

## M0-B · Modal 资源预检

### 已批准的单沙箱验证协议

用户确认 1 核/1 GiB/300 秒一次验证。先通过 SDK ClientHello，再使用独立 App `sandbox-rl-mopd-m0`、Modal Debian slim Python 3.12 镜像、CPU `(1,1)`、内存 `(1024,1024)`、GPU 无、并发 1、空闲超时 60 秒。无卷、秘密、宿主机挂载或开放端口，阻断沙箱出站网络。检查 Python exec、文件写读字节相等、错误终态返回非零、`/sys/fs/cgroup/memory.max` 等于 1073741824、`cpu.max` 配额/周期等于 1。缺少 cgroup 可读证据即记未验，不放宽判据。最后 finally 终止精确 sandbox ID，poll 必须非 None；不删共享资源，不扩大并发。SDK 预检或创建失败则不自动另建重试。此为 provider 预验证，不冒充完整 Harbor oracle trial；镜像构建费用和实际账单待查。

- 用户确认继续远端优先方案；Mac 不启动新容器或模型。
- 安装 `harbor[modal]==0.22.0`，新增 9 个依赖，Modal SDK 1.5.5；`uv lock --check --offline` exit 0。此前 harbor-environment.json 的 SHA 属于安装 extra 前快照，不代表当前锁。
- 用已安装 `ModalEnvironment` 正常构造器创建对象（不调用 start），断言 CPU `(1,1)`、内存 `(1024,1024)`、最长 300 秒；exit 0。Task EnvironmentConfig 拒绝 `cpus=0.5`，因此 1 核候选需要向用户同步。
- 环境配置片段：`configs/modal-m0-environment.json`。无真实 trial、云端限制或回收通过证据。不得据此注册远端 oracle recipe。
- 配置文件经 Harbor `EnvironmentConfig.model_validate_json` 解析，断言 CPU/内存 guarantee、1 核、1024 MiB、0 GPU、300 秒，exit 0。
- Modal `Client.from_env.aio()` 在外层 25 秒时限触发 TimeoutError，未确认认证成功；随后无凭据 TLS 探针成功（TLSv1.3，0.79 秒），API 根路径 HEAD 返回 HTTP/2 503。这只能说明 TCP/TLS 可达而 SDK 握手未完成；根路径 HEAD 不是 gRPC 健康检查，不能据此断定平台故障或 token 错误。未修改代理、凭据或共享设施。

### 单沙箱实际结果

- 缺失代理依赖的最小复现：官方两个端点无凭据 `create_channel().__connect__()` 均报缺少 python-socks。补齐 Modal 官方 api-proxy-support 后同一检查成功；同步 Client.from_env/ClientHello 成功。异步包装下仍曾超时，未宣称所有 SDK 超时均已根治。
- 唯一沙箱 `sb-kAKuRm2a3dkOEwlFTjZ8rV` 创建调用返回耗时 0.88 秒，不是容器就绪耗时。Python 已执行，文件往返断言通过后读 `/sys/fs/cgroup/memory.max` 触发 FileNotFoundError；probe exit 1。未完成 cpu.max 读取，错误终态负对照未执行。
- finally 对精确 ID 调用 terminate，但原客户端最终报 SSL handshake 超过 60 秒，未取得终止 RPC 成功确认。独立只读查询取得 poll=137，确认远端停止；不能区分停止来自该请求还是平台时限，也不能由 137 推断 OOM。原客户端已退出，无新增后台进程。未再次创建沙箱、扩大并发或启动 GPU。
- 结论：provider 可创建和执行，但预注册资源读数不成立，整体 NOT_PASSED。下一步先调查 Modal 沙箱的可观测限制/资源回报机制，再预注册可行证据方案，不靠不存在的路径或资源请求值自证限额。Harbor oracle/M0-G3 仍未执行。
- 证据：`artifacts/m0/modal-provider-smoke.json`；当前 uv.lock 离线检查通过。实际账单尚未核对。

## M0 · home-5090 与沙箱平台调查

- 日期/范围：2026-09-07，用户要求按 HOME-5090 手册继续调查，排除 RunPod，允许 home-5090/Modal/Daytona 沙箱。
- 目的与预期：以只读命令核对已部署控制器及 recipe；从官方接口确认 provider 能力、网络与计费，形成可执行接入前置。
- 操作：完整阅读 HOME-5090 及控制器 runbook/architecture/README；执行 hlab doctor/projects/recipes、两端 rev-parse；只读查询失败 unit、GPU、rootless 前置及两家 HTTPS 入口。
- 实际：两端控制器 SHA 相等；Lab 未注册；degraded 来自 OASIS bundle unit。云平台有原生 provider 入口，但认证/执行未测。详情及来源统一维护在 HARBOR_NOTES。
- 证据：`artifacts/m0/platform-investigation.json`，由本轮工具输出整理；不覆盖上一批 inventory。
- 结论：调查完成，推荐 home-5090 本机筛选/沙箱优先，Modal 原生 Sandbox 与 Daytona 为候选；安装/部署与完整 M0 Gate 仍未运行。控制器共享设施未修改，没有新云资源费用。
