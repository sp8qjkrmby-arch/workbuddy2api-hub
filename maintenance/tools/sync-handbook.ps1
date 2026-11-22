<#
.SYNOPSIS
    把工作区的知识库同步进仓库（maintenance\handbook），并重建索引。
.DESCRIPTION
    工作区 `handbook\` 是编辑入口；仓库 `repo\maintenance\handbook\` 是随 git 推送的权威副本。
    本脚本：重建索引 → 镜像复制（含删除仓库里已不存在的分册）→ 报告差异。
    只做"工作区 → 仓库"单向同步；仓库里的改动不会被反向覆盖（避免误删）。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\sync-handbook.ps1
    powershell -ExecutionPolicy Bypass -File scripts\sync-handbook.ps1 -Commit   # 顺手提交
#>
[CmdletBinding()]
param(
    [string]$Source = 'C:\Desktop\2api维护\handbook',
    [string]$Target = 'C:\Desktop\2api维护\repo\maintenance\handbook',
    [switch]$Commit
)

$ErrorActionPreference = 'Stop'

# 1) 先重建索引（保证行数/体积是最新的）
$gen = Join-Path $PSScriptRoot 'build-handbook-index.ps1'
if (Test-Path -LiteralPath $gen) {
    & $gen -HandbookDir $Source | Out-Null
    Write-Host '索引已重建'
}

# 2) 镜像复制
if (-not (Test-Path -LiteralPath $Target)) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
}

$srcFiles = Get-ChildItem -LiteralPath $Source -Filter '*.md'
$copied = 0
foreach ($f in $srcFiles) {
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $Target $f.Name) -Force
    $copied++
}
$srcNames = $srcFiles.Name
foreach ($f in (Get-ChildItem -LiteralPath $Target -Filter '*.md')) {
    if ($srcNames -notcontains $f.Name) {
        Remove-Item -LiteralPath $f.FullName -Force
        Write-Host "删除仓库中多余分册: $($f.Name)" -ForegroundColor DarkYellow
    }
}
Write-Host ("同步完成：{0} 册 → {1}" -f $copied, $Target)

# 3) 可选提交
if ($Commit) {
    $repo = Split-Path -Parent (Split-Path -Parent $Target)
    $env:HTTPS_PROXY = 'http://127.0.0.1:7897'
    $env:HTTP_PROXY  = 'http://127.0.0.1:7897'
    & git -C $repo add -A maintenance/handbook
    $status = & git -C $repo status --short maintenance/handbook
    if ($status) {
        & git -C $repo commit -m 'docs(handbook): 同步知识库' -m 'sync handbook from workspace'
        Write-Host '已提交（未自动推送，需 git push origin main）' -ForegroundColor Green
    } else {
        Write-Host '仓库里没有变化，无需提交'
    }
}
