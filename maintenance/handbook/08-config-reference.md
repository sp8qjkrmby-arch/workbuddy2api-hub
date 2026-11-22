> 主题: config
> 何时读: 改配置、解释某个开关、排查"为什么设置没生效"时
> 关键词: shell.json, settings.json, 端口, 看门狗, 更新检查, 面板密码, PBKDF2, 限额

# 配置参考

## 1. 外壳配置 `C:\Desktop\wb-shell\data\shell.json`

**启动时读取**；改完要重启外壳（见 `06-runbook-start-stop.md`）。

| 键 | 当前值 | 说明 |
|---|---|---|
| `accounts_dir` / `usage_dir` | `null` | null = 用 `core_dir` 下的默认 |
| `api_key` | `null` | 外壳自己持有的一份 key（一般不用） |
| `auto_start_core` | `true` | 外壳起来自动拉内核 |
| `autostart` | `false` | 开机自启（在托盘设置里改） |
| **`check_update_on_launch`** | **`false`**（2026-11-22 我们改的） | 启动时检查更新。原为 true，但仓库已 404，每次刷 404 |
| `close_to_tray` | `true` | 关窗即最小化到托盘 |
| `config_version` | `1` | 配置结构版本 |
| `core_dir` | `""` | 空 = 默认 `wb-shell\wb-proxy` |
| `core_watchdog` | `true` | 内核挂了自动重启 |
| `core_watchdog_retries` | `2` | 连续重试次数 |
| `host` | `127.0.0.1` | 只绑回环（`--lan` 才对外） |
| `keep_backups` | `3` | 升级前保留几份 `core-*.zip` |
| `lan` | `false` | LAN 分享开关 |
| `last_core_version` | `""` | 外壳记录的内核版本（修复后应为 `1.10.8`） |
| `panel_password` / `panel_password_remember` | `null` / `true` | 面板密码自动登录（密文另存 sealed 文件） |
| `port` | **8788** | 内核端口 |
| **`repo`** | **`sp8qjkrmby-arch/workbuddy2api-hub`**（我们改的） | 更新检查用的 GitHub 仓库 |
| `runtime_dir` | `""` | 空 = 默认 `wb-shell\runtime` |
| `start_minimized` | `false` | 启动即最小化 |
| `tray_enabled` | `true` | 托盘 |
| `ui` | `auto` | 界面模式 |
| `update_check_hours` | `6` | 检查间隔（`check_update_on_launch=false` 后仍保留该设置项） |
| `update_mirror` | `""` | 更新镜像 URL（自建通道可用，见 `19-next-steps.md`） |
| `window_geometry` | `1543x800` | 窗口大小记忆 |

## 2. 内核配置 `wb-proxy\accounts\settings.json`（**唯一配置真源**）

| 内容 | 说明 |
|---|---|
| 面板密码 | PBKDF2 哈希，`rounds=120000`；已从默认 `admin` 改过 |
| API Key 列表 | 客户端鉴权用（`/v1/*` 需要 Bearer） |
| 限额/优先级 | 日限额、Key 级额度、模型优先级等 |
| 地区/产品 | realm 选择、产品切换 |

> 这个文件同时是**面板"设置"页的后端存储**，改动请走面板或先停服务，避免被覆盖。

## 3. 内核命令行参数（11 个）

`--info` `--host` `--port` `--lan` `--api-key` `--system-prompt` `--user-agent` `--usage-dir` `--accounts-dir` `--import-desktop` `--panel-password`

`--lan` 会绑 `0.0.0.0` 并**强制生成 API Key**（安全默认：只绑回环）。

## 4. 相关环境变量

| 变量 | 作用 |
|---|---|
| `WB_UPDATE_REPO` | 覆盖内核更新仓库（`wb_updates.py` 支持，旁路硬编码） |
| `WB_SHELL_DIR` | 外壳测试用的目录重定向（旧测试目录里用） |
| `PYTHONDONTWRITEBYTECODE` | 跑测试时设 1，避免仓库里生成 `__pycache__` |

## 5. 默认值偏松的地方（安全提醒）

- 面板默认密码是 `admin`（本机已改）。
- `?pwd=` / `?key=` 可出现在 URL 里（日志/截图会带出去）。
- `--lan` 一旦开启就是 `0.0.0.0` 暴露面。
- 账号 token 明文落盘（见 `15-security.md`）。
