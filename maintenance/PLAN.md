# WorkBuddy2API 维护计划书

> 版本 v1.0 ｜ 制定日期 2026-11-22 ｜ 适用对象：`C:\Desktop\wb-shell` 生产实例 + `workbuddy2api-hub` 代码资产
> 事实来源：本仓库 `reports/01~05-*.md`（5 份实测报告，含日志/哈希/行号证据）+ 本机进程与接口实测。
> 本文件只写「怎么办」。所有结论都标注了证据位置，便于复核。

---

## 0. 一句话结论

**上游是"删号"不是"改名"**（作者账号与全部仓库 404 且无 301），**你手上的 v1.10.8 是干净的、能跑的孤本**（基线 64/64 哈希全对，未被本地改过）。
所以维护策略不是"从零接管一个 3 万行巨石"，而是四件事：

1. **先保命**：数据冷备份 + 冻结已经死掉的更新链路；
2. **再立传**：把生产 v1.10.8 源码与 fork 里的 146 套测试、CI、发布工具合成一条可信主干，打上自己的 tag；
3. **后修路**：把更新通道从"别人的 GitHub"改成"你自己的 GitHub"，并补上可回滚的安装演练；
4. **长期守**：真正的死因不在代码，而在**腾讯侧的账号与协议风控**（10-20 起指纹拦截 + 封号），代码维护必须与"通道续命/替代 provider"并行。

---

## 0.5 当前进度（2026-11-22 首次执行，已落地）

| 计划项 | 状态 | 实际落地 |
|---|---|---|
| P0-1 冷备份 + 日备 | ✅ | `scripts\backup-wb-shell.ps1`（实测 131 文件 / 19 MB）；Windows 计划任务 `wb-shell-daily-backup` 每日 04:00，已试跑 `LastTaskResult=0`；首份冷备份 `backups\wb-shell-20261122-194234-cold-0001\` |
| P0-2 冻结更新检查 | ✅ | `data\shell.json`：`check_update_on_launch=false`、`repo` 改指自己的 fork（**下次重启外壳生效**） |
| P1-1 取回 v1.10.8 源码 | ✅ | 来自 `M-strange/workbuddy2api-hub` tag `v1.10.8`；与生产基线比对 **60/64 逐字节一致、0 差异**，另 4 项已查清（构建产物 / 上游删但生产残留 / 本地 .bak） |
| P1-2 合并可信主干 | ✅（方式修订） | 不再"从 v1.7.4 增量补"，而是**直接用 v1.10.8 快照重建主干**（v1.7.4 缺 21 个顶层项，无法增量补齐），并把 `wb_entitlements.py` 从 `acc0b332` 取回补上 |
| P1-3 修三个小 bug | ◐ 1/3 完成 | `read_core_version` 已修并部署（底栏显示 1.10.8）；版本双轨、404 噪音待做 |
| P1-4 打包 + 旁路验证 | ✅ | 便携包 **10.31 MB**（61 受管文件 + CPython 3.12.15 + manifest），8799 冒烟 PASS；已发 **Release v1.10.9** 并装上生产 |
| P1-5 CI | ◐ 本地全绿 | **全量 261 passed / 0 failed / 0 skipped（59.9s）**；`check_clean_checkout` 通过；GitHub Actions 未启用 |
| P0-3 生产代码入库 | ✅ | 分支 `main` 两个提交 + tag `v1.10.8-local.1`；旧状态留存 tag `legacy-fork-v1.7.4` |
| P0-4 账号体检 | ⬜ 未开始 | 11-23 那批 token 到期就在明天 |

**新增资产（原计划未列）**：`maintenance/verify-trunk-baseline.ps1` —— 一条命令复验"仓库主干 == 生产二进制"，当前 `61 MATCH / 0 DIFF / 3 MISSING-EXPECTED`。这是防止未来任何改动悄悄跑偏的护栏。
**新增资产**：`maintenance/TEST-BASELINE.md`（测试基线、覆盖盲区、复现命令）。
**新增约束（来自仓库 `CONTRIBUTING.md` 的硬性门禁）**：提交前必须在隔离端口（8799）完成冒烟：服务能起、面板能开、**端到端真实上游请求成功**。已据此执行并留证 `reports/07-smoke-test.md`。

---

## 1. 现状基线（已逐项核实）


### 1.1 三份代码资产，别搞混

| 位置 | 内核版本 | 角色 | 关键证据 |
|---|---|---|---|
| `C:\Desktop\wb-shell` | **v1.10.8**（2026-11-03 装入） | **生产在跑** | `pythonw launcher --tray-only`(PID 22260) → `python wb_proxy.py --port 8788`(PID 11632)；`/health` 返回 200；`baseline-core-sha256.txt` 64 项全匹配、0 缺失 |
| `C:\Desktop\2api` | v1.7.12 | 源码 / 实验版 | 唯一保留 31 个外壳测试 + `tools/` + `.acl-recovery/`；端口 18788；最后活动 10-19；launcher 16 个 py 与生产 SHA256 全等（未分叉） |
| `C:\Desktop\2api维护\repo` | **v1.7.4**（提交 `2fa6287`，2026-10-14） | GitHub fork 克隆 | 641 提交、无任何 tag；**含 146 套测试 + 5 个 CI workflow + release/ 打包工具 + docs/CHANGELOG**，但代码比生产落后 v1.8→v1.10 三条线 |

> ⚠️ 最容易犯的错：拿 `repo/`（v1.7.4）当基线去重建。它缺 v1.8/v1.9/v1.10 全部演进，**基线必须是 v1.10.8**。
>
> **v1.7.4 → v1.10.8 的净增量**（`wb_*.py` 22 个 → **37 个**，新增 15 个）：
> `wb_codearts.py`、`wb_codearts_crypto.py`（华为云第二上游）、`wb_media.py`、`wb_media_client.py`、`wb_media_tasks.py`（多模态）、`wb_quota_scan.py`、`wb_tiers.py`、`wb_entitlements.py`、`wb_usage_io.py`（额度经济）、`wb_update_apply.py`、`wb_update_external.py`、`wb_update_portable.py`、`wb_update_request.py`（自更新四件套）、`wb_webprompts.py`、`wb_window_probe.py`。
> 演进方向很清晰：**多厂商 + 多模态 + 额度经济 + 无人值守**。

### 1.2 外部生态（原仓库消失后的替代源）

| 源 | 状态 | 可用性 |
|---|---|---|
| `ardeyouxipianyi/workbuddy2api-hub` + 作者账号 | 404 删除，**无 301**（改名会 301） | 不可恢复；`workbuddy2api-intl` 同样 404 |
| `M-strange/workbuddy2api-hub` | 115 个 tag，覆盖到 v2.2.0，**含 v1.10.8** | codeload 实测 200，可作源码基线首选；有便携 zip + apk/ipk + SHA256SUMS |
| `niyouqianma/workbuddy2api-hub` | 322★ / 124 fork，最新 v2.2.1（2026-11-21），CI 在跑 | 已把默认更新仓库改成自己；GHCR 镜像可拉（镜像内 `/app` 即完整源码） |
| `ghcr.io/ardeyouxipianyi/*` | 403 | 官方镜像已死 |

> fork 元数据里内嵌的 parent 对象仍显示 4019★/960 fork/11-13 push —— 那是 **GitHub 的过期缓存**，别被骗；子资源（releases/forks/refs）全部 404。

### 1.3 运行态与业务面

- 监听 `127.0.0.1:8788`，`/health` 200；`/v1/models` 需 Key（未带 Key 返回 401，符合预期）。
- 账号 9 个（全 `realm=cn`、`domain=www.codebuddy.cn`），**7 个启用**；token 到期分两批：**11-23**、12-18。
- 数据体量：`usage/` 14.28 MB（`usage.jsonl` 9.95 MB、`logs.sqlite3` 3.4 MB）、`accounts/` 0.58 MB（18 文件，token 明文）。
- 日志：`data\core-8788.log` 6.4 MB，**82 次启动中 38 次退出码 `0x40010004`（被外部终止）**，看门狗兜住了但根因未查；`data\shell.log` 0 Traceback / 126 ERROR / 185 WARN（主体是 404 噪音）。
- 加密：`panel-password.sealed` = Windows DPAPI `CryptProtectData`（用户作用域、无 entropy）→ **换机/换用户无法解密**，只能重设。

---

## 2. 五个关键判断（本计划的骨架）

| # | 判断 | 依据 | 影响 |
|---|---|---|---|
| D1 | **主干锁 v1.10.8，不追 v2.2.x** | 生产在跑的就是它，且二进制=可交叉验证的稳定态 | 升级到 v2.2.1 是"换心手术"，应作为独立项目评估，不混进重建 |
| D2 | **基线取 M-strange 的 v1.10.8 tag，不取 fork** | fork 是 v1.7.4 快照 | 只认**文件哈希**，不认版本号（见 R2 双轨坑） |
| D3 | **更新通道立刻冻结，恢复时必须指向自己的源** | 4 处硬编码指向已 404 仓库 | 现在只剩 WARN 噪音；一旦指向第三方仓库=把可执行代码执行权交给陌生人 |
| D4 | **社区只做参考源，不做跟随源** | 供应链风险 + 版本双轨 | 月度人工审阅 cherry-pick，不做自动更新 |
| D5 | **代码维护 ≠ 业务可用** | 上游域名活着但 10-20 起指纹拦截+封号 | 必须并行评估替代 provider，否则代码维护得再好也会断粮 |

---

## 3. 风险登记册

| ID | 风险 | 现状 | 影响 | 等级 | 缓解动作 |
|---|---|---|---|---|---|
| R1 | 更新链路已死 | launcher 每次检查都是 HTTP 404（`update-state.json` 最后检查 11-13，`latest_version 2.1.3` 取不到） | 无法升级、日志噪音 | 中 | P0-2 冻结 + P2-1 改指向 |
| R2 | **版本双轨** | release `v2.1.3` 包内 `release-manifest.json` 写的是 `1.10.8`；`validate_stage` 要求两者相等 | 面板 "安装更新" 即使仓库恢复也**必然失败** | 中 | P1-3 统一版本语义 |
| R3 | `read_core_version()` 只读文件前 400 000 字符，而 v1.10.8 版本串在偏移 **423 277** | 底栏显示"版本 ?"，`update-state.current_version` 为空 | 版本不可见→误判/误升级 | 低 | P1-3 修（改全量或倒序扫尾） |
| R4 | 账号断供 | 7/9 启用；token 11-23、12-18 两批到期；10-20 起官方按客户端指纹拦截并封号 | **业务直接中断** | **高** | P0-4 体检 + P3-4 替代 provider |
| R5 | 生产目录零测试网 | `wb-shell` 无 tests/tools；`build_shell.ps1` 引用的 `tests\setup_vendor.ps1` 在生产目录**不存在** | 外壳无法从生产目录重建/自检 | 中 | P0-3 快照入库 + P1-2 合并测试后从 `2api` 补 |
| R6 | 内核被外部终止 | 38/82 次 `0x40010004` | 偶发断流，看门狗掩盖根因 | 中 | P2-3 退出码审计 |
| R7 | 双巨石 | 生产 `wb_proxy.py` **23 535 行 / 1.16 MB**（v1.7.4 时代为 17 638 行 / 891 KB，三个月长 33%）；`dashboard.html` **1.07 MB / 18 279 行**（v1.7.4 时 674 KB）。`do_POST` 是上百条 `if path ==` 线性分发 | 任何改动都高冲突，CHANGELOG 已有 17 次 revert，故上游专设 revert-guard | 中 | 只做加法式改动 + P3-3 渐进抽模块 |
| R8 | 供应链 | 若把更新指向 `niyouqianma` 并保留自动安装 | 等于把可执行代码的下发权交给陌生人 | **高** | 自建 release（P2-1） |
| R9 | 数据资产裸奔 | `accounts/*.json` 明文 token；`usage.jsonl` 无归档上限 | 泄露/磁盘膨胀 | 中 | P2-4 |
| R10 | 文档漂移 | 交接文档章节乱序（0-8→11-13→9-10）；10-18 19:19 的 ACL 事故（缺 WRITE_OWNER）**完全未记录**；README §七缺 4 个配置键 | 新人踩坑 | 中 | P2-5 |

---

## 4. 工作分解

### P0｜止血（今天 ~ 48 小时，全部低风险、不动生产逻辑）

| 编号 | 任务 | 具体动作 | 验收标准 | 工时 |
|---|---|---|---|---|
| **P0-1** | 冷备份 + 日备 | 已备好 `scripts/backup-wb-shell.ps1` 并**实测跑通**：131 文件 / **19.02 MB**（含 `manifest.json` 全量 SHA256；`launcher\vendor` 34.5 MB 可重建，只存哈希清单）。再加任务计划：每日 04:00 `-IncludeCode -Keep 30` | `backups\wb-shell-*` 每日新增；`manifest.json` 可校验；恢复演练成功一次 | 0.5h |
| **P0-2** | 冻结更新检查 | `data\shell.json` → `check_update_on_launch: false`（保留 `update_check_hours` 设置项不删）；顺手把 `repo` 改成 `sp8qjkrmby-arch/workbuddy2api-hub` 只作占位 | `shell.log` 不再出现 releases 404；托盘不再弹更新 | 0.5h |
| **P0-3** | 生产代码入库 | 新建分支 `prod/v1.10.8`：把 `wb-shell\wb-proxy` 的 **60 个应用文件**（`release-manifest.json.files` 清单，含 37 个 `wb_*.py`）+ `launcher/` + `启动.vbs` + `build_shell.ps1` 提交（**排除** runtime/、vendor/、`__pycache__`、accounts/、usage/） | `git ls-tree` 能看到全部源码；`data\baseline-core-sha256.txt` 64 项能对齐 | 1h |
| **P0-4** | 通道体检 | 逐账号查 token 到期/可用性、出口 IP、日限额；标记 11-23、12-18 两批；记录到 `reports/06-account-audit.md` | 一张「账号 × 到期日 × 状态」表；知道哪些天会掉 | 1.5h |

### P1｜重建主干（本周，3~5 天）

| 编号 | 任务 | 具体动作 | 验收标准 |
|---|---|---|---|
| **P1-1** | 取回 v1.10.8 源码 | `curl -L codeload.github.com/M-strange/workbuddy2api-hub/zip/refs/tags/v1.10.8`（走代理）；**逐文件 SHA256 与生产 64 项基线比对** | 64/64 一致（若不一致，说明该 tag 不是同一构建，改用 niyouqianma 镜像 `/app` 提取） |
| **P1-2** | 合并为可信主干 | 以 P1-1 的应用文件覆盖 `repo/` 的应用层，**保留** `repo/` 的 `tests/`(146 套)、`.github/workflows/`(5 个)、`release/`、`scripts/`、`docs/CHANGELOG.md`；把 v1.10.8 新增的 **15 个模块**补进 `release/portable.txt` 白名单（漏登记打包会红） | `python tests/run_all.py --jobs 4 --logs suite-logs` 有明确通过/失败清单（失败项登记为 P2 遗留，不阻塞主干） |
| **P1-3** | 修三个已知小 bug | ① `launcher/service.py:84 read_core_version` 改全量/倒序扫描；② 版本双轨：统一"发布 tag"与"包内 version"语义（`release_tools.py` + `validate_stage`）；③ 404 噪音降级为 debug | 底栏显示 `1.10.8`；`update-state.current_version` 非空；`shell.log` 无 404 刷屏 |
| **P1-4** | 打自己的 tag 并出包 | `python release/release_tools.py` 生成便携包 + `release-manifest.json` + `SHA256SUMS`；tag `v1.10.8-local.1` | 包能在 **18788 旁路实例**启动且 `/health` 200；生产 8788 不受影响 |
| **P1-5** | 恢复 CI | 复用 `repo/.github/workflows/tests.yml`（3.9/3.12/win3.12 三腿）；fork 的 Actions 需手动启用；另加本地 `scripts\run-tests.ps1`（无网也能跑） | push 后 CI 绿；本地脚本一条命令跑完 |

### P2｜可运维化（2~3 周）

| 编号 | 任务 | 关键点 | 验收标准 |
|---|---|---|---|
| **P2-1** ✅ | 自建更新通道（2026-11-23 完成：4 处指向已改、Release 已发、克隆演练 INSTALL/ROLLBACK 均 PASS、生产已上线 v1.10.9） | 4 处硬编码：`launcher/updater.py:553`、`launcher/config.py:53`、`wb-proxy/wb_updates.py:35`、`wb-proxy/wb_update_portable.py:25`；另有 `WB_UPDATE_REPO` 环境变量旁路、`shell.json.update_mirror` 键可用。指向自己仓库的 release（或静态 JSON + zip） | 端到端装一次成功（**交接文档承认从未在生产跑过**），失败能回滚 |
| **P2-2** | 外壳纳入版本管理 | launcher 与内核的"契约"（要哪个 manifest 键、要哪个 CLI 参数、要哪个 /api 路径）写成 `docs/SHELL-CONTRACT.md`；`2api` 的 31 个测试迁回主干 | 契约文档 + 测试都在 `repo` 里 |
| **P2-3** | 监控与告警 | `/health` 探活、账号失效、5xx 率、`0x40010004` 事件计数；日志轮转已有（2 MB×3 / 8 MB×3），补"启动退出码审计" | 一份每日摘要（可推送到本对话/托盘） |
| **P2-4** | 数据治理 | `usage.jsonl` 按月归档并压缩；`logs.sqlite3` 定期 VACUUM；备份目录 ACL 收紧（含明文 token）；考虑备份用 DPAPI 加密 | 磁盘增长可预测；备份目录仅本用户可读 |
| **P2-5** | 文档重生 | 按本计划重写 README/交接文档；补录 10-18 ACL 事故（`.acl-recovery/` 有 21 条 JSONL 证据 + 回滚脚本）；`repo` 里 docker-compose 与 README 徽章仍指向上游，需改为自己仓库 | 新维护者 30 分钟能上手 |

### P3｜长期（持续）

| 编号 | 任务 | 说明 |
|---|---|---|
| P3-1 | **协议漂移跟踪** | 腾讯侧按客户端指纹拦截是最可能的死因（UA 硬编码 `5.8.4`）；建立"UA/接口变更→当日告警"机制，比追新版本更重要 |
| P3-2 | 社区月度巡视 | 只看 `M-strange` / `niyouqianma` 的 CHANGELOG 与 release notes，人工挑安全/风控类修复 cherry-pick，**不做整体升级** |
| P3-3 | 巨石渐进拆解 | 只在必须改动的区域抽函数/抽模块（如把 `do_POST` 的分发改成路由表），不改行为，每步有测试 |
| P3-4 | 替代 provider 评估 | 与代码维护并行：同一套 OpenAI 兼容层能否接别的上游（这是"不断粮"的唯一保险） |

---

## 5. 里程碑

| 时点 | 交付物 | 完成标志（DoD） |
|---|---|---|
| **D0（今天）** | 备份脚本 + 首份冷备份（已就绪）+ 更新冻结 | 每日备份生效；`shell.log` 无 404 |
| **D+3** | `repo` 有 `prod/v1.10.8` 与 `v1.10.8-local.1` 两个分支/tag | 生产 64 项哈希可对齐；包能在 18788 启动 |
| **W1** | 主干 + 测试 + CI 跑通 | `run_all.py` 出清单；CI 绿 |
| **W2** | 自建更新通道 + 端到端安装/回滚演练 | 一次成功安装 + 一次成功回滚，全程有据可查 |
| **W3~W4** | 监控、数据治理、文档重生 | 每日摘要可读；文档可用；备份可恢复 |
| **持续** | 月度：账号/协议巡检 + 社区巡视 | 每月一页巡检记录 |

---

## 6. 操作规程（SOP）

### 6.1 日常
- 启停：托盘退出（**不要**直接 kill 内核，`core_watchdog` 会在 6 秒内拉起来，这是设计行为）。
- 每日 04:00 自动备份（P0-1 的任务计划）。
- 每周看一眼：`data\shell.log` 尾部、`/health`、账号页的启用数与到期日。

### 6.2 升级内核（旁路验证，永不在生产目录试）
```powershell
# 1) 备份（必须）
pwsh -File C:\Desktop\2api维护\scripts\backup-wb-shell.ps1 -IncludeCode -Label before-upgrade
# 2) 旁路实例（换端口 + 换目录，用独立 accounts 副本）
C:\Desktop\wb-shell\runtime\python.exe C:\Desktop\wb-shell\wb-proxy\wb_proxy.py `
  --port 18788 --accounts-dir <临时副本> --usage-dir <临时目录>
# 3) 旁路跑通业务（/health 200 + 一次真实 chat）后再动生产
# 4) 回滚锚点：data\backups\core-<旧版本>-<时间>.zip（keep_backups=3）
```

### 6.3 回滚
停止外壳 → 用 `data\backups\core-*.zip` 或 `backups\wb-shell-*\` 覆盖 → 重新启动 → 确认 `baseline-core-sha256.txt` 全匹配。

### 6.4 故障速查

| 症状 | 先看这里 |
|---|---|
| 端口不通 | `Get-NetTCPConnection -LocalPort 8788`；内核日志尾部；`core_watchdog_retries=2` 是否耗尽 |
| 全部账号失败 | 上游风控（"没通过渠道校验"）；换 UA/出口；看 `limit-events.jsonl` |
| 底栏"版本 ?" | R3 已修则不会出现；未修时看 `data\shell.log` 的「内核已就绪：… 版本 x.y.z」 |
| 更新检查报错 | 已冻结（P0-2）；恢复需 P2-1 完成 |

---

## 7. 变更红线（不可做）

1. **不改** `accounts/` 与 `usage/` 的文件结构与语义（`release-manifest.json` 已把二者标 `protected`）。
2. **不在生产目录**（`C:\Desktop\wb-shell`）跑测试：探针测试会起真窗口、改注册表、真对话框，标题与生产进程同名，`kill_test_processes.py` 会误杀。
3. **不把更新指向第三方仓库**并保持自动安装（R8）。
4. **不删** `data\backups\core-*.zip`（唯一回滚锚点）。
5. 改 `wb_proxy.py` 前必须：先 commit 基线 → 改动限定在单一职责 → 至少跑相关套件。
6. 任何"顺手上调版本号"前，先对齐 R2 的双轨语义。

---

## 8. 需要你拍板的 5 件事（含推荐）

| # | 决策 | 选项 | 推荐 |
|---|---|---|---|
| D1 | 主干版本 | ① v1.10.8 就地维护 ② 追社区 v2.2.1 | **①**：生产已在跑、风险可控；②单独立"升级评估"项目 |
| D2 | 更新源 | ① 自建 release ② 指向 `niyouqianma` | **①**（先①，②只作为拉包时的临时手段） |
| D3 | 仓库归属 | ① 现有 fork 转私有 ② 新建私有仓库 | **①**：历史 641 提交有价值；但注意 fork 的 parent 已消失，需确认能否正常 push |
| D4 | 是否投人力重构巨石 | ① 不重构，只做加法式改动 ② 渐进抽模块 | **②的保守版**：只在必须改的地方抽，不强推 |
| D5 | 通道依赖 | ① 只维护代码 ② 同时准备替代 provider | **②**：R4 是唯一能让整个项目归零的风险 |

---

## 9. 本计划已为你落地的部分

- `C:\Desktop\2api维护\repo\` —— 已克隆 fork（641 提交，`upstream` remote 已尝试但确认为 404）。
- `C:\Desktop\2api维护\reports\01~05` —— 5 份实测报告（仓库结构 / 外壳 / 代理核心 / 历史交接 / 外部生态），本计划所有数字都出自这里。
- `C:\Desktop\2api维护\scripts\backup-wb-shell.ps1` —— 已实测：131 文件 / 19.02 MB / 带 SHA256 manifest，支持 `-IncludeCode` 与轮转；`launcher\vendor`（34.5 MB，可重建）改存为哈希清单。
- `C:\Desktop\2api维护\backups\wb-shell-20261122-194234-cold-0001\` —— 首份冷备份（含代码、账本、配置状态与 manifest）。

> 备份目录内含**明文 accessToken/refreshToken**，请按机密数据处理（建议：仅本用户可读 + 移动盘/加密卷副本）。

---

## 附录 A：目录语义地图

```
C:\Desktop\wb-shell\            ← 生产，勿从其它目录"整目录覆盖"
├─ 启动.vbs / build_shell.ps1   ← 外壳入口与构建（引用的 tests\setup_vendor.ps1 在生产目录缺失，R5）
├─ launcher\                    ← 自研外壳：__main__ / ui_web(143KB 面板) / updater / tray / service / paths
├─ runtime\                     ← 内嵌 CPython（pythonw.exe 跑外壳）
├─ wb-proxy\                    ← 内核 v1.10.8（62 个应用文件 + python\ 便携解释器）
│  ├─ accounts\  usage\         ← protected 数据，绝不被升级覆盖
│  └─ catalog\ pricing\ ops\    ← 模型目录 / 价格 / 阿里云更新器
└─ data\                        ← 外壳状态：shell.json / update-state.json / *.log / backups(回滚锚点)

C:\Desktop\2api\               ← 实验版（内核 1.7.12），唯一的外壳测试网，端口 18788
C:\Desktop\2api维护\           ← 本工作区：repo / reports / scripts / backups
```

## 附录 B：命令速查

```powershell
# 健康
(Invoke-WebRequest http://127.0.0.1:8788/health -UseBasicParsing).StatusCode
# 备份
pwsh -File C:\Desktop\2api维护\scripts\backup-wb-shell.ps1 -IncludeCode -Label manual
# 跑测试（在 repo 内，需 Python 3.12；本机 3.14 亦可先试）
python tests\run_all.py --jobs 4 --logs suite-logs; python scripts\check_clean_checkout.py
# GitHub 访问要代理（本机直连被重置）
$env:HTTPS_PROXY='http://127.0.0.1:7897'; $env:HTTP_PROXY='http://127.0.0.1:7897'
# 基线校验（64 项）
# 读 data\baseline-core-sha256.txt，逐文件 Get-FileHash 对比
```

## 附录 C：证据索引

| 结论 | 出处 |
|---|---|
| 生产 v1.10.8、基线 64/64、老 bug 与日志统计 | `reports/02-shell-analysis.md` |
| 更新链 404、版本双轨、4 处硬编码、账号/上游依赖面 | `reports/03-proxy-core.md` |
| 仓库结构、146 套测试、CI/发布门禁、双巨石 | `reports/01-repo-analysis.md` |
| 外壳 6 阶段历史、未完成 TODO、测试怎么跑 | `reports/04-history.md` |
| 原仓库删号、社区替代源、通道可持续性 | `reports/05-ecosystem.md` |
| v1.10.8 源码溯源与 60/64 逐字节比对、4 项缺失的解释 | `reports/06-source-provenance.md` |
| 主干冒烟门禁（隔离端口 8799，端到端真实回复） | `reports/07-smoke-test.md` + `reports/smoke-evidence/` |
| 基线复验（61 MATCH / 0 DIFF） | `repo\maintenance\verify-trunk-baseline.ps1` 输出 |
