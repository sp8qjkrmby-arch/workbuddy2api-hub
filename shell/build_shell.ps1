# build_shell.ps1 — 幂等构建外壳：铺运行时、校验依赖、生成入口、写自检清单。
#
# 设计原则：**只在 <shell> 里动手**，绝不写内核目录（内核是另一个进程的资产）。
#
# 用法：
#   pwsh -File build_shell.ps1                        # 用本机 Python 铺 runtime\
#   pwsh -File build_shell.ps1 -PythonExe <python.exe>  # 指定源解释器
#   pwsh -File build_shell.ps1 -SkipRuntime            # 只校验、不铺运行时
#   pwsh -File build_shell.ps1 -Download               # 顺便联网下载依赖轮子

param(
  [string]$ShellDir = $PSScriptRoot,
  [string]$PythonExe = '',
  [switch]$SkipRuntime,
  [switch]$Download,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ShellDir = (Resolve-Path $ShellDir).Path
$VendorDir = Join-Path $ShellDir 'launcher\vendor'
$RuntimeDir = Join-Path $ShellDir 'runtime'
$LauncherDir = Join-Path $ShellDir 'launcher'
$DataDir = Join-Path $ShellDir 'data'
$ToolsDir = Join-Path $ShellDir 'tools'
$CoreDir = Join-Path (Split-Path $ShellDir -Parent) 'wb-proxy'

function Say([string]$text) { Write-Host $text }
function Ok([string]$text) { Write-Host ("  [OK]   " + $text) }
function Warn([string]$text) { Write-Host ("  [警告] " + $text) -ForegroundColor Yellow }
function Fail([string]$text) { Write-Host ("  [失败] " + $text) -ForegroundColor Red; $script:Failures++ }
$Failures = 0

function Find-Python {
  if ($PythonExe -and (Test-Path $PythonExe)) { return (Resolve-Path $PythonExe).Path }
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.13-64\python.exe'),
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    (Get-Command py -ErrorAction SilentlyContinue).Source
  )
  foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
  # 递归找 per-user 安装
  $root = Join-Path $env:LOCALAPPDATA 'Python'
  if (Test-Path $root) {
    $found = Get-ChildItem $root -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notmatch 'venv|Scripts' } | Select-Object -First 1
    if ($found) { return $found.FullName }
  }
  return ''
}

Say ('=' * 72)
Say '  WorkBuddy2API-Hub 外壳构建'
Say ('=' * 72)
Say ("  外壳目录 : {0}" -f $ShellDir)
Say ("  内核目录 : {0}" -f $CoreDir)

# ---------------------------------------------------------------------------
Say ''
Say '[1/5] 检查目录结构'
foreach ($folder in @($LauncherDir, $DataDir, $ToolsDir)) {
  if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
}
Ok 'launcher / data / tools 就位'

$coreOk = Test-Path (Join-Path $CoreDir 'wb_proxy.py')
if ($coreOk) { Ok ("内核入口存在：{0}" -f (Join-Path $CoreDir 'wb_proxy.py')) }
else { Warn ("没找到内核：{0}（外壳仍可构造，运行时会报错并提示 --core-dir）" -f $CoreDir) }

# ---------------------------------------------------------------------------
Say ''
Say '[2/5] 校验随壳依赖（vendor）'
$required = @(
  @('py\webview\__init__.py',            'pywebview 包'),
  @('py\webview\lib\runtimes\win-x64\native\WebView2Loader.dll', 'WebView2 原生加载器'),
  @('py\webview\lib\Microsoft.Web.WebView2.Core.dll',    'WebView2 Core 托管库'),
  @('py\webview\lib\Microsoft.Web.WebView2.WinForms.dll','WebView2 WinForms 托管库'),
  @('py\pystray\__init__.py',            'pystray 包'),
  @('py\six.py',                         'six（pystray 依赖）'),
  @('py\clr.py',                         'pythonnet clr'),
  @('py\clr_loader\__init__.py',         'clr_loader'),
  @('py\bottle.py',                      'bottle'),
  @('py312\PIL\Image.py',                'Pillow（3.12 二进制）'),
  @('py314\PIL\Image.py',                'Pillow（3.14 二进制）'),
  @('py312\_cffi_backend.cp312-win_amd64.pyd', 'cffi 后端（3.12）'),
  @('py314\_cffi_backend.cp314-win_amd64.pyd', 'cffi 后端（3.14）')
)
$missing = 0
foreach ($item in $required) {
  $path = Join-Path $VendorDir $item[0]
  if (Test-Path $path) { Ok $item[1] }
  else { Fail ("缺少 {0}（{1}）" -f $item[1], $path); $missing++ }
}
if ($missing -gt 0) {
  Say ''
  Say '  依赖不全。用下面任一方式补齐：'
  Say '    pwsh -File tests\setup_vendor.ps1 -Download'
  Say '    或手工把轮子放到 %TEMP%\wbvendor 后运行 tests\setup_vendor.ps1'
}

$vendorSize = 0
if (Test-Path $VendorDir) {
  $vendorSize = (Get-ChildItem $VendorDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
}
Say ("  vendor 体积：{0:N1} MB" -f $vendorSize)

# ---------------------------------------------------------------------------
Say ''
Say '[3/5] 铺外壳运行时（runtime\）'
if ($SkipRuntime) {
  Warn '按 -SkipRuntime 跳过'
} else {
  $source = Find-Python
  # 源解释器可能被指名成 pythonw.exe（Find-Python 找的是 python.exe，
  # 但允许 -PythonExe 指任意一个）。统一成一对：解释器 + 无控制台版本。
  $sourceDir = if ($source) { Split-Path $source -Parent } else { '' }
  $sourceExe = if ($sourceDir) { Join-Path $sourceDir 'python.exe' } else { '' }
  $sourceW = if ($sourceDir) { Join-Path $sourceDir 'pythonw.exe' } else { '' }

  if (-not $source -or -not (Test-Path $sourceExe)) {
    Fail '找不到可用的 Python（用 -PythonExe 指定 python.exe）'
  } elseif (-not (Test-Path $sourceW)) {
    Fail ("源解释器旁边没有 pythonw.exe：{0}（没有它就无法无黑框运行）" -f $sourceDir)
  } else {
    Ok ("源解释器：{0}" -f $sourceExe)
    $version = (& $sourceExe -c "import sys;print('%d.%d.%d' % sys.version_info[:3])")
    Ok ("版本：{0}" -f $version)
    $hasTk = (& $sourceExe -c "import tkinter;print('yes')" 2>$null) -eq 'yes'
    if ($hasTk) { Ok '源解释器带 tkinter（原生窗口兜底可用）' }
    else { Warn '源解释器没有 tkinter，原生窗口兜底将不可用（内嵌看板仍可用）' }
    $prefix = (& $sourceExe -c "import sys;print(sys.prefix)")
    if (-not (Test-Path $prefix)) { Fail ("取不到 sys.prefix：{0}" -f $prefix) }
    else {
      if ($Force -or -not (Test-Path (Join-Path $RuntimeDir 'pythonw.exe'))) {
        Say ("  正在复制 {0} → {1}" -f $prefix, $RuntimeDir)
        if (Test-Path $RuntimeDir) { Remove-Item $RuntimeDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
        foreach ($f in @('python.exe','pythonw.exe','python3.dll','vcruntime140.dll',
                         'vcruntime140_1.dll','LICENSE.txt')) {
          if (Test-Path (Join-Path $prefix $f)) { Copy-Item (Join-Path $prefix $f) $RuntimeDir -Force }
        }
        Get-ChildItem (Join-Path $prefix 'python3*.dll') -ErrorAction SilentlyContinue |
          ForEach-Object { Copy-Item $_.FullName $RuntimeDir -Force }
        # 标准库：排除 site-packages / 缓存 / 用不到的大件
        & robocopy (Join-Path $prefix 'Lib') (Join-Path $RuntimeDir 'Lib') /E `
            /XD site-packages __pycache__ test idlelib turtledemo `
            /NFL /NDL /NJH /NJS /NP | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir 'Lib\site-packages') | Out-Null
        # 扩展模块（_socket/_ssl/_ctypes…）
        if (Test-Path (Join-Path $prefix 'DLLs')) {
          & robocopy (Join-Path $prefix 'DLLs') (Join-Path $RuntimeDir 'DLLs') /E /XF *.lib `
              /NFL /NDL /NJH /NJS /NP | Out-Null
        }
        # tkinter 需要 Tcl/Tk 的数据文件
        if (Test-Path (Join-Path $prefix 'tcl')) {
          & robocopy (Join-Path $prefix 'tcl') (Join-Path $RuntimeDir 'tcl') /E `
              /NFL /NDL /NJH /NJS /NP | Out-Null
        }
      } else {
        Ok 'runtime\ 已存在（加 -Force 可重建）'
      }
    }
  }

  $rtExe = Join-Path $RuntimeDir 'pythonw.exe'
  $rtPy = Join-Path $RuntimeDir 'python.exe'
  if (Test-Path $rtPy) {
    $rtVersion = (& $rtPy -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null)
    Ok ("runtime 可用：Python {0}" -f $rtVersion)
    $tests = @(
      @('import ssl', 'ssl'),
      @('import sqlite3', 'sqlite3'),
      @('import ctypes', 'ctypes'),
      @('import json', 'json'),
      @('import zipfile', 'zipfile')
    )
    foreach ($t in $tests) {
      if ((& $rtPy -c "$($t[0]);print('yes')" 2>$null) -eq 'yes') { Ok ("runtime 模块 " + $t[1]) }
      else { Fail ("runtime 缺少模块 " + $t[1]) }
    }
    if ((& $rtPy -c "import tkinter;print('yes')" 2>$null) -eq 'yes') { Ok 'runtime 带 tkinter（原生窗口兜底可用）' }
    else { Warn 'runtime 没有 tkinter，原生窗口兜底将不可用（内嵌看板仍可用）' }
  } elseif (Test-Path $rtExe) {
    Ok 'runtime\pythonw.exe 就位'
  } else {
    Fail 'runtime\ 未铺成功'
  }
}

# ---------------------------------------------------------------------------
Say ''
Say '[4/5] 生成入口与自检清单'
$vbs = Join-Path $ShellDir '启动.vbs'
if (Test-Path $vbs) {
  $head = [IO.File]::ReadAllBytes($vbs) | Select-Object -First 2
  if ($head[0] -eq 0xFF -and $head[1] -eq 0xFE) { Ok '启动.vbs 编码为 UTF-16 LE + BOM（WSH 必需）' }
  else {
    Warn '启动.vbs 不是 UTF-16 LE + BOM：中文注释会被 WSH 按系统代码页读坏'
    Say  '        正在转换…'
    $text = [IO.File]::ReadAllText($vbs, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($vbs, $text, [Text.Encoding]::Unicode)
    Ok '已转换为 UTF-16 LE + BOM'
  }
} else {
  Warn '缺少 启动.vbs（双击入口）'
}

$grabExe = Join-Path $ToolsDir 'grab_window.exe'
if (Test-Path $grabExe) { Ok '抓图工具已就绪（验收用）' }
else {
  $grabCs = Join-Path $ToolsDir 'grab_window.cs'
  $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
  if ((Test-Path $grabCs) -and (Test-Path $csc)) {
    & $csc /nologo /target:exe "/out:$grabExe" $grabCs 2>&1 | Out-Null
    if (Test-Path $grabExe) { Ok '已编译抓图工具' } else { Warn '抓图工具编译失败（只影响验收，不影响功能）' }
  } else { Warn '跳过抓图工具（缺源码或 csc.exe）' }
}

# runtime 清单：记录每个文件的 sha256，便于判断运行时是否被动过
$manifestPath = Join-Path $DataDir 'host-runtime-manifest.json'
if (Test-Path $RuntimeDir) {
  $files = Get-ChildItem $RuntimeDir -Recurse -File | Sort-Object FullName
  $map = [ordered]@{}
  foreach ($f in $files) {
    $rel = $f.FullName.Substring($RuntimeDir.Length + 1).Replace('\', '/')
    $map[$rel] = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower()
  }
  $payload = [ordered]@{
    created  = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    runtime  = $RuntimeDir
    files    = $map.Count
    abi      = 'py' + ((& (Join-Path $RuntimeDir 'python.exe') -c "import sys;print('%d%d' % sys.version_info[:2])"))
    manifest = $map
  }
  $payload | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding UTF8
  Ok ("清单已写入：{0}（{1} 个文件）" -f $manifestPath, $map.Count)
} else {
  Warn '没有 runtime\，跳过清单'
}

# ---------------------------------------------------------------------------
Say ''
Say '[5/5] 生效配置摘要'
$pyForConfig = if (Test-Path (Join-Path $RuntimeDir 'python.exe')) { Join-Path $RuntimeDir 'python.exe' } else { Find-Python }
if ($pyForConfig) {
  Push-Location $ShellDir
  try { & $pyForConfig 'launcher' '--print-config' 2>&1 | ForEach-Object { Say ("  " + $_) } }
  finally { Pop-Location }
} else {
  Warn '没有可用解释器，无法打印配置'
}

Say ''
Say ('=' * 72)
if ($Failures -eq 0) { Say '  构建完成：没有失败项。' }
else { Say ("  构建完成：{0} 个失败项（见上面的 [失败]）" -f $Failures) }
Say ('=' * 72)
exit $(if ($Failures -eq 0) { 0 } else { 1 })
