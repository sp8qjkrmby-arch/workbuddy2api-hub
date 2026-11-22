<#
.SYNOPSIS
    用 fine-grained PAT 推送到 GitHub，凭据交给 Windows 凭据管理器（DPAPI）保存，不落明文。
.DESCRIPTION
    1) 交互式（隐藏输入）读取 PAT；
    2) 通过 `git credential approve` 交给 GCM → 存进 Windows 凭据管理器；
    3) 设代理后推 main 与全部 tag；
    4) 把 PAT 从内存抹掉（脚本结束进程即消失）。
    用完后可在 Windows「凭据管理器 → Windows 凭据」里删除 github.com 那条，
    或执行 `git credential-manager github logout`。
.EXAMPLE
    双击 scripts\push-with-token.cmd，或：
    powershell -ExecutionPolicy Bypass -File scripts\push-with-token.ps1
#>
[CmdletBinding()]
param(
    [string]$RepoDir = 'C:\Desktop\2api维护\repo',
    [string]$Proxy   = 'http://127.0.0.1:7897'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath (Join-Path $RepoDir '.git'))) { throw "不是 git 仓库: $RepoDir" }

Write-Host '粘贴你的 fine-grained PAT（输入不回显；粘贴后回车）：' -ForegroundColor Cyan
$secure = Read-Host -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if (-not $token) { throw '没读到 token，已退出。' }
if ($token -notmatch '^(github_pat_|ghp_)') {
    Write-Host '提醒：这不像 GitHub PAT（应以 github_pat_ 或 ghp_ 开头），继续尝试……' -ForegroundColor Yellow
}

# 1) 交给凭据管理器（GCM → Windows Credential Manager，DPAPI 加密）
$payload = "protocol=https`nhost=github.com`nusername=x-access-token`npassword=$token`n`n"
try {
    $payload | & git -C $RepoDir credential approve
    Write-Host '已写入 Windows 凭据管理器（github.com）。' -ForegroundColor Green
} catch {
    Write-Host "写入凭据管理器失败（将只在本次会话里用）：$($_.Exception.Message)" -ForegroundColor Yellow
}

# 2) 推送
$env:HTTPS_PROXY = $Proxy
$env:HTTP_PROXY  = $Proxy
$env:GIT_TERMINAL_PROMPT = '0'

Write-Host ''
Write-Host '== 推送 main ==' -ForegroundColor Cyan
& git -C $RepoDir push -u origin main
$code1 = $LASTEXITCODE

Write-Host ''
Write-Host '== 推送 tags ==' -ForegroundColor Cyan
& git -C $RepoDir push origin --tags
$code2 = $LASTEXITCODE

# 3) 抹掉内存里的 token
$token = $null
[GC]::Collect()

Write-Host ''
if ($code1 -eq 0 -and $code2 -eq 0) {
    Write-Host '推送成功。' -ForegroundColor Green
    & git -C $RepoDir log --oneline -3
    Write-Host ''
    Write-Host '收尾建议：用完可删除凭据 → git credential-manager github logout' -ForegroundColor DarkGray
    exit 0
}
Write-Host "推送失败（main=$code1, tags=$code2）。常见原因：token 权限不足（需 Contents: Read and write）、仓库选错、token 过期、代理未启动。" -ForegroundColor Red
exit 1
