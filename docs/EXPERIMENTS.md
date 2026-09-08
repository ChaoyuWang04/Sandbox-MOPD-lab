# 实验记录

## M1 当前状态 · 实施中，未验收

2026-09-08用户批准持续完成200题准备；能力/来源/切分与验收见[总计划M1](sandbox-rl-lab-plan.md)，当前队列见[扩池计划](plans/2026-09-08-m1-200-case-plan.md)。现有自建48已生成并通过离线规格/质量审阅；外部来源19个登记资产已在独立Modal Volume下载并由导入器校验（448384495字节），TB已展开859文件，Harbor子集解包与完整catalog已完成。200题尚未建成或逐题实跑；无有效模型基线，未启动M2。旧32题campaign不直接复跑。

### v2自建与下载准备

自建源码`5322965`后经`c41f6ea`、`f15df30`补齐提前退出、四层None语义及资源对象identity反例。主任务实际生成48题，原32字节不变；清单身份与路径只维护在扩池计划。全套106项离线测试通过（21.319秒，含当时9项下载器测试）；本地生成后打印计数误用`tasks`键产生KeyError，实际API键为`instances`，复核48个目录和manifest无损，未重复生成覆盖。没有真实云对照，不把离线检查写成200题ready。

下载器和目录转换/隔离入口均经规格与质量审阅；Modal备用入口初始全套133项离线测试通过（21.895秒），后续精确平台挂载兼容19项相关测试通过。home5090 recipe仍待登记，不绕过hlab；在已授权平台范围及$2预留下启用无GPU/无secret的Modal CPU备用。

真实App `ap-KnwJizktwLDI0yRHNJhUMd`（源码`b9cb0e7`）已完成下载但在Harbor解包阶段以`unsafe_archive_member`失败，不能视为import整体完成。只读状态及tar成员核对：19资产共448384495字节，TB归档展开859文件；Harbor仅有范围外`CLAUDE.md`与`docs/CLAUDE.md`指向`AGENTS.md`的两个链接，拟跳过未选范围特殊成员，选内链接/路径越界/展开size检查不变。数据在Volume `sandbox-mopd-lab-m1-data-v2`（`vo-Hs5MyCjulIuJUOOsenUK0t`）的`data/m1/v2/sources/`；状态`artifacts/m1/v2/import-latest.json`与`modal-import-latest.json`。完整定义见[BUDGET](BUDGET.md)，前两次平台创建/挂载兼容失败身份保留在同处。

SWE-Gym已从Lite230扩展调查完整2438题，冻结原始记录支持A20/B20/组合10候选；TB保留A10/B10/组合10/OOD20候选。分类按任务目标，不自行增加“内部实现路径全部逐项验证”的新门槛；native grader的覆盖缺口保留，不声称组合机制已被全部独立证实。Smith52也已完成冻结数据内容匹配。候选研究在`data/m1/v2/research/`，都不是最终200清单，真实运行仍待完成。

后续App `ap-EcF3cSGivqD61WaxzrT8mD`（源码`f37929b`）通过下载复验、Harbor选定子集解包及离线wheel安装，终态在catalog失败，App已停止、tasks=0。只读逐行诊断确定首个问题是Smith首分片第3行`oauthlib__oauthlib.1fd52536.combine_file__0fukhdzk`的`problem_statement`为空（patch11399字符、FTP37/PTP636）；不是下载失败或磁盘不足证据。修复方向为原始行完整保留、无效行单独审计并排除候选，不放宽题面完整性要求；校验和、重复身份及输出量边界仍是硬错误。

修复`a567031`经两级审阅、全套146项测试通过（21.380秒）。真实App `ap-yzPJEsSEAIlMUFOQZxwm94`返回`phase=complete`：61804原始行（Smith59136、Gym2438、Lite230），accepted43771/rejected18033，输出5493296691字节。所有原始行保留；此为来源格式有效性，不是逐题可运行验收。Volume路径`data/m1/v2/catalog-77d2debc41878734/`，index SHA256 `7a71a8de06412a565093295b35a273b8ba4d96386ba500b6691fa1c29f5688cb`，rejected SHA256 `85ed9ea9926272bdd491f3307e7e61baadfddc62cabbbd9b53eab7cc81157ec3`；其余原始JSONL哈希在`artifacts/m1/v2/modal-catalog-77d2debc41878734.json`。后续完整读取rejected索引确认18033行均为`invalid_problem_statement`，不是仅据首个失败行推断。

冻结数据复核已选Smith52（A26/B26）及Gym50；加self48/TB50构成200内容候选。按原子替换4题划dev、其他新增self题train，来源配额和80/20/100切分核对通过；Smith仓库跨桶不重叠且与Gym仓库不重叠。Gym直接patch路径/作用域审计保留历史移动别名未穷尽的限制，最终清单尚未冻结。SWE63个不同镜像的Docker Hub元数据均返回Linux/amd64与摘要，最大压缩体积8183947224字节，存在性不等于启动通过。官方执行配置在`configs/m1-swe-profiles-v2.json`，6项本地测试与规格/质量审阅通过；沙箱API凭据只读验证成功、列表为空，组织管理接口401不当成key失效。没有创建或删除新的任务沙箱。

SWE许可证审计补齐63/63不同源码版本主许可证全文及Trio两份引用条款，无剩余下载缺口；主任务逐项复验63份正文SHA。审阅产物`data/m1/v2/research/swe-licenses-complete.json` SHA256 `0272886e941444a6bcdb5ca6227935d3d339deb62514f5b3fa468ed4d4754152`，每项保留精确commit/URL/全文和识别依据，尚待随正式任务包归档。Gym harness来源仓库标签已纠正为SWE-Bench-Fork，数据来源及任务身份未变。

固定TB2归档根目录仅有README和.gitignore，没有LICENSE；README提供官方Harbor下载/运行指引，但未声明再分发许可。保持原始题目资产在ignored数据目录/私有实验存储，仅提交来源URL、身份和结果元数据；不冒用Harbor工具许可证，不把本轮内部benchmark准备当成获准公开再分发。SWE构造/真实pytest小夹具已完成，最新代码测试及102条源数据加载证据见[扩池计划](plans/2026-09-08-m1-200-case-plan.md)，仍无200题云运行结论。

以下保留旧32题实施与失败证据。

- T1源码63b8416：4族32实例（每族train/eval各4），独立oracle和严格grader；7项离线测试逐实例验证oracle=1、NOP及排序/数值/缺项/类型/输入修改等反例=0；JSON<5及==5边界、Harbor30/180秒解析和字节确定性通过。两轮独立审阅通过。冻结清单路径及SHA只维护在M1计划；模型尚未读过这些评测实例。
- T2源码5cf2ad4：10项接口测试通过，覆盖多轮工具观测、token累计/模板漂移、异常finish、预算、私有路径检查、失败工具轨迹、HTTP取消收敛及usage未知标记。规格和质量审阅通过；mock不证明真实模型或沙箱链路。
- T3固定入口及持久campaign已实现、规格与质量复审通过：跨run消费pilot/全部32实例控制证据，未知创建或清理阻断后续创建；T6汇总保留全部尝试和未分类错误，不把未知usage算成零。主任务独立执行全套89项离线测试通过（6.894秒），质量审阅者独立重跑89项通过，其中真实Harbor Trial构造验证未连接provider。实际时限、云生命周期与基线尚未验证。
- 只读hlab doctor/recipes/runs确认本Lab活动任务0，GPU空闲31411MiB，磁盘734599720960bytes，为时点读数不构成预留。服务器负责人已部署控制器d4b62e6608b4f6019d512fa8ed6a8d9329e92cee；本任务现场recipes核对m1-small-pool登记一致，无新共享特性。
- 早先远端Lab secrets目录检查被权限策略拒绝后未绕过。用户随后明确批准精确pilot计划及凭据检查/缺失时供应；2026-09-08重新核对目录缺失，仅将本地已有Daytona key经SSH标准输入供应到专用secrets/daytona.env（独占创建0600，目录0700），未输出密钥或复制其他凭据。
- 首批pilot已失败并结束，未发生模型请求；尚无有效推理样本或基线，Daytona实际账单/credits抵扣未验证。
- 已评审执行源码`b9fc5787aeff6883fbb72100f574b028bd1701fb`已push到`origin/codex/m1-small-pool`，并由hlab从独立Lab主checkout同步到`refs/hlab/b9fc5787aeff6883fbb72100f574b028bd1701fb`（working_tree_not_synced=false）。linked worktree被客户端self-contained检查拒绝后，改用同一仓库主checkout传送同一已提交对象；未合并或同步脏文件。服务器负责人已确认独立Lab边界，普通recipe扩展不涉及Syncopate主线迁移。
- 首pilot4精确计划`plan-sandbox-rl-mopd-20260907t175341z-82fc6534`，执行上述b9fc578源码，argv `[/usr/bin/python3, scripts/m1_run.py]`、cwd `.`、env/bindings空、timeout3660秒、risk=training、approval_required=true；registry_digest `f8a2d202abe6fe6b7d7e57bea915d1219e41a971c94af0f387c3771c7290c2d5`。用户原文“我同意你上面的计划”，随后“可以的请开始吧”；2026-09-08T02:12:08Z唯一提交为`run-sandbox-rl-mopd-20260908t021208z-d1853cfa`，unit为`hlab-run-sandbox-rl-mopd-20260908t021208z-d1853cfa.service`。02:13:13Z终态failed/exit1，不重复提交已消费plan。

### 首pilot失败与修复（2026-09-08）

原始目录`/home/samwang/data/sandbox-rl-MOPD-lab/artifacts/m1/home5090/m1-373b33adc10e43899f6bb5dbc600cf05`；state SHA256 `3d0e6805c27ac6a163892dc797efb9e1e2101599698bf71f945569e1851cf7b5`，summary SHA256 `2388713bd8d3e59da5cbdc2c94c6c6feec76f99052d06ac52a26be9fa0784909`。

- 创建1个sandbox（ID `6cd0449c-0a8b-48e4-8278-001a3b89fbc2`，创建返回9.317秒），仅尝试fs_logs-train-00，其余3个未启动。START/END隔离探针均exit0、stdout为`private-absent\n`；代码严格比较无换行文本导致误判，error_phase=isolation_start，turns=[]，模型调用0，reward=null。不是模型能力失败或证实私有答案可见。
- Harbor清理后独立delete遇DaytonaConflictError，再次清理遇DaytonaNotFoundError，批次因此CleanupUncertain。之后使用独立客户端只读GET精确ID取得DaytonaNotFoundError，按精确Lab/run标签list为空；确认该时点无本批沙箱，未新建或追加删除。旧summary保留原来的不确定状态，不能追改为成功。
- 脚本60.174秒，显存1秒采样峰值22946MiB，owned_group_gone=true；失败路径post_model_check=not_checked，不能声称模型前后复验完成。hlab活动Lab任务复核0；共享GPU空闲27224MiB是复核时点，不把其他占用归成本实验或停止他人。
- 最小修复：隔离判据仅允许精确标记后无换行/LF/CRLF，仍拒绝额外内容；清理只捕获409/404竞态并在原截止时间内等待fresh list空，不直接把异常当成功、不重复delete。新增3项测试先RED后GREEN；全套92 tests PASS（12.315秒），独立审阅41项M1测试通过。尚未真实复跑。
- 旧campaign继续锁定：保留run/summary哈希、1次尝试和既有预算消耗，附独立清理确认。当前ledger的unresolved_cleanup=true和旧agent身份仍保留；200题使用另行设计的v2身份与预算关联，不清空账本，不用旧plan直接新建。下一步先做来源清点与扩池，而不是恢复旧pilot。

## M0 收尾 · PASS（2026-09-08，用户修订验收范围）

用户明确取消M0-G4“从零准备≤20分钟”：当前不换机器，不以首次公网下载耗时作为阶段门槛。该项记为移除，不改写历史失败为通过。G1满上下文推理/工具调用、G2授权8并发降级、G3官方正负对照和正常阶段回收均通过，证据见下文，因此M0完成。M1尚未启动，强对抗隔离、训练能力和大规模稳定性不属于本次通过声明。

收尾实查hlab活动Lab任务为0，G1原run仍succeeded/exit0且状态SHA一致。服务器负责人确认未注册/部署第三个冷recipe；已取消接入请求、暂停并改写heartbeat m0防止旧计划恢复执行。未提交冷运行，未额外下载20–35GiB；删除未使用的冷准备入口及其专用测试，Git保留历史。保留普通prepare/probe、私有头文件修复、模型/环境和原始失败/成功证据。

以下为逐次实验记录，其中旧阶段的“尚未通过/待执行”和已取消G4属于当时状态，以本节最终验收为准。

## M0-G1 · 私有头文件修复后 PASS（2026-09-08本地时间）

- Lab `ebdde738cfb03e4793125d771a23098de22cc930`，43项测试（头文件3项先RED）及独立review通过。系统Python.h仍缺失且sudo需密码；官方APT固定SHA的dev包仅解包到Lab本次prepare run，头文件manifest及无GPU gcc检查通过。系统Python/库/网络/驱动均未改；后续真实Triton编译通过，不能把无GPU语法检查单独当运行兼容证明。
- 缓存prepare plan `plan-sandbox-rl-mopd-20260907t161339z-887a8f9f`，run `run-sandbox-rl-mopd-20260907t161352z-090708ef` succeeded/exit0，7.236秒，cold_start=false；状态SHA `6dfad4e010e96d553a1ba3404933c30cf248d5969f89e3c964902da7a54bff64`。不是G4通过。
- GPU plan `plan-sandbox-rl-mopd-20260907t161432z-4a28be05`，唯一run `run-sandbox-rl-mopd-20260907t161450z-131ece78` succeeded/exit0，脚本185.095秒。独立解析实际请求/响应：输入12288、输出4096且4096个token_ids、finish_reason=length，生成墙钟30.751秒<90秒；tool_calls=add_numbers(a=17,b=25)正确。模型前后manifest一致，owned_group_gone=true，显存按秒采样峰值22000MiB（非硬峰值证明）。Triton编译14.63秒，CUDA graph预热66秒，不计入单请求生成时间但计入脚本总时间。
- 原始目录`/home/samwang/data/sandbox-rl-MOPD-lab/artifacts/m0/home5090/probe-20260907T161451Z-a75fe31757164a239c40b002037438c1`；状态SHA `2f27e10fcbce10e41fd878c0ad35f640adaa4f89162da9f32fa3b5d74a7c2b2a`。tokenizer对构造时140001-token中间序列警告超131072，实际请求已截为12288并独立核实，不是发送超长输入。G1/G2/G3有通过证据，G4仍需真正冷复测。

## M0-G1 · 首次GPU smoke：缺少系统Python开发头文件

- 用户批准已展示的单卡660秒计划后，唯一提交`plan-sandbox-rl-mopd-20260907t150221z-d7c07fa5`，run `run-sandbox-rl-mopd-20260907t152121z-ecf732e7`，源码`cf91c2395f32ead8fbfa8f07f85bf8e12ca697ed`。15:21:21Z开始、15:22:12Z控制器终止，failed/exit1；脚本46.121秒。
- 模型3分片完整加载，日志报告7.56GiB模型内存；随后Triton编译cuda_utils.c报`fatal error: Python.h: No such file or directory`。sysconfig指向`/usr/include/python3.12`，实际Python.h不存在，dpkg-query确认python3.12-dev/libpython3.12-dev未安装。这是系统开发依赖缺口，不是模型下载失败或已证明的网络问题。[Ubuntu官方包说明](https://packages.ubuntu.com/noble/libpython3.12-dev)列出对应开发头文件包；已交服务器改造任务核对最小补齐，不修改网络/驱动/他人进程。
- 未进入请求验收，G1未通过；峰值按秒采样9178MiB，owned_group_gone=true，失败后post_model_check=not_checked。固定probe-latest.json SHA256 `aa89b45a8a71ef92d2c2e8462c525add2159112127db6a26166cc432b685014d`；原始证据在`/home/samwang/data/sandbox-rl-MOPD-lab/artifacts/m0/home5090/probe-20260907T152121Z-bdcd36ebde7f4b84a6f9f534dea4e884`。不重复提交已消费plan，不关闭编译绕过根因。

## M0 · 网络实测与自适应续装配置

2026-09-07用户明确授权：先实测源站/镜像，根据实际情况调整下载限制并继续安装，不为这些参数重复要求操作。安装恢复不改GPU授权、共享系统或实验结论。

- 5090直接HTTPS无凭据测速，curl -q、禁用显式代理、每次最多8MiB/20秒，payload不落盘。相同vLLM0.28.0 wheel的SHA与原锁一致。串行两轮速度（MiB/s）：PyPI1.03/0.89、华为0.51/0.90、阿里0.99/1.37；清华主域和专用域各6秒TLS超时。PyPI两路不同区间16MiB合计8.587秒，约1.86MiB/s。此为短测，不是持续吞吐或4并发收益保证。
- 固定Qwen分片HTTP范围下载：HF官方0.76MiB/s、hf-mirror0.91MiB/s，均HTTP206/8MiB，最终同落us.aws.cdn.hf.co；不能据此宣称独立镜像更快。保留HF官方HTTP传输，2文件并发，暂不用未经此轮测量的Xet扇出。没有修改DNS/VPN/驱动、系统代理或共享环境。
- 本次选择阿里作包传输候选。原uv.lock完全不变：离线冻结export后得到245个固定包/511个原锁允许SHA；无direct-URL requirements。运行时导出到本次run目录，uv pip sync显式专属venv、require-hashes、no-build、default-index，后接pip check；不会让镜像重新选版本。40项测试（4项新RED→GREEN）及独立规格/代码review通过，两份锁offline check通过。
- UV读取120秒/并发4；HF读取120秒/元数据30秒/官方CLI1.30.0的max-workers2。prepare最多7200秒，控制器7260秒；G4仍独立判cold且≤1200秒。缓存续装不能抹去下面的首次冷准备失败。预计需几十分钟到约两小时，实际由完整文件下载决定。
- 原始测速摘要：artifacts/m0/network-source-probe.json；官方依据：[uv环境参数](https://docs.astral.sh/uv/configuration/environment/)、[HF环境参数](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)、[清华镜像说明](https://mirror.tuna.tsinghua.edu.cn/help/pypi/)。服务器同事负责单独更新prepare时限，Lab负责同步精确新commit后启动一次新plan。
- 续装已成功：Lab `cf91c2395f32ead8fbfa8f07f85bf8e12ca697ed`，controller `20efa2718139173e31ab7a403f6662eb5e94914f`；plan `plan-sandbox-rl-mopd-20260907t142308z-f5c8df69`，唯一run `run-sandbox-rl-mopd-20260907t142335z-3c2c88b3`。14:23:35Z开始、14:58:34Z结束，status=succeeded/exit0，精确unit终态MainPID=0、inactive/dead。脚本2093.272秒（34分53秒），cold_start=false、g4_candidate=false、gpu_validated=false；环境就绪，不是G4冷启动通过。
- 原始目录`/home/samwang/data/sandbox-rl-MOPD-lab/artifacts/m0/home5090/prepare-20260907T142335Z-41581ab8b61a4844a63fc886c4d6cc39`；固定prepare-latest.json SHA256为`7a851e5884268c056d17be3d16fdc15f1d387c6ac0f1665dcaa1321f5aca793b`，source_git_sha/run_dir均匹配。pip check成功；独立比较245个pip_freeze包名/版本与锁全部相等，固定revision `1cfa9a7208912126459214e8b04321603b3df60c` 的13个模型文件SHA256复验匹配manifest，3个safetensors分片合计8,044,982,000字节。依赖准备13分52秒；模型期间有网络重连但最终完成。
- 全Lab data根一次du核对19,787,198,464字节（约18.43GiB）；env/cache存在硬链接，不能把分目录du相加当实际总占用。原始日志和模型留在5090，没有删除缓存、新增GPU或云资源。
- 安装跟进（heartbeat id=m0）在环境完成交付后暂停；不在Mac新增模型或容器服务。禁止重复安装，下一步仅有待批准GPU计划，见实施计划。

## M0-G4 · 首次受控 prepare FAIL（网络读取超时）

- 用户看过精确计划后批准原文：“可以没问题, 请开始吧”。仅批准准备环境和固定模型下载，不含 GPU 运行。
- plan `plan-sandbox-rl-mopd-20260907t132949z-10a59c45`；固定 Lab commit `437590c9eeaf3cba5636dd1979d27fb27c78d272`；controller 部署由负责人回报为 `fc3b7282cb05c04ed32127242ac54839fb7c6b2c`。主任务通过 doctor/projects/recipes 独立核对接入与固定参数。
- 唯一 run `run-sandbox-rl-mopd-20260907t134805z-b0da49fb`，unit `hlab-run-sandbox-rl-mopd-20260907t134805z-b0da49fb.service`，2026-09-07T13:48:05Z 创建，13:59:00.734935Z 终止。精确 status 实读 failed/exit1；脚本冷启动耗时650.242秒。内部日志确认 clean detached SHA、系统 Python3.12.3 和独立 venv 创建；vLLM0.28.0 wheel下载最终报 network timeout（UV_HTTP_TIMEOUT=30s），未开始hf模型下载。不是已验证的版本冲突或总时限耗尽。
- 服务器原始证据目录：`/home/samwang/data/sandbox-rl-MOPD-lab/artifacts/m0/home5090/prepare-20260907T134805Z-52eb52a14ccc4140a3c56db06cd9db1d`。`prepare-latest.json` 在入口 finally 发布，运行时不存在不表示入口未执行。
- 状态文件SHA256 `9836553164b9878b963cbbb68b2db7004e5daec6f49bb74f58ffd7557c9ef75c`，success=false/cold_start=true。终态精确unit MainPID=0、inactive/dead。保留envs 94208bytes、cache 3352326144bytes、models空目录4096bytes（du分配读数）；没有删除或重试。一次运行中unit MemoryCurrent=4108701696、MemoryPeak=6301720576 bytes，只是采样时点、包含计入缓存，不是最终峰值证明。
- `hlab runs` 遇到运行中 `finished_at=null` 的索引校验缺陷；精确 status/logs 仍可用。已交共享设施负责人核对，不改运行记录、不重投、不停止任务。准入磁盘 available=755107246080 bytes，为时点读数；未启动 GPU。小型证据摘要在artifacts/m0/home5090-prepare-first.json，原始日志保留服务器。
- 后续安装策略已按用户新授权经实测更新，见本页顶部；使用新commit/plan，不复用已消费计划、不改写本次冷启动失败。

## M0-G1/G4 · 固定入口代码准备（不是目标环境验收）

共享控制器负责人回报通用升级已上线；本任务只读核对两端git HEAD均为52b327d398b461d0e36b617c19ebf5c1c5c12b1c，hlab --help包含runs。未替其他任务清理或重启任何进程。

- environments/home5090的独立Linux/Python3.12锁实际解析246包（vLLM0.28.0、Harbor0.22.0），锁内没有只提供sdist的依赖；这不是所有目标wheel已安装/可运行的证明。Mac未安装这些GPU依赖。官方Qwen模型API固定revision为1cfa9a7208912126459214e8b04321603b3df60c。
- 准备scripts/prepare_environment.py和scripts/qwen3_4b_m0_probe.py，真实实现位于lab_runtime/home5090*.py；参数/路径/模型身份只有共享模块一个来源。source必须clean detached；持久资产移到独立data根，防止写source_repo。准确资源、时限、状态路径见实施计划与BUDGET。
- TDD覆盖短输出、输入不足、token ID不足、90秒边界、错误工具参数、Mac拒绝、隐式凭据过滤、锁竞争、未知目录、源码身份、模型漂移、端口所属进程组、显存对象、超时/信号清理等。主进程重跑36项离线测试PASS，Mac132包锁和Linux246包锁均offline check PASS。
- 独立规格审阅拦下“抢锁失败覆盖latest”和“cleanup被总超时打断”的问题，修复与负向回归后复审通过；失败post-model-check明确not_checked，成功才标verified。独立代码质量review无阻断；目标机安装与运行仍待plan审批。
- 未进行服务器依赖安装、模型下载、GPU生成或从零20分钟计时；不把入口代码通过当M0-G1/G4通过。将精确commit交控制器负责人注册，实际prepare/probe均需精确plan和用户approval note。

## M0-G3 · Harbor正负对照与正常阶段隔离 PASS

- 固定上游commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276` 的hello-world，源码在tasks/m0-hello-world，许可证与PROVENANCE保留。六个行为文件对照官方raw：三个完全相同、三个只有空白差异。
- 本地首次构造失败：Daytona适配器不支持候选配置的guarantee；未创建云资源。沿真实资源校验路径改为request，增加离线回归；真实沙箱阶段另读cgroup，不将request当硬保证。
- Lab适配器在最终_create_sandbox给image/snapshot请求补ttl_minutes=5、public=False。独立审阅发现上游创建层有额外重试；测试先观察3次而非1次，再显式stop_after_attempt(1)，保留上游取消保护与ID捕获。三个本地测试通过。
- NOP `mopd-g3-nop-deeca65f`：54.81秒，reward=0，exception_info=null；CTRF两项测试确实因hello.txt不存在失败。agent START/END均实际执行私有路径不存在与memory.max=1073741824、cpu.max="100000 100000"断言，exit0。沙箱32d83671-6adf-49b5-af36-36208fd2db80独立get NOT_FOUND、标签列表空。退出时上游客户端atexit跨事件循环清理抛CancelledError；不影响已落盘奖励，后续oracle在原事件循环显式关闭客户端。
- Oracle `mopd-g3-oracle-6b3c4a84`：36.99秒，reward=1，exception_info=null；agent START私有路径与cgroup断言exit0。沙箱ef49d15a-f848-4ced-af28-1ff15df47c42独立get NOT_FOUND、标签列表空。NOP与oracle间新增LICENSE/PROVENANCE导致task目录checksum不同，六个行为文件未改；不得比较该checksum为完全一致。
- 两个trial的result.json、config.json、lock.json、verifier/ctrf.json、reward.txt、test-stdout.txt和oracle轨迹在artifacts/m0/harbor各run目录。官方verifier在线安装，实际Python3.14.0；此结果不证明G4全部依赖冻结。
- 隔离边界：NOP正常agent阶段没有私有材料；oracle按定义会读取solution，verifier随后上传tests到同一沙箱。未证明恶意后台进程跨阶段不可读，M1前不得把这种阶段检查当强对抗隔离。

## M0-G2 · 8并发授权降级 PASS，原16门槛未通过

2026-09-07用户批准直接测16，不满足后测8，无需修改账户权限。每个资源与停止方案见BUDGET的G2预注册。AsyncDaytona同时发起所有create，全部返回后再同时delete(wait=True)，不是滚动复用少量沙箱。

- 16批次 `mopd-g2-b4c1ab045421`：10创建成功，6个DaytonaBadRequestError；全部创建请求结束7.36秒，至全部成功对象删除返回10.48秒。只保留异常类别，不能据此确认为配额拒绝。
- 8批次 `mopd-g2-f3e3514e1e9a`：8/8成功，全部就绪3.15秒，至全部删除返回4.97秒，低于60秒。按用户明确授权采用8并发，不改写原16成功率。
- 两批delete后即时list仍返回部分旧条目；后续独立客户端按精确标签查询均为空，确认18个实际创建对象全部回收。计时不含延迟列表复核；镜像此前用过，缓存状态未知，不声称冷启动性能。
- 证据 artifacts/m0/daytona-concurrency.json（实测输出整理）。无GPU、未提额/充值/改权限。完整M0仍须G1/G3/G4。

## Daytona 认证与单沙箱 · PASS（不是完整 M0）

- 用户已在本地受控 secrets/.env 配置 API key，并要求完整推进 M0。未打印、提交或上传该 key；它仅用于控制端认证，未注入沙箱。
- 认证：SDK 0.210.0 使用 ListSandboxesQuery 的列表查询 PASS。最初一次探针误用旧版 list 接口报 AttributeError，修正为当前接口后成功，不属于认证故障。
- 沙箱 `f02d8061-20ad-4a2f-b5a3-000be5734aba`：python:3.12-slim，1 vCPU、1 GiB、3 GiB 磁盘、5 分钟 TTL、1 分钟空闲停止、ephemeral、禁止出站网络。创建并就绪 3.09 秒，到 exec 结果 3.87 秒；文件字节往返 PASS，正例0/负例1，exec exit0。
- cgroup 实读 memory.max=1073741824、cpu.max="100000 100000"，配置读数符合本次1 GiB/1 CPU；不是OOM故障注入测试，也不推广为全部provider已认证。
- delete(wait=True) PASS，独立 get 返回 NOT_FOUND。没有留下本次沙箱。
- 组织额度查询：官方HTTP接口返回401，经成功认证SDK底层 OrganizationsApi 复查仍为 UnauthorizedException。账户tier/余额仍未知；后续按用户新授权完成16/8直接试验，见G2结果，不再要求组织查询权限作为前置。
- 证据：artifacts/m0/daytona-auth-smoke.json。首次成功不代表冷/热性能统计、G2或G3通过。

## M0 后续前置与已准备配置

- G1/G4：5090只读检查时主内存available=28531146752 bytes、显存free=31435 MiB、利用率0%；仅见sunshine计算进程。资源时点不等于预约，未启动任何GPU工作。hlab仍无Lab项目。共享设施协作者确认需要精确commit的source checkout落位、固定prepare入口、评审后的controller配置和上线授权；不能临时SSH安装后冒充受控接入。
- G3的已执行结果、配置修正与真实隔离范围见本页顶部；不再将旧候选配置当未执行前置。

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
