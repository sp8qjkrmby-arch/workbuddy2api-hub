# -*- coding: utf-8 -*-
"""watchdog_test.py — 内核守护 / 单实例 / 窗口几何 / 死键修复的回归测试。

安全设计（与其它测试同一套纪律）：
* 全部在临时目录里跑（`%TEMP%\\wb-watchdog`），不碰真 `shell.json`、不碰 8788；
* 不启动真内核（用桩 spawned 对象模拟"内核进程退出"）；
* 单实例测试用**临时目录**当外壳根，绝不会和真正在跑的外壳抢锁。

覆盖：
1. 内核意外退出 → 判定为"崩溃"、走守护、按退避重启（不真起内核，打桩 start）；
2. 用户手动停止 → **不算崩溃**，不会触发自动重启；
3. 守护关闭（core_watchdog=false）→ 只报告、不重启；
4. 重启次数用尽 → 停止自动恢复并给出"需要人工介入"；
5. 配置键归一化（core_watchdog / core_watchdog_retries 的钳制）;
6. 单实例：同一目录第二次拿不到锁、不同目录互不影响、命名事件能唤醒；
7. 窗口几何：解析 "1280x800" / 非法值回退默认 / 写回配置；
8. 死键 `check_update_on_launch` 真的被读（调度函数存在且能开/关）。
"""

import json
import os
import shutil
import sys
import tempfile
import time
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
              or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配（repo/shell/tests → 内核在 repo 根；生产布局在 <shell>/wb-proxy）
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))

import config as config_mod      # noqa: E402
import logging_setup             # noqa: E402
import paths                     # noqa: E402
import service as service_mod    # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "wb-watchdog")
_failed = []


def check(label, condition, extra=""):
    mark = "PASS" if condition else "FAIL"
    print("  [%s] %s%s" % (mark, label, ("  " + extra) if extra else ""))
    if not condition:
        _failed.append(label)
    return condition


def section(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


class FakeSpawned(object):
    """冒充 proc.spawn() 的返回值：只需要 poll/wait/file/pid 这几个成员。"""

    def __init__(self, code=3, pid=4242):
        self.code = code
        self.pid = pid
        self.file = iter(())          # 立即 EOF，模拟"进程没了、输出流关了"
        self.used_job = True
        self.notes = []

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def close_handles(self):
        pass


def make_service(tmp, cfg_extra=None):
    """造一个指向临时目录的 CoreService（内核目录用真的，但从不启动它）。"""
    cfg = config_mod.default_config()
    cfg.update(cfg_extra or {})
    shell = os.path.join(tmp, "shell")
    core = os.path.join(tmp, "core")
    for folder in (os.path.join(shell, "data"), os.path.join(core, "accounts"),
                   os.path.join(core, "usage")):
        os.makedirs(folder, exist_ok=True)
    shutil.copyfile(
        os.path.join(_KERNEL_DIR, "wb_proxy.py"),
        os.path.join(core, "wb_proxy.py"))
    effective = {
        "config": cfg,
        "core_dir": core,
        "accounts_dir": os.path.join(core, "accounts"),
        "usage_dir": os.path.join(core, "usage"),
        "warnings": [],
        "fatal": "",
    }
    return service_mod.CoreService(effective, shell), effective


def wait_until(predicate, timeout=6.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    logging_setup.setup(os.path.join(ROOT, "logs"))

    # ---------------------------------------------------------------- 1
    section("1. 内核意外退出 → 守护按退避重启（打桩 start，不真起内核）")
    svc, eff = make_service(os.path.join(ROOT, "case1"))
    started = []

    def fake_start():
        started.append(time.time())
        with svc.lock:
            svc.state = service_mod.RUNNING
            svc.detail = "内核已就绪（桩）"
        return True, "桩启动"

    svc.start = fake_start
    svc._watchdog_stop.wait = lambda timeout=None: False   # 不真的等 5 秒
    with svc.lock:
        svc.state = service_mod.RUNNING
        svc.started_at = time.time()
    svc._read_loop(FakeSpawned(code=3))
    check("状态变成 failed", svc.status()["state"] == service_mod.FAILED,
          svc.status()["state"])
    check("记住了退出码", svc.status()["exit_code"] == 3, str(svc.status()["exit_code"]))
    check("触发了自动重启", wait_until(lambda: len(started) >= 1), "次数=%d" % len(started))
    check("重启计数被记下", wait_until(lambda: svc.restart_count >= 1),
          str(svc.restart_count))

    # ---------------------------------------------------------------- 2
    section("2. 用户手动停止 → 不算崩溃，绝不自动重启")
    svc2, _eff2 = make_service(os.path.join(ROOT, "case2"))
    restarted = []
    svc2.start = lambda: (restarted.append(1), (True, "桩"))[1]
    svc2._watchdog_stop.wait = lambda timeout=None: False
    with svc2.lock:
        svc2.state = service_mod.RUNNING
        svc2._stop_requested = True      # stop() 会先置这个
        svc2.state = service_mod.STOPPING
    svc2._read_loop(FakeSpawned(code=0))
    time.sleep(0.3)
    check("手动停止后状态是已停止", svc2.status()["state"] == service_mod.STOPPED,
          svc2.status()["state"])
    check("没有触发自动重启", not restarted, "重启次数=%d" % len(restarted))
    check("重启计数仍为 0", svc2.restart_count == 0, str(svc2.restart_count))

    # ---------------------------------------------------------------- 3
    section("3. 守护关闭（core_watchdog=false）→ 只报告、不重启")
    svc3, _eff3 = make_service(os.path.join(ROOT, "case3"),
                               {"core_watchdog": False})
    restarted3 = []
    svc3.start = lambda: (restarted3.append(1), (True, "桩"))[1]
    svc3._watchdog_stop.wait = lambda timeout=None: False
    check("服务里守护是关的", svc3.watchdog is False)
    with svc3.lock:
        svc3.state = service_mod.RUNNING
        svc3._stop_requested = False
    lines = []
    svc3.subscribe(lambda line: lines.append(line))
    svc3._read_loop(FakeSpawned(code=1))
    time.sleep(0.3)
    check("仍然判定为意外退出（状态 failed）",
          svc3.status()["state"] == service_mod.FAILED)
    check("没有自动重启", not restarted3)
    check("明确告诉用户守护已关闭",
          any("守护已关闭" in line for line in lines),
          " | ".join(lines[-2:]))

    # ---------------------------------------------------------------- 4
    section("4. 重启次数用尽 → 停止自动恢复并提示人工介入")
    svc4, _eff4 = make_service(os.path.join(ROOT, "case4"),
                               {"core_watchdog_retries": 1})
    attempts = []
    svc4.start = lambda: (attempts.append(1), (False, "模拟启动失败"))[1]
    svc4._watchdog_stop.wait = lambda timeout=None: False
    with svc4.lock:
        svc4.state = service_mod.RUNNING
    svc4._read_loop(FakeSpawned(code=2))
    wait_until(lambda: len(attempts) >= 1, timeout=4)
    time.sleep(0.5)
    check("尝试过一次自动重启", len(attempts) == 1, "次数=%d" % len(attempts))
    check("配额用尽后给出人工介入提示",
          "人工介入" in svc4.status()["watchdog_note"],
          svc4.status()["watchdog_note"])
    check("不再继续重启", len(attempts) == 1, "次数=%d" % len(attempts))

    # ---------------------------------------------------------------- 5
    section("5. 配置归一化：core_watchdog / core_watchdog_retries")
    cfg = config_mod.default_config()
    cfg["core_watchdog"] = "false"
    cfg["core_watchdog_retries"] = "99"
    warnings = []
    config_mod._normalise(cfg, warnings)
    check("字符串 false 被归一成布尔假", cfg["core_watchdog"] is False,
          repr(cfg["core_watchdog"]))
    check("重试次数被钳到 10", cfg["core_watchdog_retries"] == 10,
          str(cfg["core_watchdog_retries"]))
    cfg2 = config_mod.default_config()
    cfg2["core_watchdog_retries"] = "-5"
    config_mod._normalise(cfg2, [])
    check("负数被钳到 0", cfg2["core_watchdog_retries"] == 0,
          str(cfg2["core_watchdog_retries"]))
    check("默认开着守护且重试 3 次",
          config_mod.DEFAULTS["core_watchdog"] is True
          and config_mod.DEFAULTS["core_watchdog_retries"] == 3)

    # ---------------------------------------------------------------- 6
    section("6. 单实例：同目录互斥、不同目录独立、命名事件")
    dir_a = os.path.join(ROOT, "inst-a")
    dir_b = os.path.join(ROOT, "inst-b")
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)
    first = paths.acquire_single_instance(dir_a)
    check("第一个实例拿到锁", first["acquired"] is True and first["first"] is True,
          repr(first))
    second = paths.acquire_single_instance(dir_a)
    check("同目录第二个实例拿不到", second["acquired"] is False, repr(second))
    other = paths.acquire_single_instance(dir_b)
    check("不同目录互不影响", other["acquired"] is True, repr(other))
    handle, err = paths.create_wakeup_event(dir_a)
    check("能创建叫窗口的命名事件", handle is not None, err or "")
    ok, why = paths.signal_existing_instance(dir_a)
    check("第二个实例能发唤醒请求", ok, why)
    if handle is not None:
        check("第一个实例能收到唤醒", paths.wait_wakeup_event(handle, 2000))
        check("没请求时等待会超时放行",
              paths.wait_wakeup_event(handle, 100) is False)

    # ---------------------------------------------------------------- 7
    section("7. 窗口几何：解析 / 回退 / 写回配置")
    import ui_web
    svc7, eff7 = make_service(os.path.join(ROOT, "case7"))
    win = ui_web.WebWindow(os.path.join(ROOT, "case7", "shell"),
                           eff7["config"], eff7, svc7)
    check("默认尺寸是 DEFAULT_SIZE", win._initial_size() == ui_web.DEFAULT_SIZE,
          "%s vs %s" % (win._initial_size(), ui_web.DEFAULT_SIZE))
    eff7["config"]["window_geometry"] = "1440x900"
    check("读得懂 1440x900", win._initial_size() == (1440, 900),
          str(win._initial_size()))
    eff7["config"]["window_geometry"] = "1440x900+120+80"
    check("带位置也能解析出宽高", win._initial_size() == (1440, 900),
          str(win._initial_size()))
    eff7["config"]["window_geometry"] = "abc"
    check("坏值回退默认", win._initial_size() == ui_web.DEFAULT_SIZE,
          str(win._initial_size()))
    eff7["config"]["window_geometry"] = "100x100"
    check("太小的值被钳到最小尺寸", win._initial_size() == (880, 560),
          str(win._initial_size()))

    class FakeWindow(object):
        width = 1500
        height = 950

    eff7["config"]["window_geometry"] = ""
    got = win._remember_geometry(FakeWindow())
    check("从窗口对象记下宽高", got == "1500x950", got)
    check("写进了配置", eff7["config"]["window_geometry"] == "1500x950",
          eff7["config"]["window_geometry"])

    class TinyWindow(object):
        width = 0
        height = 0

    eff7["config"]["window_geometry"] = "1200x800"
    got2 = win._remember_geometry(TinyWindow())
    check("拿不到真实尺寸时不写入 0x0", got2 == "" and
          eff7["config"]["window_geometry"] == "1200x800",
          "%r / %r" % (got2, eff7["config"]["window_geometry"]))

    ok, message = win.save_geometry()
    check("保存几何写盘成功（临时目录）", ok, message)
    shell_json = os.path.join(ROOT, "case7", "shell", "data", "shell.json")
    saved = json.load(open(shell_json, encoding="utf-8"))
    check("shell.json 里是新的宽高",
          saved.get("window_geometry") == "1200x800",
          repr(saved.get("window_geometry")))
    mtime_before = os.path.getmtime(shell_json)
    time.sleep(0.05)
    ok2, _msg2 = win.save_geometry()
    check("值没变时第二次不写盘（mtime 不动）",
          ok2 and os.path.getmtime(shell_json) == mtime_before,
          "mtime=%r" % os.path.getmtime(shell_json))

    # ---------------------------------------------------------------- 8
    section("8. check_update_on_launch 不再是死键")
    src = open(os.path.join(_SHELL_DIR, "launcher", "__main__.py"),
               encoding="utf-8").read()
    check("__main__ 里有调度函数", "def schedule_launch_update_check" in src)
    check("main 里真的调用了它",
          "schedule_launch_update_check(effective, service, tray)" in src)
    calls = []

    class FakePlan(object):
        available = False
        current_version = "1.7.12"
        latest_version = "1.7.12"

    class FakeUpdater(object):
        def __init__(self, *a, **k):
            pass

        def check(self):
            calls.append("check")
            return FakePlan()

    original = sys.modules.get("updater")
    sys.modules["updater"] = type("M", (), {"Updater": FakeUpdater,
                                            "UpdateError": RuntimeError})
    try:
        # 本测试自己也叫 __main__，所以不能 __import__("__main__")——
        # 必须按文件把 launcher/__main__.py 当成独立模块加载。
        import importlib.util
        entry_path = os.path.join(_SHELL_DIR, "launcher", "__main__.py")
        spec = importlib.util.spec_from_file_location("wb_entry_watchdog",
                                                      entry_path)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        off = entry.schedule_launch_update_check(
            {"config": {"check_update_on_launch": False}}, svc7, None)
        check("关着时不启动检查", off is None and not calls,
              "thread=%r calls=%r" % (off, calls))
        entry.LAUNCH_UPDATE_DELAY = 0.0
        thread = entry.schedule_launch_update_check(
            {"config": {"check_update_on_launch": True}}, svc7, None)
        check("开着时会启动后台检查", thread is not None)
        if thread is not None:
            thread.join(timeout=5)
        check("真的去查了", calls == ["check"], repr(calls))
    finally:
        if original is not None:
            sys.modules["updater"] = original
        else:
            sys.modules.pop("updater", None)

    # ---------------------------------------------------------------- 结论
    section("结论")
    print("  隔离根：%s" % ROOT)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
