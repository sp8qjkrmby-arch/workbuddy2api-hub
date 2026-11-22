> 主题: paths
> 何时读: 找文件、判断"这份代码在哪、谁在跑、哪份是权威"时
> 关键词: 目录, wb-shell, 2api, repo, data, accounts, usage, launcher, maintenance

# 目录地图（四棵树，别搞混）

| 路径 | 角色 | 关键事实 |
|---|---|---|
| `C:\Desktop\wb-shell` | **生产在跑** | 外壳 + 内核 v1.10.8 + 内嵌 python + 数据 |
| `C:\Desktop\2api` | 旧实验/源码版 | 内核 **v1.7.12**，端口 18788，**唯一留存 31 个外壳测试**，10-19 后没再动 |
| `C:\Desktop\2api维护` | **本工作区** | 仓库克隆、报告、脚本、备份、日志、知识库 |
| GitHub `sp8qjkrmby-arch/workbuddy2api-hub` | 远端仓库 | 自维护主干，见 `11-git-workflow.md` |

## 1. 生产树 `C:\Desktop\wb-shell`

```
启动.vbs                双击入口（WScript 隐藏窗口拉起 pythonw launcher）
build_shell.ps1         外壳构建脚本（⚠ 引用的 tests\setup_vendor.ps1 在生产目录不存在）
app.ico / tray.ico      图标
README.md               外壳说明（42 KB）
交接文档.md              上一轮交接记录（77 KB，章节乱序，见 17-history-shell.md）
launcher\               自研外壳（16 个 py，见下表）
runtime\                内嵌 CPython 3.14.4（pythonw.exe 跑外壳）
wb-proxy\               内核 v1.10.8 + 数据（protected：accounts / usage）
data\                   外壳状态与日志（见 08-config-reference.md / 09-logs.md）
```

`launcher\` 模块分工：

| 文件 | 作用 |
|---|---|
| `__main__.py` (50 KB) | 入口、单实例、托盘协调、`--tray-only` |
| `ui_web.py` (143 KB) | 面板前端宿主（内嵌页面 + JS 桥） |
| `updater.py` (61 KB) | 更新检查/下载/安装（默认仓库硬编码在此，见 13-known-issues.md） |
| `tray.py` | 托盘菜单、建窗、设置项 |
| `service.py` (32 KB) | 内核进程管理 + 看门狗 + `read_core_version()` |
| `paths.py` / `config.py` | 路径解析 / 配置读写（`shell.json`） |
| `proc.py` / `autostart.py` | 进程工具 / 开机自启 |
| `secret_store.py` | DPAPI 密封面板密码 |
| `lanshare.py` | LAN 分享 |
| `clipboard.py` / `ui_native.py` / `logging_setup.py` / `vendor.py` / `kill_test_processes.py` | 杂项 |

## 2. 内核数据 `wb-shell\wb-proxy\`

| 子路径 | 内容 | 备注 |
|---|---|---|
| `accounts\*.json` | 账号（**明文 accessToken/refreshToken**） | git 忽略 |
| `accounts\settings.json` | **配置真源**：面板密码哈希、API Key、限额 | 5.4 KB |
| `accounts\model.json` | 模型目录缓存（100 KB 级） | 运行时刷新 |
| `usage\usage.jsonl` | 用量账本（≈9.95 MB） | 只追加 |
| `usage\logs.sqlite3` | 日志库（≈3.4 MB） | |
| `usage\usage-aggregate-cache-*.json` | 增量聚合缓存（5 类） | 可重建 |
| `catalog\product-config-cn.json` / `-intl.json` | 产品配置 | 运行时会更新 |
| `pricing\pricing.json` | 价格表 | |
| `ops\aliyun\` | 阿里云更新器（含 `xybzljc` 残留引用） | 未使用 |
| `python\` | 便携 Python 3.12.14 | 跑测试用 |

## 3. 工作区 `C:\Desktop\2api维护`

```
repo\                  git 仓库（= 自维护主干，权威代码）
handbook\              ★ 本知识库（字典式，每个主题一册）
reports\               原始证据：01~07 + smoke-evidence\（按需读，勿全读）
scripts\               备份 / 重启 / 推送助手
backups\               冷备份（wb-shell-*）与 prod-files\（部署前的原件）
logs\                  run_all 测试日志、restart 日志
sources\               M-strange-v1.10.8 快照（溯源用，可删）
维护计划书.md           计划（= repo\maintenance\PLAN.md）
执行记录.md             实际改动流水（= repo\maintenance\LOCAL-LOG.md）
```

## 4. 仓库 `repo\` 布局

```
/                    上游应用载荷（37 个 wb_*.py、dashboard.html、pricing、catalog、ops、tools）
tests\               276 个测试文件（146 套件）
.github\workflows\   5 个 CI（tests / release / release-checksums / docker-publish / revert-guard）
release\             打包工具（portable.txt 白名单 = 更新器契约）
docs\CHANGELOG.md    1104 行变更史
shell\               自研外壳源码（launcher 16 模块 + 启动.vbs + build_shell.ps1 + ops\ 脚本）
maintenance\         基线、校验脚本、PLAN、LOCAL-LOG、TEST-BASELINE、handbook\
```
