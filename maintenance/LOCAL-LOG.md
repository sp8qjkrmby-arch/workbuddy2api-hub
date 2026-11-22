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

---

## 2026-11-22 20:30~20:5x 第三轮（重启验证 + 推送成功）

### 1. 重启结果 ✅

| 时刻 | 事件 |
|---|---|
| 20:30:02 | 任务开始，重启前 `/health` 200 |
| 20:30:03 | 停旧外壳（22260） |
| 20:30:07 | 8788 端口释放 |
| 20:30:10 | 新内核启动（29424），Job Object 已绑定 |
| 20:30:14 | **`/health` 200**，总耗时约 12 秒，实际断流 7 秒 |

- 新进程：外壳 20648 / 内核 29424；旧 22260 / 11632 已退出
- `shell.log`：`内核已就绪：账号 9 个（可用 7），版本 1.10.8` ← `read_core_version` 修复生效的直接证据
- `shell.json` 两项配置未被回写覆盖；一次性任务已自删
- 顺手修：重启脚本用 `Tee-Object` 在 PowerShell 5.1 下写 UTF-16LE，日志变二进制 → 改显式 UTF-8 追加

### 2. 推送 GitHub ✅

- 远端 `sp8qjkrmby-arch/workbuddy2api-hub`：`main` = `2136220`，tag `v1.10.8-local.2`（附注标签）与 `legacy-fork-v1.7.4` 均已推上
- **第一次 push 被 GitHub 拒（GH007）**：提交邮箱 `2359668069@qq.com` 在 GitHub 是私密邮箱，而仓库开了"阻止命令行推送暴露邮箱"。
  → 处置：`git config --local user.email = 242735504+sp8qjkrmby-arch@users.noreply.github.com`，用 `filter-branch --env-filter` 重写 `2fa6287..HEAD` 的 author/committer 邮箱（只重写我们这 6 个本地提交，641 条上游历史不动），并重建 tag。
- **凭据**：走 Git Credential Manager 的浏览器 OAuth，令牌存在 Windows 凭据管理器（DPAPI）。撤销：`git credential-manager github logout`
- 顺手修：`C:\Users\63648\.gitconfig` 里 `credential.helper` 路径**没加引号**，每次 push 都刷一行 `line 1: C:/Program: No such file or directory`。已改为带引号的完整路径。

### 3. 教训：`.cmd` 必须 CRLF + ASCII

用户双击 `push-to-github.cmd` 时出现 `'PROXY' 不是内部或外部命令` 与乱码——根因是**文件用 LF 换行**（cmd.exe 按行解析，LF 会把 `set HTTPS_PROXY=...` 截断成 `PROXY`），外加 UTF-8 中文在 GBK 控制台乱码。
→ 三个 `.cmd` 全部重写：**CRLF + 纯 ASCII + 用 `%~dp0` 代替中文字面路径**，并用 stub 验证过分行与分支。

### 4. 仍未做

P1-4 便携包、P1-5 全量测试首跑、P2-1 自建更新通道、账号真实可用性抽测。

---

## 2026-11-22 20:57~21:05 第四轮（全量测试首跑：261/261 全绿）

| 项 | 结果 |
|---|---|
| 命令 | `tests\run_all.py --jobs 8 --logs suite-logs` |
| 解释器 | `wb-proxy\python\python.exe`（CPython 3.12.14，对齐 CI 的 3.12 腿）+ node v24.18.0 |
| 首轮 | 260 passed / **1 failed** / 0 skipped，59~63 秒 |
| 唯一失败 | `_test_release_assets.py::test_every_module_is_listed`：`['wb_entitlements.py'] != []` |
| 根因 | `release/portable.txt`（打包白名单）里没有 `wb_entitlements.py`；我们为对齐生产补回了这个模块，但没登记进白名单 |
| 处置 | 在 `wb_egress.py` 之后按字母序插入 `wb_entitlements.py`（生产包里的 `release-manifest.json` 本来就有这条） |
| 复跑 | **261 passed / 0 failed / 0 skipped，59.9 秒** |
| 副作用 | 无：`PYTHONDONTWRITEBYTECODE=1`，套件全用临时目录，`check_clean_checkout.py` 通过；8788 全程未用 |

结论：这套代码**可改**——1 分钟就能验证有没有回归。测试重心在 usage/pricing/aggregate 后端；面板前端只有 DOM 桩；外壳的 31 个探针测试仍留在 `C:\Desktop\2api\tests\`，待迁入（P2-2）。

---

## 2026-11-23 04:46~05:0x 第五轮（A 便携包 + B 自建更新通道）

### A. P1-4 便携包 ✅

| 步骤 | 结果 |
|---|---|
| 取固定运行时 | CPython `3.12.15+20261003`，44.4 MB，sha256 与钉死值 `4b6f0bee…0f08` 一致 |
| 裁剪 | 528 文件，契约检查通过（61 必需文件在，17 项裁剪未回流） |
| 打包 | `dist\workbuddy2api-hub-v1.10.8.zip` 10.31 MB；包内 61 受管文件 + `python\` + manifest |
| 载荷校验 | 与生产基线 61 MATCH / 0 DIFF |
| 冒烟（包内自带 python，8799） | `/health` 200、22 模型、chat `PACKAGE_OK`、日志 0 错 |

### B. P2-1 自建更新通道 ✅

| 步骤 | 结果 |
|---|---|
| 代码改动 | 4 处仓库常量改指自维护仓库；版本统一 1.10.9；CHANGELOG/README/compose 同步 |
| 门 1 | 发版窗口：57 MATCH / 4 DIFF-ALLOWED / 3 MISSING-EXPECTED（新增 `-AllowChanged` 支持） |
| 门 2 | **261 passed / 0 failed / 0 skipped**（70.9 s） |
| 发布 | GitHub Release **v1.10.9**：zip 10.31 MB + SHA256SUMS，匿名可下（HEAD 200） |
| 克隆演练 | 8799 上 `run_update` → **INSTALL PASS**（11.8 s，自动备份 1.10.8）；`run_rollback` → **ROLLBACK PASS**（8.7 s） |
| 生产上线 | `promote-production.ps1` 脱离会话：停外壳 → `promote.py`（复用产品 `backup_current`/`apply_stage`）→ 起外壳 → 健康检查 = **PASS**，断流 ~11 秒 |
| 上线后 | 生产 manifest/`server_version` 均 1.10.9；真请求冒烟 `PACKAGE_OK`（1.48 s）；基线重录 `core-sha256.v1.10.9.txt` → 门 1 回到 **61 MATCH** |
| 更新检查 | 恢复 `check_update_on_launch=true`（通道可用）；launcher 两个默认值也已改指自维护仓库 |

### 本轮踩的两个坑（已修）

1. **manifest 位置**：便携包在 `<root>\release-manifest.json`，生产在 `wb-proxy\release-manifest.json`。第一次 promote 把 package 根传成 `wb-shell` → 立刻失败；**失败发生在改动之前**，外壳被自动拉起，生产无影响（04:53:49–04:54:00，仅一次短暂重启）。
2. **误杀演练实例**：停外壳的过滤器匹配过宽（`wb_proxy.py` 全路径），把 8799 的克隆实例一起杀了；已限定只匹配 `wb-shell` 路径。

### 备份与回滚锚点（本轮新增）

- 生产侧：`C:\Desktop\wb-shell\wb-proxy\updates\backup-1.10.9\`（61 个文件，由产品 `backup_current` 生成）
- 仓库侧：`maintenance\baseline\core-sha256.v1.10.9.txt`
- 发布物：`repo\dist\workbuddy2api-hub-v1.10.9.zip` + `SHA256SUMS`（dist 被 gitignore，随 Release 归档）

---

## 2026-11-23 05:19~05:3x 第六轮（C：外壳测试迁入）

### 分类与结果

旧目录 `C:\Desktop\2api\tests\` 共 **29 个文件**（25 py + 2 js + 2 ps1）。按"能否在仓库里无副作用运行"分档：

| 档 | 数量 | 处置 |
|---|---|---|
| headless（不起窗、不碰 8788、不写注册表） | 11 py（10 套件 + `snapshot_core.py` 辅助库） | ✅ **已迁入 `repo\shell\tests\`，10/10 全绿** |
| GUI 探针（真窗口、鼠标键盘、窗口名与生产同名） | 12 py + 1 ps1 | 留在旧目录，手工档（跑前告知、跑后 `kill_test_processes.py`） |
| 需 vendor / 注册表 / 真内核副本 | `tray_autostart_test.py`（PIL + HKCU Run）、`update_isolated_test.py`（18899 真内核） | 留旧目录；vendor 就绪后可再迁 |
| 内核看板前端脚本 | `bar_menu_test.js`、`collapse_sections_test.js` | 归内核侧，不属外壳 |
| 工具 | `grab_window.ps1`、`setup_vendor.ps1` | 不是测试；后者正好补上 `build_shell.ps1` 缺失的 vendor 依赖 |

### 布局适配（关键改动）

旧测试假设"测试目录就在外壳根下、内核在 `<shell>\wb-proxy`"。仓库布局是内核在根、外壳在 `shell\`。
每个迁移文件头部注入：

```python
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
```

并把 `os.path.join(_SHELL_DIR, "wb-proxy")` 换成 `_KERNEL_DIR`（共 2 处表达式，10 个文件）。

### 新增

- `repo\shell\tests\run-headless.ps1`：一条命令跑全部（自动挑解释器，支持 `-Python` / `-Only`）
- `repo\shell\tests\README.md`：三档分类、运行方法、适配说明、未迁原因

### 基线

**10 passed / 0 failed / 0 skipped**（约 20 秒）；不依赖第三方包、不需要管理员权限。

---

## 2026-11-23 05:4x 决定：跳过 P2-3 监控与告警

评估后**不做**常驻监控（5 分钟探活 / 小时巡检 / 通知 / 外部心跳 / 每日摘要全部不做）。
理由与"何时重新考虑"记在 `handbook\16-decisions.md` D9；`19-next-steps.md` 与计划书的 P2-3 已标注跳过。

替代保障（已在运行）：每日 04:00 备份、重启/升级脚本自带失败兜底与自检日志、三门验收。
