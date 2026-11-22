<#
.SYNOPSIS
    wb-shell 生产实例冷备份（只读源目录，不动生产）。

.DESCRIPTION
    备份 wb-shell 中「丢了就回不来」的东西：
      core-config/  accounts\ (账号凭据)、usage\ (用量账本)、catalog\ (模型目录快照)、pricing\pricing.json
      shell-state/  data\ 下的配置与状态（shell.json / update-state.json / panel-password.sealed /
                    baseline-core-sha256.txt / dashboard.patch.css）
    可选 -IncludeCode 额外打包 wb-proxy 的应用文件（*.py / dashboard.html / 启动脚本）与 launcher。
    每次备份生成 manifest.json（文件数 / 字节数 / SHA256），便于校验与恢复。
    默认保留最近 14 份，按目录名时间戳轮转。

.EXAMPLE
    pwsh -File scripts\backup-wb-shell.ps1
    pwsh -File scripts\backup-wb-shell.ps1 -IncludeCode -Keep 30
#>
[CmdletBinding()]
param(
    [string]$Source = 'C:\Desktop\wb-shell',
    [string]$Dest   = 'C:\Desktop\2api维护\backups',
    [int]$Keep      = 14,
    [switch]$IncludeCode,
    [string]$Label  = 'manual'
)

$ErrorActionPreference = 'Stop'
function Write-Step($m) { Write-Host "[backup] $m" }

if (-not (Test-Path -LiteralPath $Source)) { throw "源目录不存在: $Source" }
$cores = Join-Path $Source 'wb-proxy'
if (-not (Test-Path -LiteralPath $cores)) { throw "找不到内核目录: $cores（wb-shell 结构变了？）" }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out   = Join-Path $Dest ("wb-shell-$stamp-$Label")
New-Item -ItemType Directory -Force -Path $out | Out-Null
Write-Step "目标: $out"

# ---------- 1. 核心配置与账本 ----------
$coreCfg = Join-Path $out 'core-config'
New-Item -ItemType Directory -Force -Path $coreCfg | Out-Null
foreach ($item in @('accounts', 'usage', 'catalog', 'pricing')) {
    $src = Join-Path $cores $item
    if (Test-Path -LiteralPath $src) {
        Write-Step "复制 core/$item"
        Copy-Item -LiteralPath $src -Destination $coreCfg -Recurse -Force
    } else { Write-Warning "缺少 $src（跳过）" }
}

# ---------- 2. 外壳状态 ----------
$shellState = Join-Path $out 'shell-state'
New-Item -ItemType Directory -Force -Path $shellState | Out-Null
$dataDir = Join-Path $Source 'data'
if (Test-Path -LiteralPath $dataDir) {
    foreach ($f in @('shell.json', 'update-state.json', 'panel-password.sealed',
                     'baseline-core-sha256.txt', 'dashboard.patch.css', 'estimate-daily.json')) {
        $src = Join-Path $dataDir $f
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $shellState -Force }
    }
    # 配置历史快照（体积小，保留）
    $cb = Join-Path $dataDir 'config-backups'
    if (Test-Path -LiteralPath $cb) { Copy-Item -LiteralPath $cb -Destination $shellState -Recurse -Force }
} else { Write-Warning "缺少 $dataDir（跳过外壳状态）" }

# ---------- 3. 可选：代码 ----------
if ($IncludeCode) {
    $codeDir = Join-Path $out 'code'
    New-Item -ItemType Directory -Force -Path $codeDir | Out-Null
    Write-Step '打包内核代码 + 外壳代码'
    Copy-Item -Path (Join-Path $cores '*.py')       -Destination $codeDir -Force
    Copy-Item -Path (Join-Path $cores '*.bat')      -Destination $codeDir -Force -ErrorAction SilentlyContinue
    Copy-Item -Path (Join-Path $cores '*.command')  -Destination $codeDir -Force -ErrorAction SilentlyContinue
    Copy-Item -Path (Join-Path $cores '*.sh')       -Destination $codeDir -Force -ErrorAction SilentlyContinue
    foreach ($f in @('dashboard.html', 'README.md', 'LICENSE', 'release-manifest.json')) {
        $src = Join-Path $cores $f
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $codeDir -Force }
    }
    $shellOut = Join-Path $codeDir '_shell'
    New-Item -ItemType Directory -Force -Path $shellOut | Out-Null
    foreach ($f in @('launcher', '启动.vbs', 'build_shell.ps1')) {
        $src = Join-Path $Source $f
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination $shellOut -Recurse -Force
        }
    }
    # 不要备份 runtime\ (python 解释器) 和 launcher\vendor\ (34.5MB, 可由 build_shell.ps1 / vendor.py 重建)
    Remove-Item -LiteralPath (Join-Path $shellOut 'launcher\__pycache__') -Recurse -Force -ErrorAction SilentlyContinue
    $vendorDir = Join-Path $shellOut 'launcher\vendor'
    if (Test-Path -LiteralPath $vendorDir) {
        $vendorList = Get-ChildItem -LiteralPath $vendorDir -Recurse -File |
            ForEach-Object { [pscustomobject]@{
                path   = $_.FullName.Substring($vendorDir.Length + 1)
                bytes  = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } }
        $vendorList | ConvertTo-Json -Depth 3 |
            Set-Content -LiteralPath (Join-Path $shellOut 'launcher\vendor-MANIFEST.json') -Encoding UTF8
        Remove-Item -LiteralPath $vendorDir -Recurse -Force
        Write-Step "launcher\vendor 已替换为 vendor-MANIFEST.json（可重建，不占备份体积）"
    }
}

# ---------- 4. manifest ----------
$files = Get-ChildItem -LiteralPath $out -Recurse -File
$entries = foreach ($f in $files) {
    [pscustomobject]@{
        path   = $f.FullName.Substring($out.Length + 1)
        bytes  = $f.Length
        sha256 = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
    }
}
$manifest = [pscustomobject]@{
    created_at     = (Get-Date).ToString('s')
    source         = $Source
    include_code   = [bool]$IncludeCode
    label          = $Label
    file_count     = $entries.Count
    total_bytes    = ($entries | Measure-Object -Property bytes -Sum).Sum
    core_manifest  = (Test-Path (Join-Path $cores 'release-manifest.json'))
    files          = $entries
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $out 'manifest.json') -Encoding UTF8

$mb = [math]::Round((($entries | Measure-Object -Property bytes -Sum).Sum) / 1MB, 2)
Write-Step "完成: $($entries.Count) 个文件, $mb MB -> $out"

# ---------- 5. 轮转 ----------
$all = Get-ChildItem -LiteralPath $Dest -Directory -Filter 'wb-shell-*' | Sort-Object Name -Descending
if ($all.Count -gt $Keep) {
    $all | Select-Object -Skip $Keep | ForEach-Object {
        Write-Step "轮转删除旧备份: $($_.Name)"
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
}

# ---------- 6. 校验提示 ----------
Write-Host ''
Write-Host '恢复要点:'
Write-Host '  1) 停掉外壳（托盘退出）后再覆盖 accounts\ 与 usage\，否则运行中的进程会写回旧状态。'
Write-Host '  2) panel-password.sealed 绑定 Windows 用户 DPAPI，换用户名/换机后无法解密，需重设面板密码。'
Write-Host '  3) accounts\*.json 内含明文 accessToken/refreshToken，备份目录权限需按机密数据处理。'
