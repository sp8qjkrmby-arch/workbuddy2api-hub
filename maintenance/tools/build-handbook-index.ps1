<#
.SYNOPSIS
    重新生成知识库索引 handbook\00-INDEX.md（从各分册头部的 front-matter 自动汇总）。
.DESCRIPTION
    每个分册开头四行约定（生成器靠它填表）：
        > 主题: xxx
        > 何时读: xxx
        > 关键词: xxx
    改了/新增分册后重跑本脚本，索引里的行数、体积、主题都会自动更新。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-handbook-index.ps1
#>
[CmdletBinding()]
param(
    [string]$HandbookDir = 'C:\Desktop\2api维护\handbook'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $HandbookDir)) { throw "找不到目录: $HandbookDir" }

function Get-Field([string[]]$lines, [string]$name) {
    $pat = '^>\s*' + [regex]::Escape($name) + '\s*:\s*(.+)$'
    foreach ($l in $lines) {
        if ($l -match $pat) { return $Matches[1].Trim() }
    }
    return ''
}

$rows = @()
$files = Get-ChildItem -LiteralPath $HandbookDir -Filter '*.md' | Where-Object { $_.Name -ne '00-INDEX.md' } | Sort-Object Name
foreach ($f in $files) {
    $head = Get-Content -LiteralPath $f.FullName -TotalCount 12 -Encoding UTF8
    $all = Get-Content -LiteralPath $f.FullName -Encoding UTF8
    $nonEmpty = ($all | Where-Object { $_.Trim() }).Count
    $rows += [pscustomobject]@{
        Number  = $f.BaseName.Substring(0, 2)
        Topic   = Get-Field $head '主题'
        When    = Get-Field $head '何时读'
        Keys    = Get-Field $head '关键词'
        Lines   = $nonEmpty
        KB      = [math]::Round($f.Length / 1KB, 1)
        File    = $f.Name
    }
}

# ---------- 按"我想干什么"的速查表（人工维护） ----------
$quick = @'
| 我想… | 读这个 |
|---|---|
| 知道这台机器装了什么、端口/代理怎么设 | `01-environment.md` |
| 找文件在哪、哪份代码是权威 | `02-paths.md` |
| 确认服务在不在跑 / 看 PID | `03-processes.md` |
| 判断版本、解释"版本 ?"、双轨是什么 | `04-versions.md` |
| 弄清代码从哪来、能不能换源 | `05-sources.md` |
| 启动 / 停止 / 重启（含脱离会话的做法） | `06-runbook-start-stop.md` |
| 备份、恢复、换机迁移 | `07-runbook-backup-restore.md` |
| 看懂 `shell.json` 每个键、改配置 | `08-config-reference.md` |
| 查日志、判断噪音与真错误 | `09-logs.md` |
| 改完怎么验收（门 1/2/3） | `10-verification.md` |
| 提交、推送、认证报错、tag | `11-git-workflow.md` |
| 动手前确认红线与纪律 | `12-rules.md` |
| 遇到怪现象 / 看还有哪些 bug | `13-known-issues.md` |
| 写脚本报怪错（BOM/CRLF/编码） | `14-gotchas-windows.md` |
| 密钥、仓库可见性、账号风控 | `15-security.md` |
| 为什么这么做（不追新/自建通道…） | `16-decisions.md` |
| 了解外壳是怎么演化的 | `17-history-shell.md` |
| 今天改了什么、怎么回滚 | `18-worklog-2026-11-22.md` |
| 接下来做什么、优先级 | `19-next-steps.md` |
| 理解内核架构、请求链路、模块分工 | `20-architecture.md` |
| 看不懂某个词 | `21-glossary.md` |
'@

$table = @()
$table += '| # | 主题 | 何时读 | 关键词 | 行数 | KB | 文件 |'
$table += '|---|---|---|---|---|---|---|'
foreach ($r in $rows) {
    $table += ('| {0} | **{1}** | {2} | {3} | {4} | {5} | `{6}` |' -f $r.Number, $r.Topic, $r.When, $r.Keys, $r.Lines, $r.KB, $r.File)
}

# ---------- 原始证据（大文件） ----------
$reportDir = Join-Path (Split-Path -Parent $HandbookDir) 'reports'
$ev = @()
if (Test-Path -LiteralPath $reportDir) {
    $ev += '| 文件 | 体积 | 什么时候才读 |'
    $ev += '|---|---|---|'
    $map = @{
        '01-repo-analysis.md'    = '要改仓库结构/CI/发布流程时（183 行）'
        '02-shell-analysis.md'   = '要动外壳、查它的模块与数据（318 行，最厚）'
        '03-proxy-core.md'       = '要动内核、查上游依赖面与硬编码位置（238 行）'
        '04-history.md'          = '要还原外壳开发史与未完成项（175 行）'
        '05-ecosystem.md'        = '要找替代源/社区镜像（22 KB）'
        '06-source-provenance.md'= '要复核"我们的代码是不是原版"（94 行）'
        '07-smoke-test.md'       = '要复核冒烟门禁结果（62 行）'
    }
    foreach ($f in (Get-ChildItem -LiteralPath $reportDir -Filter '*.md' | Sort-Object Name)) {
        $when = if ($map.ContainsKey($f.Name)) { $map[$f.Name] } else { '按需' }
        $ev += ('| `reports\{0}` | {1} KB | {2} |' -f $f.Name, [math]::Round($f.Length / 1KB, 1), $when)
    }
    $ev += '| `reports\smoke-evidence\` | — | 要贴冒烟证据（日志 + 脱敏响应）时 |'
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
$doc = @()
$doc += '# 知识库索引（字典入口）'
$doc += ''
$doc += "> 生成时间：$stamp ｜ 由 ``scripts\build-handbook-index.ps1`` 自动汇总，**改了分册就重跑**。"
$doc += '>'
$doc += '> **用法（省上下文的规矩）**：'
$doc += '> 1. 先看下面的速查表，定位到**一个**主题；'
$doc += '> 2. 只打开那一个分册（单册目标 ≤150 行）；'
$doc += '> 3. 分册之间用 `文件名` 互相指路，顺着跳，别整目录读；'
$doc += '> 4. `reports\` 里的原始证据体积大，**只在复核结论时才读**。'
$doc += ''
$doc += '---'
$doc += ''
$doc += '## 1. 速查：我想干什么 → 读哪册'
$doc += ''
$doc += ($quick -split "`n")
$doc += ''
$doc += '## 2. 全部分册（自动生成）'
$doc += ''
$doc += $table
$doc += ''
$doc += '## 3. 原始证据（大文件，按需读）'
$doc += ''
if ($ev.Count -gt 0) { $doc += $ev } else { $doc += '（未找到 reports 目录）' }
$doc += ''
$doc += '## 4. 维护这份知识库的约定'
$doc += ''
$doc += '| 约定 | 说明 |
|---|---|
| 一册一主题 | 文件名 `NN-topic.md`，`NN` 是两位序号，新增就往后排 |
| 头部四行 | `> 主题:` / `> 何时读:` / `> 关键词:`（生成器靠它填表） |
| 单册上限 | 目标 **≤150 行**；超了就拆册（比如把"故障"拆成"故障现象"和"故障处置"） |
| 不留悬空引用 | 提到别的文件一律写 `文件名`，不写绝对路径 |
| 事实要有出处 | 数字/结论尽量标注来源（哪个日志、哪次实测、哪份报告） |
| 权威副本 | 仓库 `maintenance\handbook\`（随 git 推送）；工作区这份是同一内容 |
| 更新时机 | 每次"踩新坑 / 改架构 / 改流程"后立即补，别攒 |
'
$doc += ''
$doc += ('> 统计：共 {0} 册，合计约 {1} 行 / {2} KB。' -f $rows.Count, (($rows | Measure-Object Lines -Sum).Sum), [math]::Round((($rows | Measure-Object KB -Sum).Sum),1))

$out = Join-Path $HandbookDir '00-INDEX.md'
$doc | Set-Content -LiteralPath $out -Encoding UTF8
Write-Host ("已生成 {0}（{1} 册，{2} KB）" -f $out, $rows.Count, [math]::Round((Get-Item $out).Length / 1KB, 1))
