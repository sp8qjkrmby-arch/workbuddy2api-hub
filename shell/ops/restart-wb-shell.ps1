<#
.SYNOPSIS
    重启 wb-shell 生产实例（外壳 + 内核），并自证恢复。
.DESCRIPTION
    由一次性计划任务以「脱离当前会话」的方式调用，避免把正在使用网关的调用方一起打断。
    步骤：停外壳 → 停内核 → 等端口释放 → 用 启动.vbs 拉起 → 轮询 /health 直到 200 →
    复核 shell.json 关键项 → 抓 shell.log 里的内核版本行 → 写日志。
    失败时再尝试拉起一次，并把结果写进日志（不会静默失败）。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\restart-wb-shell.ps1 -OnceTaskName wb-shell-restart-once
#>
[CmdletBinding()]
param(
    [string]$ShellDir     = 'C:\Desktop\wb-shell',
    [string]$LogDir       = 'C:\Desktop\2api维护\logs',
    [string]$OnceTaskName = '',
    [int]$HealthTimeoutSec = 120
)

$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $LogDir "restart-$stamp.log"
function L([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    $line | Tee-Object -FilePath $log -Append | Out-Null
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8788/health' -TimeoutSec 4 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Get-Procs([string]$name, [string]$pattern) {
    Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern }
}

L "=== 重启开始（日志：$log）==="
L ("重启前 /health 200 = {0}" -f (Test-Health))

# 1) 停外壳（先停外壳，避免它的看门狗把内核又拉起来）
$launchers = Get-Procs 'pythonw.exe' 'launcher'
if (-not $launchers) { $launchers = Get-Procs 'python.exe' 'launcher' }
if ($launchers) {
    foreach ($p in $launchers) { L ("停止外壳 PID {0}" -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
} else { L '未找到运行中的外壳进程（可能已是停止状态）' }
Start-Sleep -Seconds 2

# 2) 停内核
$cores = Get-Procs 'python.exe' 'wb_proxy\.py'
foreach ($p in $cores) { L ("停止内核 PID {0}" -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $busy = Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue
    if (-not $busy) { break }
    Start-Sleep -Milliseconds 500
}
L ("端口 8788 已释放 = {0}" -f (-not (Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue)))

# 3) 拉起（与用户双击 启动.vbs 完全同一条路径）
function Start-Shell([int]$n) {
    L ("第 {0} 次拉起：wscript 启动.vbs --tray-only" -f $n)
    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') `
        -ArgumentList ('"' + (Join-Path $ShellDir '启动.vbs') + '" --tray-only') -WindowStyle Hidden
}

Start-Shell 1
$ok = $false
for ($i = 0; $i -lt $HealthTimeoutSec; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Health) { $ok = $true; L ("健康检查通过，耗时约 {0} 秒" -f ($i + 1)); break }
}
if (-not $ok) {
    L '第一次拉起后 120 秒内 /health 未恢复，重试一次'
    Start-Shell 2
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Health) { $ok = $true; L ("第二次拉起后恢复，耗时约 {0} 秒" -f ($i + 1)); break }
    }
}

# 4) 复核配置与版本
$cfg = $null
try { $cfg = Get-Content -LiteralPath (Join-Path $ShellDir 'data\shell.json') -Raw | ConvertFrom-Json } catch { L "读取 shell.json 失败: $($_ | Out-String)" }
if ($cfg) {
    L ("check_update_on_launch = {0}（期望 False）" -f $cfg.check_update_on_launch)
    L ("repo                    = {0}（期望 sp8qjkrmby-arch/workbuddy2api-hub）" -f $cfg.repo)
    L ("last_core_version       = '{0}'（期望 1.10.8，修复生效的证明）" -f $cfg.last_core_version)
}
$shellLog = Join-Path $ShellDir 'data\shell.log'
if (Test-Path -LiteralPath $shellLog) {
    L '--- shell.log 末尾 8 行 ---'
    Get-Content -LiteralPath $shellLog -Tail 8 -Encoding UTF8 | ForEach-Object { L ("    " + $_) }
}

# 5) 进程清点
foreach ($p in (Get-Procs 'pythonw.exe' 'launcher')) { L ("外壳 PID {0} 存活" -f $p.ProcessId) }
foreach ($p in (Get-Procs 'python.exe' 'wb_proxy\.py')) { L ("内核 PID {0} 存活" -f $p.ProcessId) }
L ("最终 /health 200 = {0}" -f (Test-Health))

# 6) 自删一次性任务
if ($OnceTaskName) {
    $null = schtasks /Delete /TN $OnceTaskName /F 2>&1
    L ("已删除一次性计划任务 {0}" -f $OnceTaskName)
}
L '=== 重启结束 ==='
