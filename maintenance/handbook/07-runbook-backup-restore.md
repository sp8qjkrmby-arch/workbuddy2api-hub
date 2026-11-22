> 主题: backup
> 何时读: 要做/恢复/校验备份、换机迁移、怀疑丢了数据时
> 关键词: 备份, 恢复, 计划任务, DPAPI, 明文token, 冷备份

# 备份与恢复

## 备份什么（丢了回不来的东西）

| 内容 | 位置 | 能不能重建 |
|---|---|---|
| 账号凭据 | `wb-proxy\accounts\*.json`（含 settings.json） | ❌ 只能重新登录 |
| 用量账本 | `wb-proxy\usage\usage.jsonl` + `logs.sqlite3` | ❌ 历史不可重建 |
| 外壳配置/状态 | `data\shell.json`、`update-state.json`、`panel-password.sealed`、`baseline-core-sha256.txt` | 部分可重建（密码密封件不可） |
| 代码 | `wb-proxy\*.py` + `launcher\` | ✅ 已在 git 里 |

## 现役脚本

`C:\Desktop\2api维护\scripts\backup-wb-shell.ps1`（仓库镜像：`shell\ops\`）

```powershell
# 手动
powershell -ExecutionPolicy Bypass -File C:\Desktop\2api维护\scripts\backup-wb-shell.ps1 -IncludeCode -Label manual

# 计划任务（已注册并试跑通过）
schtasks /Query /TN "wb-shell-daily-backup" /FO LIST
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Source` | `C:\Desktop\wb-shell` | 只读源 |
| `-Dest` | `C:\Desktop\2api维护\backups` | 输出 |
| `-Keep` | 14 | 保留份数（任务里用 30） |
| `-IncludeCode` | 关 | 额外打包内核 *.py + dashboard + 外壳源码 |
| `-Label` | `manual` | 目录名后缀 |

产物：`backups\wb-shell-<时间戳>-<label>\`
```
core-config\   accounts\ usage\ catalog\ pricing\
shell-state\   shell.json update-state.json panel-password.sealed
               baseline-core-sha256.txt dashboard.patch.css config-backups\
code\          *.py dashboard.html README LICENSE release-manifest.json  _shell\launcher
manifest.json  全量 SHA256（可在恢复后校验）
```
- 体积：**约 19 MB / 131 文件**（`launcher\vendor` 34.5 MB 可重建，只存 `vendor-MANIFEST.json` 哈希）。
- 计划任务：每日 **04:00**，`-IncludeCode -Keep 30`，用**系统** `powershell.exe` 全路径（见 `14-gotchas-windows.md`）。

## 恢复要点（顺序很重要）

1. **先停外壳**（托盘退出），否则运行中的进程会把旧状态写回去；
2. 覆盖 `wb-proxy\accounts`、`wb-proxy\usage`、`data\` 对应文件；
3. 启动，看 `/health` 200 + `shell.log` 有"内核已就绪"；
4. 用 `manifest.json` 抽样核对文件哈希。

## 三个硬约束

| 约束 | 原因 |
|---|---|
| `panel-password.sealed` 只能在本机本用户解密 | DPAPI 用户作用域、无 entropy。换机/换用户 → **只能重设面板密码** |
| 备份目录按机密数据对待 | `accounts\*.json` 是**明文** token；`manifest.json` 只有哈希不含密文 |
| 内核回滚锚点别删 | `data\backups\core-*.zip` 是外壳升级前自动留的（`keep_backups=3`） |

## 回滚手段一览

| 想撤什么 | 怎么做 |
|---|---|
| 配置改动 | 用 `backups\*\shell-state\shell.json` 覆盖 |
| 内核版本 | `data\backups\core-<旧版本>-<时间>.zip` 解压覆盖 `wb-proxy` |
| 计划任务 | `schtasks /Delete /TN "wb-shell-daily-backup" /F` |
| 仓库改动 | `git reset --hard <tag>`（本地未推的）/ `git revert`（已推的） |
