<#
.SYNOPSIS
   跑外壳 headless 测试（不起窗口、不碰生产 8788）。
.DESCRIPTION
   逐个执行 tests 目录里的 headless 套件，汇总 PASS/FAIL/SKIP 并给出退出码。
   需要 Python 3.12+（建议用内核便携运行时 wb-proxy\python\python.exe 或外壳 runtime）。
.EXAMPLE
   powershell -ExecutionPolicy Bypass -File shell\tests\run-headless.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = '',
    [string[]]$Only = @()
)
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
if (-not $Python) {
    $candidates = @(
        (Join-Path (Split-Path -Parent (Split-Path -Parent $here)) 'python\python.exe'),
        'C:\Desktop\wb-shell\runtime\python.exe',
        'python'
    )
    foreach ($c in $candidates) { if ($c -eq 'python' -or (Test-Path -LiteralPath $c)) { $Python = $c; break } }
}
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:WB_SHELL_DIR = Split-Path -Parent $here

$suites = @(Get-ChildItem $here -Filter '*_test.py') + @(Get-Item (Join-Path $here 'check_js_api.py'))
$suites = $suites | Sort-Object Name
if ($Only.Count -gt 0) { $suites = $suites | Where-Object { $Only -contains $_.Name } }

$pass = 0; $fail = 0; $skip = 0; $failed = @()
foreach ($s in $suites) {
    $out = & $Python $s.FullName 2>&1
    $code = $LASTEXITCODE
    $tail = ($out | Select-Object -Last 1)
    if ($code -eq 0) { $pass++; Write-Host ("  [PASS] {0,-30} {1}" -f $s.Name, $tail) -ForegroundColor Green }
    elseif ($code -eq 3) { $skip++; Write-Host ("  [SKIP] {0,-30} {1}" -f $s.Name, $tail) -ForegroundColor Yellow }
    else { $fail++; $failed += $s.Name; Write-Host ("  [FAIL:{0}] {1,-28} {2}" -f $code, $s.Name, $tail) -ForegroundColor Red }
}
Write-Host ""
Write-Host ("  共 {0} 套件：{1} passed, {2} failed, {3} skipped" -f $suites.Count, $pass, $fail, $skip)
if ($failed) { Write-Host ("  失败: " + ($failed -join ', ')) -ForegroundColor Red; exit 1 }
exit 0