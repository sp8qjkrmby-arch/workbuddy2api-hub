<#
.SYNOPSIS
    把生产内核切到 v1.10.9（停外壳 → 装载荷 → 起外壳 → 自证）。
.DESCRIPTION
    由调用方以「脱离会话」的方式启动（Start-Process），因为停 8788 会打断正在使用网关的调用方。
    步骤：
      1. 停外壳（Job Object 会带走内核），等 8788 释放
      2. 运行 promote.py（产品自带 backup_current/apply_stage，失败自动 restore）
      3. 无论成败都用 启动.vbs 把外壳拉起来
      4. 轮询 /health（最多 120 秒），复核版本与基线文件
    日志：C:\Desktop\2api维护\logs\promote-<时间戳>.log
#>
[CmdletBinding()]
param(
    [string]$ShellDir = 'C:\Desktop\wb-shell',
    [string]$LogDir   = 'C:\Desktop\2api维护\logs'
)

$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $LogDir "promote-$stamp.log"
$enc = New-Object System.Text.UTF8Encoding($true)

function L([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Write-Host $line
    [System.IO.File]::AppendAllText($log, $line + [Environment]::NewLine, $enc)
}

function Health { 
    try { return (Invoke-WebRequest 'http://127.0.0.1:8788/health' -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200 } catch { return $false }
}
function PortOpen { return [bool](Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue) }

function Start-Shell([string]$why) {
    L ("拉起外壳（$why）：wscript 启动.vbs --tray-only")
    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') `
        -ArgumentList ('"' + (Join-Path $ShellDir '启动.vbs') + '" --tray-only') -WindowStyle Hidden
}

L "=== 生产切换到 v1.10.9 开始 ==="
L ("切换前 /health 200 = {0}" -f (Health))

# 1) 停外壳
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'wb-shell' }
foreach ($p in $procs) { L ("停止 PID {0}" -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline -and (PortOpen)) { Start-Sleep -Milliseconds 500 }
if (PortOpen) { L '8788 未在 25 秒内释放，放弃切换（外壳未改动）'; Start-Shell '恢复'; exit 1 }
L '8788 已释放'

# 2) 装载荷
$py = Join-Path $ShellDir 'runtime\python.exe'
L '运行 promote.py…'
$out = & $py 'C:\Desktop\2api维护\.build\promote.py' 2>&1
$code = $LASTEXITCODE
foreach ($line in $out) { L ("    " + $line) }
L ("promote.py 退出码 = {0}" -f $code)

# 3) 无论如何把外壳拉起来
Start-Shell '切换后'
$ok = $false
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    if (Health) { $ok = $true; L ("健康检查通过，约 {0} 秒" -f ($i + 1)); break }
}
if (-not $ok) { L '120 秒内未恢复，再拉一次'; Start-Shell '重试'; Start-Sleep -Seconds 20; $ok = (Health) }

# 4) 复核
$cfgPath = Join-Path $ShellDir 'data\shell.json'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    L ("last_core_version = '{0}'" -f $cfg.last_core_version)
}
$man = Get-Content (Join-Path $ShellDir 'wb-proxy\release-manifest.json') -Raw | ConvertFrom-Json
L ("包内 manifest.version = {0}" -f $man.version)
$base = Join-Path $ShellDir 'data\baseline-core-sha256.txt'
if (Test-Path $base) { L ("基线文件头: " + ((Get-Content $base -TotalCount 2 -Encoding UTF8) -join ' | ')) }
$shellLog = Join-Path $ShellDir 'data\shell.log'
if (Test-Path $shellLog) {
    Get-Content $shellLog -Tail 6 -Encoding UTF8 | ForEach-Object { L ("    " + $_) }
}
L ("最终 /health 200 = {0}" -f (Health))
L ("RESULT = {0}" -f $(if ($ok -and $code -eq 0) { 'PASS' } else { 'FAIL' }))
L '=== 结束 ==='
