# 维护执行记录

> 记录"对这台机器实际做了什么"，与 `维护计划书.md`（计划）分开。按时间倒序。
> 原则：任何改动都能回滚；生产实例 8788 全程未停、未改代码。

---

## 2026-11-22 首次执行（P0 止血 + P1 主干重建）

### 1. 备份（P0-1）✅

| 项 | 内容 |
|---|---|
| 脚本 | `scripts\backup-wb-shell.ps1`（UTF-8 **带 BOM**，兼容 PowerShell 5.1 与 7） |
| 内容 | `core-config\`（accounts / usage / catalog / pricing）+ `shell-state\`（shell.json / update-state.json / panel-password.sealed / baseline-core-sha256.txt 等）+ 可选 `code\`（内核 *.py、dashboard、launcher 源码） |
| 体积 | 131 文件 / 19.02 MB（`launcher\vendor` 34.5 MB 可重建，改存 `vendor-MANIFEST.json` 哈希清单） |
| 计划任务 | `wb-shell-daily-backup`，每日 04:00，`-IncludeCode -Keep 30`，**已试跑 `LastTaskResult=0`** |
| 首份备份 | `backups\wb-shell-20261122-194234-cold-0001\` |
| ⚠️ 注意 | 备份含**明文 accessToken/refreshToken**；`panel-password.sealed` 绑 DPAPI，换机/换用户无法解密 |

> 计划任务用 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`（稳定路径）而不是 Store 版 `pwsh`——后者路径随商店更新会变，任务会 `0x80070002` 失败（已实测踩过）。

### 2. 冻结失效的更新链路（P0-2）✅（下次重启外壳生效）

`C:\Desktop\wb-shell\data\shell.json`：
- `check_update_on_launch`: `true` → **`false`**（原仓库 404，每 6 小时一次无效请求）
- `repo`: `ardeyouxipianyi/workbuddy2api-hub` → **`sp8qjkrmby-arch/workbuddy2api-hub`**（指向自己的仓库，占位）
- 未改：`update_check_hours=6`、`update_mirror=""`、看门狗、端口等

> 说明：外壳进程在跑时以内存中的配置为准，文件改动**要等下次启动外壳**才生效；届时应确认这两项没被面板回写覆盖。

### 3. 重建可信主干（P1-1 / P1-2 / P0-3）✅

仓库 `C:\Desktop\2api维护\repo`：

| 提交 / tag | 内容 |
|---|---|
| `legacy-fork-v1.7.4`(tag) | 原 fork 状态（641 提交，v1.7.4 快照），保留可查 |
| `9340aec` | `vendor: 同步上游载荷至 v1.10.8`：整套载荷 + tests(276) + CI(5) + release/ + docs 替换为 v1.10.8 |
| `2a4d9db` | `feat(maintenance): 纳入自研外壳、基线与校验工具` |
| `v1.10.8-local.1`(tag) | 我们的第一个自维护锚点 |

- 源码来源：`M-strange/workbuddy2api-hub` tag `v1.10.8`（社区留存镜像）。
- 一致性：与生产 `baseline-core-sha256.txt` 比对 **60/64 逐字节一致、0 差异**；4 项缺失已查清（`release-manifest.json` 构建产物、`wb_entitlements.py` 从 `acc0b332` 取回后哈希吻合、2 个 `.bak` 为本地备份）。
- 新增 `wb_entitlements.py`：上游 10-20 加、10-22 删，但 11-03 发布的包里仍残留，生产在跑它。
- 新增 `maintenance\verify-trunk-baseline.ps1`：复验结果 **61 MATCH / 0 DIFF / 3 MISSING-EXPECTED**，退出码 0。

### 4. 冒烟门禁（CONTRIBUTING.md 硬性要求）✅

隔离端口 **8799** + 独立 accounts/usage 副本：

- 服务：`/health` 200，日志 Traceback/ERROR/Exception 全 0
- 面板：`GET /` 200、1,071,023 B、7 个关键 DOM id 命中、`/settings` 未登录 401
- 端到端：`POST /v1/chat/completions` **200、2.10 s、`content:"SMOKE_OK"`、49 tokens**（真实上游回复）
- 生产 8788（PID 11632）全程未触碰；8799 已杀、临时目录（含账号副本）已删
- 证据：`reports\07-smoke-test.md`、`reports\smoke-evidence\`

### 5. 本轮未做（留给下一轮）

- P1-3 三个小 bug（`read_core_version` 只读 40 万字符 / 版本双轨 / 404 噪音降级）
- P1-4 便携包生成、P1-5 CI 与 `run_all.py` 首跑
- P0-4 账号体检（**11-23 有一批 token 到期**）
- 推送到 GitHub：本机没有 `gh` CLI，HTTPS push 需要 PAT/SSH key（有 `~/.ssh/id_rsa`，但未验证能否用）。当前所有成果都在本地仓库，`origin` 仍指向自己的 fork。

### 6. 回滚办法

| 想撤什么 | 怎么做 |
|---|---|
| 撤销配置改动 | 用 `backups\wb-shell-*-cold-0001\shell-state\shell.json` 覆盖回去 |
| 撤销计划任务 | `schtasks /Delete /TN "wb-shell-daily-backup" /F` |
| 撤销仓库改动 | `git reset --hard legacy-fork-v1.7.4`（本地仓库，未推送过） |
| 生产内核回滚 | `C:\Desktop\wb-shell\data\backups\core-*.zip`（外壳自带 keep_backups=3 的锚点） |

---

## 2026-11-22 20:2x 第二轮（修 bug + 重启 + 推送准备）

### 1. 修 `read_core_version`（P1-3 ①）✅

- 根因：只读 `wb_proxy.py` 前 400000 字符，而 v1.10.8 的 `version` 在偏移 **423277**。
- 改法：全文读取（上限 8 MB，仍有 `(mtime,size)` 缓存兜底）。
- 验证：生产 runtime 直接调用 → 修复前 `''`，修复后 `'1.10.8'`；`py_compile` 通过。
- 部署：已覆盖 `C:\Desktop\wb-shell\launcher\service.py`；原文件备份 `backups\prod-files\service.py.orig-20261122`。
- 生效：需重启外壳 → 已登记一次性任务 `wb-shell-restart-once`（20:27），跑完自删。

### 2. 重启流程脚本化 ✅

`scripts\restart-wb-shell.ps1`（同时入库 `shell/ops/`）：停外壳 → 停内核 → 等 8788 释放 → `wscript 启动.vbs --tray-only` → 轮询 `/health` 最长 120 秒（失败再拉一次）→ 复核 `shell.json` 关键项 → 抓 `shell.log` 尾部 → 自删一次性任务。日志写 `logs\restart-<时间戳>.log`。

### 3. 推送 GitHub：已就绪，待授权 ⏳

- 仓库：`sp8qjkrmby-arch/workbuddy2api-hub`（你的 fork），本地 3 个提交 + 2 个 tag 待推。
- 障碍：本机没有 `gh` CLI；`~/.ssh/id_rsa` **未被 GitHub 接受**（`Permission denied (publickey)`）；HTTPS 无已存凭据。
- 已修：系统级 `credential.helper` 路径没加引号（`C:/Program Files/...` 被当命令截断），已在本仓库用 `--local` 覆盖为带引号的完整路径。
- 两种授权方式：
  1. 双击 `scripts\push-to-github.cmd` → 弹出浏览器授权 → 自动 push main + tags；
  2. 或者建一个 PAT（fine-grained，仅该仓库 Contents: Read and write），然后 `git remote set-url origin https://<TOKEN>@github.com/sp8qjkrmby-arch/workbuddy2api-hub.git`。
- ⚠️ 建议把该 fork 设为 **private**：仓库里现在有 `shell\交接文档.md`（记录了你的目录结构、面板密码机制、备份策略）与 `reports` 里的环境细节。公开也无密钥泄露（已扫描），但没必要公开。
