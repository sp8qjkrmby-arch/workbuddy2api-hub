# -*- coding: utf-8 -*-
"""relaunch_test.py — 钉住「重启外壳」这条链路：**拉起 + 确认起来了 + 才退出**。

真实故障（2026-10-14 生产日志）：点托盘「重启外壳」之后
    21:41:05  已拉起新外壳，当前进程退出      ← 日志这么说
    21:42:52  外壳启动 v0.1.0                ← 107 秒后才起来，而且是**人手动开的**
也就是说那次"重启"实际效果是"内核停了、外壳也没了"。

原因：老写法用 `powershell -Command "Start-Sleep 800; & pythonw launcher"` 包了一层，
而父进程紧接着 `os._exit(0)` —— 实测被 powershell 拉起的孙子进程**活不下来**；
直接 `CreateProcess` 一个 pythonw（GUI 子系统）则没事。同一份实测里 A 组挂、B 组过。

本测试用一个**假外壳目录**（自己写一个会打一行"外壳启动 v…"的 launcher）把这条
链路完整跑一遍，不碰真外壳、不真 os._exit、不弹真提示框：

1. 成功路径：拉起 → 等到启动日志 → 才 `_EXIT(0)`；
2. 失败路径：假 launcher 什么都不打 → **不退出**、返回失败、并且弹了提示框（被打桩记录）；
3. 源码里不能再用那层 powershell wrapper。

用法：runtime\\python.exe tests\\relaunch_test.py
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
               or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配（repo/shell/tests → 内核在 repo 根；生产布局在 <shell>/wb-proxy）
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))

import logging_setup             # noqa: E402

_failed = []

FAKE_LAUNCHER = '''# -*- coding: utf-8 -*-
"""假外壳：只往日志里打一行启动标记，然后退出（测试用）。"""
import os
import sys
import time

mode = os.environ.get("WB_FAKE_LAUNCHER_MODE", "ok")
path = os.environ.get("WB_FAKE_LOG", "")
if mode == "ok" and path:
    with open(path, "a", encoding="utf-8", newline="\\n") as handle:
        handle.write("%s  INFO   外壳启动 v9.9.9（fake launcher）\\n"
                     % time.strftime("%Y-%m-%d %H:%M:%S"))
sys.exit(0)
'''


def check(label, cond, extra=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + extra) if extra else ""))
    if not cond:
        _failed.append(label)


def load_entry():
    path = os.path.join(_SHELL_DIR, "launcher", "__main__.py")
    spec = importlib.util.spec_from_file_location("wb_entry_relaunch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    root = os.path.join(os.environ["TEMP"], "wb-relaunch")
    shutil.rmtree(root, ignore_errors=True)
    data = os.path.join(root, "data")
    shell = os.path.join(root, "shell")            # 假外壳目录（含 launcher\）
    launcher_dir = os.path.join(shell, "launcher")
    os.makedirs(data, exist_ok=True)
    os.makedirs(launcher_dir, exist_ok=True)
    with open(os.path.join(launcher_dir, "__main__.py"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(FAKE_LAUNCHER)
    log_path = logging_setup.setup(data)
    os.environ["WB_FAKE_LOG"] = log_path

    entry = load_entry()
    # 把时间常数压小，让失败路径也在几秒内跑完
    entry.RELAUNCH_PAUSE = 0.05
    entry.RELAUNCH_TIMEOUT = 2.5
    entry.RELAUNCH_ATTEMPTS = 2

    exits = []
    warns = []
    entry._EXIT = lambda code: exits.append(code)
    entry._warn_restart_failed = lambda detail: warns.append(detail)

    def log_text():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except Exception:
            return ""

    print()
    print("=" * 72)
    print("1. 成功路径：拉起 → 见到启动日志 → 才退出")
    print("=" * 72)
    os.environ["WB_FAKE_LAUNCHER_MODE"] = "ok"
    result = entry._spawn_replacement_shell(shell)
    print("    返回：%r；_EXIT 调用：%r" % (result, exits))
    check("假 launcher 真的跑起来了（日志里有启动行）",
          "外壳启动 v" in log_text())
    check("确认之后才退出（_EXIT(0) 恰好一次）", exits == [0], str(exits))
    check("成功路径不弹提示框", not warns, str(warns))

    print()
    print("=" * 72)
    print("2. 失败路径：假 launcher 什么都不打 → 不许偷偷退出")
    print("=" * 72)
    exits.clear()
    warns.clear()
    os.environ["WB_FAKE_LAUNCHER_MODE"] = "quiet"
    before = time.time()
    result = entry._spawn_replacement_shell(shell)
    took = time.time() - before
    print("    返回：%r；_EXIT 调用：%r；提示框：%r（耗时 %.1fs）"
          % (result, exits, warns, took))
    check("返回失败而不是假装成功",
          isinstance(result, tuple) and result[0] is False
          and "重启失败" in result[1], str(result))
    check("**没有**调用 _EXIT（进程不该悄悄消失）", exits == [], str(exits))
    check("弹了「重启失败」的提示框（打桩记录）",
          bool(warns) and "没等到" in warns[0], str(warns))
    check("按配置重试了（不是一次就放弃）",
          log_text().count("没等到新外壳的启动日志") >= 1,
          "重试日志条数=%d" % log_text().count("没等到新外壳的启动日志"))

    print()
    print("=" * 72)
    print("3. 源码形状：不许再回到那层 powershell wrapper")
    print("=" * 72)
    entry_source = open(os.path.join(_SHELL_DIR, "launcher", "__main__.py"),
                        encoding="utf-8").read()
    check("没有 powershell wrapper 的写法",
          '"powershell"' not in entry_source
          and "Start-Sleep -Milliseconds" not in entry_source)
    check("直接拉起 pythonw（Popen 的第一项就是它）",
          "[pythonw, launcher]" in entry_source)
    check("退出前先等新外壳的启动日志",
          "_wait_for_shell_start(log_path, offset)" in entry_source)

    print()
    print("  隔离根：%s" % root)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
