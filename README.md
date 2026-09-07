# Sandbox RL / MOPD Lab

## 版本管理

本目录独立 Git 跟踪，父仓库通过 `/sandbox-rl-MOPD-lab/` 忽略整个目录；不是 submodule。GitHub 仓库为 https://github.com/ChaoyuWang04/Sandbox-MOPD-lab ，origin 使用 HTTPS（本次 SSH 公钥认证失败，HTTPS refs 查询成功）。用户已授权每批完成并验证后提交、推送本仓库，不包含父仓库改动。原始 artifacts 保留本地，远程文档中的相应路径表示本地证据，不保证克隆后存在。

独立的容器 Agentic RL 与多教师在线蒸馏实验。M0已完成：G1实测12288输入/4096输出30.75秒及工具调用通过；G2授权8并发降级和G3正常阶段链路通过，测试进程/沙箱均已回收。2026-09-08用户取消G4首次冷准备速度检查，不将其历史失败改写为通过。M1尚未启动。

## 入口与状态

- 总体目标与 M0–M5 门槛：[总计划](docs/sandbox-rl-lab-plan.md)。
- 当前范围与下一步：[M0 实施计划](docs/plans/2026-09-07-m0-environment-plan.md)。
- 实测记录：[EXPERIMENTS](docs/EXPERIMENTS.md)；费用：[BUDGET](docs/BUDGET.md)。
- Harbor 格式、依赖来源和平台限制：[HARBOR_NOTES](docs/HARBOR_NOTES.md)。

## 独立边界

2026-09-07 用户批准本 Lab 独立于父项目：新模型 Qwen/Qwen3-4B、新任务池、新运行时、新训练环境。不导入父项目 syncopate/、不使用父项目模型、数据、venv、锁文件、runbook、Volume 或审计目录。harness-lab 仅供目录与记录方式参考，服务、密钥和产物各自独立。

用户已排除RunPod；本Lab优先home-5090模型推理与调度、Daytona CPU沙箱、Modal有界对照与云训练候选。遵循HOME-5090手册，Mac不跑长期服务。普通prepare/probe已接入并验收；冷prepare接入和测试已取消。终态与证据见EXPERIMENTS，未运行训练或故障注入。

Mac所有Lab文件在本目录。5090源码已落位`/home/samwang/code/projects/sandbox-rl-MOPD-lab`，控制器按commit创建独立运行worktree；持久环境/模型/cache/artifacts统一在独立`/home/samwang/data/sandbox-rl-MOPD-lab`，不写回source_repo。这是控制器源码与资产分离边界，不是借用其他项目资源。云端使用独立Lab路径，不借用父项目Volume。禁止将父仓库、宿主机HOME、密钥目录或Docker socket挂给agent。

第三方系统程序、驱动、Docker 管理的镜像层和平台托管 Secret 是存储边界的明确例外。按 HOME-5090 手册采用 hlab 后，它管理的 mirror/worktree/运行元数据也位于固定控制器目录，须以独立 Lab project ID 隔离。数据、模型、checkpoint、缓存、业务运行日志、W&B 本地文件和实验原始证据仍须显式定向 Lab。远端大文件留在远端 Lab 根下，Mac 保存清单与取回的必要证据。

## 目录

| 路径 | 内容 |
|---|---|
| `config/` | 无密钥环境模板 |
| `configs/` | 版本绑定的实验配置；5090 M0 serving锁在environments/home5090，训练栈另行冻结 |
| `docs/` | 总计划、当前实施计划、实验与预算账本 |
| `tasks/` | Harbor 任务源码、切分清单；运行实例进入 data/ |
| `recipe/`、`opd/` | 后续 RL 集成与蒸馏源码；当前只预留目录 |
| `lab_runtime/`、`scripts/`、`tests/` | Lab有界provider适配与测试；当前未写训练程序 |
| `secrets/` | `.env`，不提交、不输出到日志 |
| `models/`、`data/`、`checkpoints/` | 模型、运行数据和训练产物，不提交 |
| `cache/`、`logs/`、`artifacts/` | 缓存、日志、机器证据，不提交 |

Python 3.12.13 与 Harbor 0.22.0 已安装在 Lab `.venv/` 下，依赖由 `pyproject.toml` 和 `uv.lock` 固定；这不是 SkyRL/vLLM/GPU 环境锁。后续 Ray working_dir 只打包源码，显式排除环境、密钥和大文件。
