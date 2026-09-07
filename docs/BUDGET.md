# 预算账本

总计划硬上限 $500；这是实验设计预算，不表示本轮获得全额运行授权。当前执行 M0，逐批CPU smoke、并发与Harbor正负对照范围见下文。

## 三端资源边界

用户于 2026-09-07 裁定：Mac 日常办公至少保留 12 GiB，两个实验合计最多使用 12 GiB。当前 Mac 已有较高压缩与 swap；本 Lab 不新增常驻进程、不运行本地模型或容器，短时 CPU 检查串行、目标不超过 0.5 GiB（尚非操作系统硬限额）。不修改 Docker 分配，不停止其他实验或办公应用。

home-5090 拟承担模型与调度，初期主内存预算 16 GiB、显存预算 24 GiB，从单请求测量；尚未部署或证明容量足够。Daytona 优先承担大规模 CPU 沙箱，Modal 为小规模对照；未启动GPU。用户报告 Daytona $200 credits，余额、期限和 tier 未核实；credits 不等于资源额度或无限运行授权。最新已批准并完成16/8并发试验，后续采用8并发；不是M1大规模运行授权。

Harbor 0.22.0 仅接受整数 CPU，先前提出的 0.5 物理核不适用于完整 Harbor trial。用户已确认调整为 1 物理核、1024 MiB、300 秒最大存活、60 秒空闲超时，并批准 API 预检成功后执行一次有界验证。按此前核对的 Sandbox CPU/内存费率，300 秒约 $0.01383，不含镜像构建、额外存储等费用，因此不是总账单硬上限。该次验证已执行，实际费用待核对。

| 日期 | 批次 | 资源/时长 | 新增云资源费用 | 证据 |
|---|---|---|---|---|
| 2026-09-07 | M0-A | Mac 与现有 5090 只读检查；公开文档查询 | $0（未分配云资源） | artifacts/m0/inventory.json |

Modal累计2个CPU Sandbox，均确认停止；Daytona包含原单沙箱1个、G2实际创建18个和G3串行trial，精确ID及终态见EXPERIMENTS。GPU会话0。Daytona凭据已认证，组织余额/配额查询无权限，未验证credits抵扣。实际CPU/构建账单待核对，不将本轮写成零云费用。现有设备电费、开发工具订阅及助手调用费用不含在估算中。

## G1/G4 固定入口准备预算

首个已批准prepare失败：650.242秒，GPU会话0、云资源新增0；保留服务器Lab cache约3.12GiB和空venv骨架，精确分配字节见EXPERIMENTS。尚未下载模型，无删除。下一次若复用缓存须标缓存恢复，不能冒充从零准备。

代码准备不分配GPU，不在Mac安装Linux/GPU依赖。目标5090持久根为/home/samwang/data/sandbox-rl-MOPD-lab，下设按锁SHA键控的envs/m0-serving-<lock-id>、固定revision的models/Qwen3-4B、cache和artifacts；不写source_repo。prepare最多1200秒（退出清理另预留），磁盘空闲不足80GiB拒绝；下载公开约4B BF16模型和锁定wheel，不安装Docker/驱动，不编译源码包。预计需要数十GiB存储，实际大小与从零/缓存状态由prepare记录，不宣称已占用。

probe正式提交前展示精确hlab plan并取得用户approval note：GPU0、BF16、16384上下文、单请求4096输出、最长600秒，规划显存比例0.70且采样本次进程组峰值>24GiB停止自身。GPU空闲不足26GiB或主存available不足18GiB拒绝，不停止其他进程。主存16GiB是本Lab目标预算，目前脚本资源读数并非OS硬限额；最终运行需在计划中明确此局限。Mac仅代码/短时CPU检查，云资源本批不新增。模型推理与G4实测尚未运行。

## G3 有界完整链路预注册

按完整M0授权：官方hello-world固定commit，串行NOP/oracle各一次、无重试、每次1CPU/1GiB/3GiB、5分钟服务端TTL，CPU费用预留$1含构建余量（非账单硬限额）。Lab适配器在实际create边界补TTL，不改上游包；TDD断言已先见None!=5再通过。官方verifier需要出站apt/astral/PyPI，不传任何业务秘密。每次外层执行240秒，构建时限缩为120秒；独立清理、列表确认后才下一次。NOP在agent START/END检查/tests和/solution不存在，reward=0；oracle reward=1；须检查exception_info为空及判分日志，不能把依赖失败产生的0当负对照通过。仅验证正常阶段隔离，不证明恶意后台进程无法跨阶段偷读。代码/例题/小日志在Lab；不运行Mac服务或本地容器。

## G2 最新授权与预注册

用户明确批准直接尝试 Daytona 16 并发；不满足时完全清理后尝试8。每个 python:3.12-slim 沙箱1 vCPU/1 GiB/3 GiB，ttl_minutes=5、空闲停止1分钟、ephemeral、禁止出站、非公开、无密钥。最多两批，总创建请求24个；名义峰值16 vCPU/16 GiB/48 GiB。创建全部返回后再并行删除，确保真正同时驻留；create timeout60秒，delete(wait=True) timeout60秒。只按唯一run标签和返回ID清理，列表非空不开始下一批。G2原门槛为16全部就绪并删除<60秒；8成功单独记降级，不冒充16通过。冷镜像构建与缓存状态如实标记未知。本批预留$1（不是平台硬账单上限），不提额、不绑卡、不充值。证据 artifacts/m0/daytona-concurrency.json；错误只保存类别，不输出可能含凭据的请求内容。官方依据：https://www.daytona.io/docs/en/limits/ 与 https://www.daytona.io/docs/en/sandboxes/ ，2026-09-07核对。

后续每批开跑前登记平台、卡型/卡数、单价来源与时间、最长时限、GPU/CPU/存储费用预留、停止命令及证据路径。结束后填实际开始/结束时间、停止确认和账单。单里程碑超预估 50% 暂停；计费上限也应由程序定时退出约束。
