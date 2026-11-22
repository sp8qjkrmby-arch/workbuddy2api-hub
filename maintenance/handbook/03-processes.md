> 主题: processes
> 何时读: 怀疑没在跑、想看 PID、要判断"能不能动这个进程"时
> 关键词: 进程, PID, pythonw, launcher, wb_proxy, 看门狗, 单实例

# 进程与运行态

## 正常状态：两个进程，一父一子

```
pythonw.exe  C:\Desktop\wb-shell\runtime\pythonw.exe  C:\Desktop\wb-shell\launcher --tray-only   ← 外壳（父）
python.exe   C:\Desktop\wb-shell\runtime\python.exe   ...\wb-proxy\wb_proxy.py --port 8788
             --accounts-dir ...\wb-proxy\accounts --usage-dir ...\wb-proxy\usage            ← 内核（子）
```

- 外壳用 `pythonw.exe`（无控制台）；内核用 `python.exe`（有 stdout，被外壳接管写日志）。
- 外壳启动内核时会绑定 **Job Object**：外壳退出，内核被系统一并回收（日志里会打印"Job Object：已绑定"）。

## 怎么查

```powershell
# 进程 + 命令行
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'launcher|wb_proxy' } |
  Select-Object ProcessId, CreationDate, CommandLine

# 端口
Get-NetTCPConnection -LocalPort 8788 -State Listen

# 健康检查（唯一权威判据）
(Invoke-WebRequest http://127.0.0.1:8788/health -UseBasicParsing).StatusCode   # 期望 200
```

`/health` 返回体里含 `ok`、`realm`、账号数量（如"账号 9 个（可用 7）"）。

## 看门狗行为（别被它骗）

- `shell.json`: `core_watchdog=true`、`core_watchdog_retries=2`。
- **直接 kill 内核 → 外壳约 6 秒内自动拉起**。所以"杀内核"不是停服务的方法，要停就先停外壳。
- 单实例：重复启动 `启动.vbs` 不会起第二份外壳（会聚焦已有窗口）。

## 历史现象：内核被外部终止

`core-8788.log` 统计：**82 次启动中 38 次退出码 `0x40010004`**（Windows 侧强制终止）。看门狗都兜住了，但根因未查。见 `13-known-issues.md`。

## 判定"现在到底谁在跑"的稳妥办法

1. `Get-CimInstance` 看命令行里的 `--port 8788`（不要只看进程名，可能有 8799 冒烟实例）；
2. `/health` 返回 200；
3. `data\shell.log` 最后几行有"内核已就绪：… 版本 x.y.z"。

> 本文档写作时（2026-11-22 20:30 重启后）的 PID：外壳 20648 / 内核 29424。**PID 会变，永远现查，别背。**
