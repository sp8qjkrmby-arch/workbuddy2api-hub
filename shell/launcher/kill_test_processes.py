# -*- coding: utf-8 -*-
"""kill_test_processes.py — 带硬保护的测试进程清理器。

**为什么必须用脚本而不是手写一行 PowerShell：**

我在这上面犯过两次不可饶恕的错，两次都把用户真正在用的 8788 代理给停了，
导致会话掉线。两次都是"清理我自己的测试进程"这个动作，问题出在匹配式：

1. `-match '188\\d\\d'` 是**正则**，本意匹配我在用的 18788/18840 这些测试端口，
   但任何命令行里含 `188XX` 的进程都会被命中；
2. `Where CommandLine -match 'wb-shell'` 只匹配了外壳路径、**没有过滤端口**，
   于是用户那个管着 8788 的外壳实例也被杀掉；外壳一死，Job Object 立刻把
   8788 的内核一起回收——这就是掉线的直接原因。

**所以这个脚本用"白名单 + 黑名单"而不是"模糊匹配"：**

* 只杀端口在本文件 `ALLOWED_TEST_PORTS` 白名单里的内核进程（每个端口必须是
  显式列出的测试端口，绝不用通配）；
* **任何命令行含 `--port 8788` 的进程一律跳过**（用户的生产代理，硬编码保护）；
* 只杀 `python.exe` / `pythonw.exe`；
* 跳过调用者自己、以及当前进程树里的进程；
* 删临时目录时同样先断言路径在 `%TEMP%` 下且名字以 `wb-` 开头。

用法：
    runtime\\python.exe launcher\\kill_test_processes.py            # 只报告，不杀
    runtime\\python.exe launcher\\kill_test_processes.py --apply    # 真的清理
"""

import argparse
import os
import re
import subprocess
import sys
import time

# 生产端口：任何进程只要命令行里带它，一律不碰。不要改成可配置。
PROTECTED_PORTS = (8788,)
# 我测试用过的端口，显式列出，不用通配。
# 规矩：只往里加"我为了测试而显式选用"的端口，且都远离 8788；
# 不在名单里的端口一律跳过（宁可漏杀，也不误杀）。
ALLOWED_TEST_PORTS = (18788, 18789, 18790, 18791, 18792, 18793, 18794, 18795,
                      18801, 18802, 18803, 18810, 18820, 18830, 18840, 18850,
                      18860, 18870, 18877, 18880, 18890, 18899,
                      18901, 18902, 18903, 18904, 18905)
SAFE_TEMP_PREFIX = "wb-"
# 我**明确创建过**的临时目录/文件。绝不用 "wb-" 前缀去扫——
# 实测 %TEMP% 下还有 `wb-installer-register.log`、`wb-switch-upstream` 这些
# 属于别的工具的东西，按前缀删就是把别人的文件删了（和误杀 8788 是同一类错误）。
OWNED_TEMP_NAMES = (
    "wb-api-test", "wb-update-test", "wb-web-test", "wb-vbs-test",
    "wb-close", "wb-regress", "wb-final", "wb-final2", "wb-verify",
    "wb-lifecycle", "wb-recreate", "wb-job", "wb-172-check", "wbshell-test",
    "wb-tray-show", "wb-panel-secret", "wb-panel-autologin", "wb-tray-settings",
    "wb-lan-test",
    "wb-settings-debug", "wb-secret-probe", "wb-download-test",
    "wb-menu", "wb-menu-live", "wb-push", "wb-kdir", "wb-no-reopen",
    "wb-relaunch",
    "wb-web-run.log", "wb-web-run.log.err", "fake-core",
)

# 我**明确写过**的测试脚本（按文件名白名单，配 `tests\` 这个路径片段一起匹配）。
# 为什么需要它：这类进程命令行里**没有 --port**，上面那条端口白名单永远抓不到，
# 于是一个卡住的探针会一直挂在桌面上。2026-10-14 实测：一次 probe_menu.py 没销毁
# 窗口，就那样开着 `menu-probe` 窗口挂了几十分钟，而清理器对它无能为力。
# 这里仍然是白名单（文件名 + tests\ 路径片段），不是通配，也绝不可能匹配到
# 生产外壳（`launcher`）或内核（`wb_proxy.py --port`）。
OWNED_TEST_SCRIPTS = (
    "probe_menu.py", "probe_menu_live.py", "probe_tray_show.py",
    "probe_tray_settings.py",
    "probe_panel_autologin.py", "probe_no_self_reopen.py",
    "probe_push.py", "probe_closing.py",
    "probe_recreate.py", "probe_webwindow_recreate.py",
    "check_js_api.py", "ui_api_test.py", "ui_native_test.py",
    "tray_autostart_test.py", "kernel_dir_test.py", "panel_secret_test.py",
    "download_resume_test.py", "update_isolated_test.py", "snapshot_core.py",
    "relaunch_test.py", "lan_share_test.py",
)


def owned_test_script(command):
    """命令行里是不是在跑我写的某个测试脚本。命中就返回文件名。"""
    text = str(command or "").replace("/", "\\").lower()
    for name in OWNED_TEST_SCRIPTS:
        if ("tests\\" + name).lower() in text:
            return name
    return ""


def guess_shell_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def list_python_processes():
    """返回 [{pid, name, command, is_ours}]；只列 python/pythonw。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
          "Name='pythonw.exe'\" | "
          "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
    except Exception as exc:
        print("枚举进程失败：%r" % (exc,))
        return []
    import json
    text = (out.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        print("进程列表解析失败，原始输出前 200 字：%s" % text[:200])
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for item in data:
        rows.append({"pid": int(item.get("ProcessId") or 0),
                     "name": str(item.get("Name") or ""),
                     "command": str(item.get("CommandLine") or "")})
    return rows


def port_of(command):
    match = re.search(r"--port\s+(\d+)", command or "")
    return int(match.group(1)) if match else None


def classify(row, mine_pids):
    """决定这个进程该不该被杀。返回 (verdict, reason)。

    verdict: "kill" / "skip"
    """
    pid = row["pid"]
    name = row["name"].lower()
    command = row["command"]

    if name not in ("python.exe", "pythonw.exe"):
        return "skip", "不是 python 进程"
    if pid in mine_pids or pid == os.getpid():
        return "skip", "是本次清理进程自己/其父进程"

    for protected in PROTECTED_PORTS:
        if re.search(r"--port\s+%d\b" % protected, command):
            return "skip", "生产端口 %d（受保护，绝不触碰）" % protected

    script = owned_test_script(command)
    if script:
        return "kill", "测试脚本残留（%s）" % script

    port = port_of(command)
    if port is None:
        return "skip", "命令行里没有 --port，无法确认是测试实例"

    if port not in ALLOWED_TEST_PORTS:
        return "skip", "端口 %d 不在测试白名单里" % port

    if "wb_proxy.py" not in command and "launcher" not in command:
        return "skip", "既不是内核也不是外壳"

    return "kill", "测试端口 %d" % port


def parent_chain():
    """当前进程及其父进程 pid（避免把自己所在的 pwsh / python 杀掉）。"""
    pids = {os.getpid()}
    try:
        pid = os.getppid()
        for _ in range(3):
            if not pid or pid in pids:
                break
            pids.add(pid)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").ParentProcessId"
                 % pid],
                capture_output=True, text=True, timeout=30)
            pid = int((out.stdout or "0").strip() or 0)
    except Exception:
        pass
    return pids


def safe_temp_dirs():
    """只返回我明确创建过的那些（按确切名字，不按前缀扫）。"""
    base = os.path.abspath(os.environ.get("TEMP") or "")
    found = []
    for name in OWNED_TEMP_NAMES:
        path = os.path.join(base, name)
        if os.path.exists(path):
            found.append(path)
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="真的执行清理（默认只报告）")
    args = parser.parse_args()

    mine = parent_chain()
    rows = list_python_processes()
    print("python 进程共 %d 个；保护端口：%s；测试白名单：%s"
          % (len(rows), PROTECTED_PORTS, list(ALLOWED_TEST_PORTS)[:6] + ["…"]))
    print()
    to_kill = []
    for row in sorted(rows, key=lambda r: r["pid"]):
        verdict, reason = classify(row, mine)
        mark = "将清理" if verdict == "kill" else "跳过  "
        print("  [%s] pid=%-6s %-13s %s" % (mark, row["pid"], row["name"], reason))
        if verdict == "kill":
            to_kill.append(row)

    print()
    if not to_kill:
        print("没有需要清理的测试进程。")
    else:
        print("待清理：%s" % ", ".join(str(r["pid"]) for r in to_kill))
        if not args.apply:
            print("（这是预演；加 --apply 才会真的结束它们）")

    if args.apply:
        for row in to_kill:
            try:
                subprocess.run(["taskkill", "/PID", str(row["pid"]), "/T", "/F"],
                               capture_output=True, timeout=30)
                print("  已结束 pid=%s" % row["pid"])
            except Exception as exc:
                print("  结束 pid=%s 失败：%r" % (row["pid"], exc))
        time.sleep(1.0)

    print()
    temps = safe_temp_dirs()
    print("可清理的临时目录/文件（按确切名字，不用前缀扫）：")
    for path in temps:
        print("  %s" % path)
    if args.apply:
        for path in temps:
            # 双保险：再断言一次"在 %TEMP% 下、且名字在名单里"
            base = os.path.abspath(os.environ.get("TEMP") or "")
            resolved = os.path.abspath(path)
            if not resolved.startswith(base + os.sep) or \
                    os.path.basename(resolved) not in OWNED_TEMP_NAMES:
                print("  跳过（不在我的名单里）：%s" % resolved)
                continue
            try:
                import shutil
                if os.path.isdir(resolved):
                    shutil.rmtree(resolved, ignore_errors=True)
                else:
                    os.remove(resolved)
                print("  已删除 %s" % resolved)
            except Exception as exc:
                print("  删除 %s 失败：%r" % (resolved, exc))
    print()
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
