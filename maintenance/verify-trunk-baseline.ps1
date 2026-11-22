<#
.SYNOPSIS
    校验本仓库的主干载荷是否与生产内核 v1.10.8 逐字节一致（基线回归）。

.DESCRIPTION
    读取 maintenance\baseline\core-sha256.v1.10.8.txt（64 条，来自生产实例
    C:\Desktop\wb-shell\data\baseline-core-sha256.txt），对仓库根目录下同名文件重算 SHA256。
    预期结果（v1.10.8 生产基线 vs 当前主干）：发新版时允许的 4 个文件走 DIFF-ALLOWED，其余必须 MATCH。

    例外清单（EXCEPTIONS）里给出的 3 个文件不可能出现在 git 主干中：
      release-manifest.json        构建产物，由 release/release_tools.py 从 portable.txt 生成
      wb_proxy.py.bak-*            生产环境本地手工备份，从未入库

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$Baseline,
    # 允许"已声明"的变更：发新版时版本串必然与上一版基线不同
    [string[]]$AllowChanged = @()
)

$ErrorActionPreference = 'Stop'

# 兼容 Windows PowerShell 5.1：$PSScriptRoot 在参数默认值求值阶段可能还不可用
$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Root)     { $Root = Split-Path -Parent $here }
if (-not $Baseline) { $Baseline = Join-Path $here 'baseline\core-sha256.v1.10.8.txt' }

# -AllowChanged 支持逗号分隔或空格分隔
$AllowChanged = @($AllowChanged | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

$expectMissing = @(
    'release-manifest.json',
    'wb_proxy.py.bak-20261021-231708',
    'wb_proxy.py.bak-20261028-172236'
)

if (-not (Test-Path -LiteralPath $Baseline)) { throw "找不到基线文件: $Baseline" }

$rows = New-Object System.Collections.ArrayList
Get-Content -LiteralPath $Baseline | ForEach-Object {
    $line = $_.TrimEnd()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') { return }
    $want = $Matches[1].ToLower()
    $name = $Matches[2].Trim().Replace('/', '\')
    $path = Join-Path $Root $name

    if (-not (Test-Path -LiteralPath $path)) {
        $status = if ($expectMissing -contains $name) { 'MISSING-EXPECTED' } else { 'MISSING' }
        [void]$rows.Add([pscustomobject]@{ Status = $status; File = $name; Detail = '' })
    } else {
        $got = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
        $status = if ($got -eq $want) { 'MATCH' } elseif ($AllowChanged -contains $name) { 'DIFF-ALLOWED' } else { 'DIFF' }
        [void]$rows.Add([pscustomobject]@{ Status = $status; File = $name; Detail = $(if ($status -eq 'DIFF') { "$want != $got" } else { '' }) })
    }
}

$byStatus = $rows | Group-Object Status | Sort-Object Name
Write-Host "== 基线校验: $Baseline" -ForegroundColor Cyan
Write-Host "   root: $Root" -ForegroundColor DarkGray
Write-Host ''
foreach ($g in $byStatus) {
    $color = switch ($g.Name) { 'MATCH' { 'Green' } 'MISSING-EXPECTED' { 'Yellow' } 'DIFF-ALLOWED' { 'Cyan' } default { 'Red' } }
    Write-Host ("  {0,-16} {1}" -f $g.Name, $g.Count) -ForegroundColor $color
}
$bad = $rows | Where-Object { $_.Status -in @('DIFF', 'MISSING') }
if ($bad) {
    Write-Host ''
    Write-Host '== 不一致明细 ==' -ForegroundColor Red
    $bad | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}
Write-Host ''
Write-Host "OK: $($rows.Count) 条基线全部符合预期（$((($rows | Where-Object Status -eq 'MATCH').Count)) 项逐字节一致，$($expectMissing.Count) 项为已知构建产物/本地件）。" -ForegroundColor Green
exit 0
