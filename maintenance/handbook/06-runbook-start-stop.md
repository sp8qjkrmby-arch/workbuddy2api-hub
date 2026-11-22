> 主题: start-stop
> 何时读: 要启停服务、改完配置要生效、服务没起来时
> 关键词: 启动, 停止, 重启, 启动.vbs, 看门狗, restart-wb-shell, 8788

# 启停与重启

## 启动（用户日常路径）

双击 `C:\Desktop\wb-shell\启动.vbs`（可选参数 `--tray-only` 表示只留托盘不建窗）。
它用 WScript 以隐藏窗口方式找解释器（顺序：`runtime\pythonw.exe` → `runtime\python.exe` → `python\pythonw.exe` → `python\python.exe`），再拉起 `launcher`。

## 停止

**从托盘菜单退出**（"退出"而不是"关闭窗口"）。
⚠️ 不要用任务管理器杀内核：`core_watchdog` 会在约 6 秒内把它拉回来；要停就必须先停外壳（外壳绑了 Job Object，外壳退出会带走内核）。

## 重启（推荐用脚本，别手搓）

```powershell
# 立即重启（会短暂断开 8788；如果正在用网关，务必从外部窗口执行）
powershell -ExecutionPolicy Bypass -File C:\Desktop\2api维护\scripts\restart-wb-shell.ps1
# 或双击 C:\Desktop\2api维护\scripts\restart-now.cmd
```

脚本流程（`shell\ops\restart-wb-shell.ps1`，同一份）：

1. 停外壳（`pythonw … launcher`）→ 2. 停内核（`python … wb_proxy.py`）→ 3. 等 8788 释放（≤20 s）
4. `wscript 启动.vbs --tray-only` → 5. 轮询 `/health` ≤120 s（失败再拉一次）
6. 复核 `shell.json` 关键项 → 7. 抓 `shell.log` 尾部 → 8. 自删一次性任务（若由计划任务触发）
9. 日志写 `C:\Desktop\2api维护\logs\restart-<时间戳>.log`（UTF-8）

### 为什么必须"脱离会话"

这个对话的模型请求也走 8788。**在对话中途重启 = 打断我自己正在跑的一轮**。所以标准做法是用一次性计划任务延迟执行：

```powershell
$st = (Get-Date).AddMinutes(2).ToString('HH:mm')
schtasks /Create /TN "wb-shell-restart-once" /SC ONCE /ST $st /RL LIMITED /F `
  /TR "`"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"C:\Desktop\2api维护\scripts\restart-wb-shell.ps1`" -OnceTaskName wb-shell-restart-once"
```

### 实测数据（2026-11-22 20:30）

| 时刻 | 事件 |
|---|---|
| 20:30:02 | 开始，重启前 `/health` 200 |
| 20:30:03 | 停旧外壳 |
| 20:30:07 | 8788 释放 |
| 20:30:10 | 新内核启动，Job Object 绑定 |
| 20:30:14 | `/health` 200 |

→ **总耗时 12 秒，实际断流约 7 秒**。

## 配置改动何时生效

`data\shell.json` 是**启动时读取**，运行中改文件不会立即生效，而且运行中的外壳在保存配置时可能覆盖你的手改。
→ 改完必须重启外壳；重启后**回读确认**（重启脚本第 6 步就是干这个）。

## 没起来怎么办

1. 看 `C:\Desktop\2api维护\logs\restart-*.log` 最后几行；
2. 看 `C:\Desktop\wb-shell\data\shell.log` 尾部（外壳视角）；
3. 看 `C:\Desktop\wb-shell\data\core-8788.log` 尾部（内核视角，启动失败常在这）；
4. 再双击一次 `启动.vbs`（单实例保护，重复点无害）。
