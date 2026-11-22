# 知识库索引（字典入口）

> 生成时间：2026-11-23 05:42 ｜ 由 `scripts\build-handbook-index.ps1` 自动汇总，**改了分册就重跑**。
>
> **用法（省上下文的规矩）**：
> 1. 先看下面的速查表，定位到**一个**主题；
> 2. 只打开那一个分册（单册目标 ≤150 行）；
> 3. 分册之间用 `文件名` 互相指路，顺着跳，别整目录读；
> 4. `reports\` 里的原始证据体积大，**只在复核结论时才读**。

---

## 1. 速查：我想干什么 → 读哪册

| 我想… | 读这个 |
|---|---|
| 知道这台机器装了什么、端口/代理怎么设 | `01-environment.md` |
| 找文件在哪、哪份代码是权威 | `02-paths.md` |
| 确认服务在不在跑 / 看 PID | `03-processes.md` |
| 判断版本、解释"版本 ?"、双轨是什么 | `04-versions.md` |
| 弄清代码从哪来、能不能换源 | `05-sources.md` |
| 启动 / 停止 / 重启（含脱离会话的做法） | `06-runbook-start-stop.md` |
| 备份、恢复、换机迁移 | `07-runbook-backup-restore.md` |
| 看懂 `shell.json` 每个键、改配置 | `08-config-reference.md` |
| 查日志、判断噪音与真错误 | `09-logs.md` |
| 改完怎么验收（门 1/2/3） | `10-verification.md` |
| 提交、推送、认证报错、tag | `11-git-workflow.md` |
| 动手前确认红线与纪律 | `12-rules.md` |
| 遇到怪现象 / 看还有哪些 bug | `13-known-issues.md` |
| 写脚本报怪错（BOM/CRLF/编码） | `14-gotchas-windows.md` |
| 密钥、仓库可见性、账号风控 | `15-security.md` |
| 为什么这么做（不追新/自建通道…） | `16-decisions.md` |
| 了解外壳是怎么演化的 | `17-history-shell.md` |
| 今天改了什么、怎么回滚 | `18-worklog-2026-11-22.md` |
| 接下来做什么、优先级 | `19-next-steps.md` |
| 理解内核架构、请求链路、模块分工 | `20-architecture.md` |
| 看不懂某个词 | `21-glossary.md` |
| 要发新版 / 打包 / 安装回滚 | `22-release-pipeline.md` |

## 2. 全部分册（自动生成）

| # | 主题 | 何时读 | 关键词 | 行数 | KB | 文件 |
|---|---|---|---|---|---|---|
| 01 | **environment** | 换机器、排查"命令找不到/连不上网/端口占用"、确认工具版本时 | Windows, Python, node, 代理, 7897, 端口, 8788, 63045, 硬件 | 41 | 2.5 | `01-environment.md` |
| 02 | **paths** | 找文件、判断"这份代码在哪、谁在跑、哪份是权威"时 | 目录, wb-shell, 2api, repo, data, accounts, usage, launcher, maintenance | 70 | 4.3 | `02-paths.md` |
| 03 | **processes** | 怀疑没在跑、想看 PID、要判断"能不能动这个进程"时 | 进程, PID, pythonw, launcher, wb_proxy, 看门狗, 单实例 | 35 | 2.3 | `03-processes.md` |
| 04 | **versions** | 看到版本号对不上、"底栏显示版本 ?"、想升级、判断某个 tag 能不能用时 | 版本, 1.10.8, 1.10.9, 1.7.4, 2.1.3, 双轨, tag, release-manifest, validate_stage | 41 | 2.9 | `04-versions.md` |
| 05 | **sources** | 要补代码/比对上游/怀疑"我们这份是不是原版"、找替代源时 | 溯源, M-strange, niyouqianma, 404, 删号, 哈希, wb_entitlements, acc0b332 | 39 | 2.8 | `05-sources.md` |
| 06 | **start-stop** | 要启停服务、改完配置要生效、服务没起来时 | 启动, 停止, 重启, 启动.vbs, 看门狗, restart-wb-shell, 8788 | 45 | 3 | `06-runbook-start-stop.md` |
| 07 | **backup** | 要做/恢复/校验备份、换机迁移、怀疑丢了数据时 | 备份, 恢复, 计划任务, DPAPI, 明文token, 冷备份 | 54 | 3.1 | `07-runbook-backup-restore.md` |
| 08 | **config** | 改配置、解释某个开关、排查"为什么设置没生效"时 | shell.json, settings.json, 端口, 看门狗, 更新检查, 面板密码, PBKDF2, 限额 | 54 | 3.4 | `08-config-reference.md` |
| 09 | **logs** | 排查异常、怀疑有静默错误、想知道日志写在哪/怎么轮转时 | shell.log, core-8788.log, 轮转, 0x40010004, Traceback, ERROR | 43 | 2.5 | `09-logs.md` |
| 10 | **verification** | 改完代码要验收、想确认"仓库==生产"、准备提交/发版前 | 基线校验, 冒烟, run_all, 261, check_clean_checkout, 61 MATCH | 54 | 3.5 | `10-verification.md` |
| 11 | **git** | 提交/推送/建 tag、认证报错、别人克隆后脚本坏了时 | push, GCM, OAuth, GH007, filter-branch, credential.helper, tag, eol, crlf | 58 | 3.3 | `11-git-workflow.md` |
| 12 | **rules** | 动手改任何东西之前（尤其是"顺手改一点"的冲动出现时） | 红线, 禁止, 提交纪律, 生产保护, 端口, 测试 | 33 | 2.7 | `12-rules.md` |
| 13 | **known-issues** | 遇到怪现象先查这里；计划下一步做修复时 | bug, 待修, 版本双轨, 404, 0x40010004, setup_vendor, DPAPI | 33 | 3.2 | `13-known-issues.md` |
| 14 | **gotchas** | 写脚本/批处理/计划任务报怪错时；踩过一遍不想再踩时 | BOM, CRLF, Tee-Object, UTF-16, PSScriptRoot, WindowsApps, 0x80070002, ACL, DPAPI | 51 | 3.6 | `14-gotchas-windows.md` |
| 15 | **security** | 改仓库可见性、处理备份、怀疑泄露、账号异常时 | token, 明文, DPAPI, .gitignore, private, LAN, 面板密码, 封号 | 37 | 2.8 | `15-security.md` |
| 16 | **decisions** | 纠结"该不该升级/该不该跟随社区/要不要重构"时 | 决策, 不追新, 自建通道, 不重构, 替代provider, 记录理由 | 50 | 4.4 | `16-decisions.md` |
| 17 | **history-shell** | 想知道"外壳为什么长这样"、某个设计（托盘/密码/LAN/看门狗）的来历时 | 外壳历史, 交接文档, 10-12, 10-19, 托盘, 看门狗, 面板密码, 工作区分身 | 36 | 2.7 | `17-history-shell.md` |
| 18 | **worklog** | 想知道"今天到底改了什么、为什么改、怎么回滚"时 | 2026-11-22, 接手, 备份, 重启, 推送, 测试, 261, GH007 | 44 | 2.5 | `18-worklog-2026-11-22.md` |
| 18 | **worklog-latest** | 想知道最近一轮做了什么、生产现在是什么版本 | 2026-11-23, 便携包, release, v1.10.9, 演练, 上线, 基线重录 | 29 | 2.2 | `18-worklog-2026-11-23.md` |
| 19 | **next-steps** | 准备继续做维护、排优先级时 | 待办, P1-4, 便携包, 更新通道, 外壳测试, 监控 | 53 | 3.3 | `19-next-steps.md` |
| 20 | **architecture** | 要改内核、理解一次请求怎么走、找某个功能在哪个模块时 | 模块, 请求链路, SSE, 账号池, 冷却, 端点, wb_proxy | 65 | 3.8 | `20-architecture.md` |
| 21 | **glossary** | 遇到看不懂的专有名词/缩写/自定义术语时 | 术语, 外壳, 载荷, 基线, 双轨, portable.txt, PATCHED-BY-OPS, egress | 31 | 2.8 | `21-glossary.md` |
| 22 | **release-pipeline** | 要发新版、打便携包、验证安装/回滚、排查"更新按钮没反应"时 | 打包, release, portable, SHA256SUMS, publish, run_update, 回滚, 8799, promote | 76 | 5.3 | `22-release-pipeline.md` |

## 3. 原始证据（大文件，按需读）

| 文件 | 体积 | 什么时候才读 |
|---|---|---|
| `reports\01-repo-analysis.md` | 27.1 KB | 要改仓库结构/CI/发布流程时（183 行） |
| `reports\02-shell-analysis.md` | 33.7 KB | 要动外壳、查它的模块与数据（318 行，最厚） |
| `reports\03-proxy-core.md` | 28.8 KB | 要动内核、查上游依赖面与硬编码位置（238 行） |
| `reports\04-history.md` | 26.8 KB | 要还原外壳开发史与未完成项（175 行） |
| `reports\05-ecosystem.md` | 21.9 KB | 要找替代源/社区镜像（22 KB） |
| `reports\06-source-provenance.md` | 9.1 KB | 要复核"我们的代码是不是原版"（94 行） |
| `reports\07-smoke-test.md` | 6.1 KB | 要复核冒烟门禁结果（62 行） |
| `reports\smoke-evidence\` | — | 要贴冒烟证据（日志 + 脱敏响应）时 |

## 4. 维护这份知识库的约定

| 约定 | 说明 |
|---|---|
| 一册一主题 | 文件名 `NN-topic.md`，`NN` 是两位序号，新增就往后排 |
| 头部四行 | `> 主题:` / `> 何时读:` / `> 关键词:`（生成器靠它填表） |
| 单册上限 | 目标 **≤150 行**；超了就拆册（比如把"故障"拆成"故障现象"和"故障处置"） |
| 不留悬空引用 | 提到别的文件一律写 `文件名`，不写绝对路径 |
| 事实要有出处 | 数字/结论尽量标注来源（哪个日志、哪次实测、哪份报告） |
| 权威副本 | 仓库 `maintenance\handbook\`（随 git 推送）；工作区这份是同一内容 |
| 更新时机 | 每次"踩新坑 / 改架构 / 改流程"后立即补，别攒 |


> 统计：共 23 册，合计约 1072 行 / 72.9 KB。
