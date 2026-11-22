# 外壳测试（headless 档）

这里的测试来自旧的实验目录 `C:\Desktop\2api\tests`，**只迁了"不起窗口、不碰生产"的那一档**。
运行它们不需要生产实例，也不会碰 `127.0.0.1:8788`。

## 怎么跑

```powershell
# 一条命令跑全部（推荐用内核便携运行时或外壳 runtime 的 Python 3.12+）
powershell -ExecutionPolicy Bypass -File shell\tests\run-headless.ps1

# 指定解释器 / 只跑部分
powershell -ExecutionPolicy Bypass -File shell\tests\run-headless.ps1 `
    -Python "C:\Desktop\wb-shell\wb-proxy\python\python.exe" `
    -Only ui_api_test.py,watchdog_test.py
```

当前基线：**10 passed / 0 failed / 0 skipped**（约 20 秒）。

| 套件 | 覆盖什么 |
|---|---|
| `check_js_api.py` | 面板暴露给 JS 的 `api_*` 方法签名契约（pywebview 会多传一个位置参数，签名不符会静默失败） |
| `ui_api_test.py` | ui_web 的 API 层：状态载荷、看板补丁、控制层分发、更新检查/入口接线（更新全部打桩） |
| `watchdog_test.py` | 内核看门狗：崩溃后自动拉起、重试上限 |
| `kernel_dir_test.py` | 内核目录解析与"换目录"行为 |
| `panel_secret_test.py` | 面板密码 DPAPI 密封/解封、自动登录落盘 |
| `lan_share_test.py` | LAN 分享：配置写入、日志留痕、重启链路（隔离目录） |
| `download_resume_test.py` | 更新包断点续传与完整性 |
| `relaunch_test.py` | 外壳自重启（用假外壳目录验证 CreateProcess 路径） |
| `update_notify_test.py` | 更新提醒状态机（A-D 四档） |
| `snapshot_check_test.py` | 目录快照/校验（配合 `snapshot_core.py` 辅助库） |

## 适配了什么

旧的测试假设"测试目录就在外壳根下、内核在 `<shell>\wb-proxy`"。本仓库布局是：

```
repo\                ← 内核载荷在仓库根
  wb_proxy.py ...
  shell\launcher\    ← 外壳源码
  shell\tests\       ← 本目录
```

因此每个测试头部注入了一段适配（已内置，无需手工改）：

```python
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
```

- `WB_SHELL_DIR`：外壳根（默认 `repo\shell`）
- `WB_KERNEL_DIR`：内核根（默认 `repo`）

## 环境依赖

| 依赖 | 说明 |
|---|---|
| Python 3.12+ | 优先用 `wb-proxy\python\python.exe`（发布包自带）；系统 3.14 也可 |
| `PYTHONDONTWRITEBYTECODE=1` | 运行器已设置，避免仓库里堆 `__pycache__` |
| 无第三方包 | 这一档**不需要** `launcher\vendor`（pywebview / pystray / PIL） |
| 无管理员权限 | 这一档不写注册表 |

## 没有迁过来的（有意为之）

| 类别 | 文件 | 原因 |
|---|---|---|
| GUI 探针（12） | `probe_menu*.py`、`probe_tray_*.py`、`probe_closing.py`、`probe_lifecycle.py`、`probe_no_self_reopen.py`、`probe_panel_autologin.py`、`probe_push.py`、`probe_recreate.py`、`probe_webwindow_recreate.py`、`tray_confirm_test.py` | 会真的建窗口、抓鼠标键盘；窗口标题与生产进程同名，自动化里跑会互杀 |
| 需要 vendor / 注册表（2） | `tray_autostart_test.py`（要 PIL + 写 HKCU Run）、`update_isolated_test.py`（要在 18899 起真内核副本） | 依赖 `launcher\vendor`（34.5 MB，可重建）与真实注册表；建议手工跑 |
| 前端脚本（2） | `bar_menu_test.js`、`collapse_sections_test.js` | 针对内核看板 DOM，归内核侧测试，不属外壳 |
| 工具脚本（2） | `grab_window.ps1`（抓窗口截图）、`setup_vendor.ps1`（解包 vendor） | 是工具不是测试；`setup_vendor.ps1` 同时补上了 `build_shell.ps1` 缺失的依赖 |

原件仍在 `C:\Desktop\2api\tests\`（**不要删**，GUI 探针只此一份）。

## 想增加套件时

1. 放进来，确认它满足"不起窗口 / 不碰 8788 / 不写注册表 / 用临时目录"；
2. 头部加那段 `_KERNEL_DIR` 适配（若用到内核路径）；
3. 跑 `run-headless.ps1` 确认变绿；
4. 更新本文件与 `maintenance/LOCAL-LOG.md` 的计数。
