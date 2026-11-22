> 主题: gotchas
> 何时读: 写脚本/批处理/计划任务报怪错时；踩过一遍不想再踩时
> 关键词: BOM, CRLF, Tee-Object, UTF-16, PSScriptRoot, WindowsApps, 0x80070002, ACL, DPAPI

# Windows / PowerShell 坑集（每条都真踩过）

## 1. `.ps1` 含中文 → 必须 UTF-8 **带 BOM**

Windows PowerShell 5.1 读无 BOM 文件按 ANSI(GBK) 解释，中文变乱码后**语法直接炸**：

```
Unexpected token ')' in expression or statement.
The string is missing the terminator: '.
```

修法：写成 UTF-8 **with BOM**（`New-Object System.Text.UTF8Encoding($true)`）。这样 5.1 和 7 都能读。

## 2. `.cmd` 必须 CRLF + 纯 ASCII

cmd.exe 按行解析，**LF 换行会把一行截断**：

```bat
set HTTPS_PROXY=http://127.0.0.1:7897     ← 被切成 "set HTTPS_" + "PROXY=…"
→ 'PROXY' 不是内部或外部命令
```

再加 UTF-8 中文进 GBK 控制台 → 乱码一片。
修法：CRLF + 纯 ASCII 提示语 + 中文路径用 `%~dp0` 拼；`.gitattributes` 钉 `*.cmd text eol=crlf`。

## 3. `Tee-Object -FilePath` 在 5.1 下写 UTF-16LE

日志变"二进制"、`Get-Content` 一片乱码、grep 不到。
修法：`[System.IO.File]::AppendAllText($log, $line + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($true)))`。

## 4. `$PSScriptRoot` 在参数默认值里可能是空的（5.1）

`param([string]$Root = (Split-Path -Parent $PSScriptRoot))` → `Cannot bind argument to parameter 'Path' because it is an empty string.`
修法：默认值留空，脚本体里再算：
```powershell
$here = $PSScriptRoot; if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
```

## 5. 计划任务用 Store 版 `pwsh` 会 `0x80070002`

`pwsh` 在 `C:\Program Files\WindowsApps\Microsoft.PowerShell_<版本>_x64__...\pwsh.exe`，**商店更新后路径变**，任务随即失败（`LastTaskResult=2147942402` = 文件找不到）。
修法：计划任务固定用 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`（系统自带、路径稳定），并把脚本写成 5.1 兼容。

## 6. git `credential.helper` 路径必须加引号

路径含空格且不加引号 → sh 按空格切词：
```
git-credential-manager.exe store: line 1: C:/Program: No such file or directory
git: 'credential-C:/Program Files/Git/mingw64/bin/git-credential-manager.exe' is not a git command
```
修法：删掉坏覆盖，用系统级 `credential.helper=manager`。

## 7. GitHub 的 GH007（私密邮箱）

见 `11-git-workflow.md` §1。核心：提交邮箱改 `noreply`，用 `filter-branch` 只重写自己那几条。

## 8. ACL 事故（2026-10-18 19:19）

`C:\Desktop\2api` 曾缺 `WRITE_OWNER` 权限，导致写操作被拒；当时加了 FullControl ACE 并留了 DACL 备份与回滚脚本（证据在 `C:\Desktop\2api\.acl-recovery\`，21 条 JSONL，`verification: verified`）。
**这条事故在 `交接文档.md` 里完全没记录**——遇到"明明有权限却写不进去"，先想它。

## 9. DPAPI 密封件不可迁移

`data\panel-password.sealed` = `CryptProtectData` 用户作用域、**无 entropy**。
换 Windows 用户 / 换机器 → 解不开 → 只能重设面板密码（面板里"忘记密码"）。

## 10. 中文路径的连带问题

- `.cmd` 里避免出现中文（用 `%~dp0`）；
- 某些老工具/打包器对非 ASCII 路径处理不佳，`repo` 放在 `C:\Desktop\2api维护\` 下尚可，但**别再把路径加长或加中文**。

## 11. 一次 push 会打印两行 helper 错误（已修）

系统级配置 `manager` + 全局那边曾被加了一条无引号路径，两者叠加导致每次 `git push` 先报两行错误再成功。现在只剩系统级 `manager`，干净。
