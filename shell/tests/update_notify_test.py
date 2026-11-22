# -*- coding: utf-8 -*-
"""update_notify_test.py — 更新提醒（A/B/C/D）的回归测试。

覆盖：
1. 节流：15 分钟内不重复联网；`force=True` 可绕过；
2. 同一版本只提醒一次（`notified_version`）；
3. 有新版本时才提醒；"已是最新"不打扰、不写 notified；
4. 网络失败静默（只写日志、不抛、不弹）；
5. `cached_plan()` 纯本地：不联网也能从 update-state.json 拼出"有新版本"；
6. 托盘悬停文字 / 菜单标签带上「有新版 X」；
7. 底栏 payload 带 `pending_version`；
8. 配置默认值与钳制（check_update_on_launch / update_check_hours）。

全部在临时目录，不联网（网络调用被打桩），不碰真 shell.json。
"""

import io
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

import config as config_mod      # noqa: E402
import logging_setup             # noqa: E402
import updater as updater_mod    # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "wb-update-notify")
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


def make_env(name, current="1.7.15"):
    """造一个临时外壳/内核 + 一份 shell.json，返回 (shell, effective, service)。

    **注意**：`Updater.__init__` 内部用 `paths.data_dir()`（按模块自身位置推导），
    不看我们传的 `shell`。所以凡是要经过 Updater 的地方，都必须显式把
    `upd.data_dir` / `state_path` 指到临时目录——否则会写到真实外壳的账本里
    （`update_isolated_test.py` 就是这么隔离的，这里同规矩）。
    """
    import service as service_mod
    root = os.path.join(ROOT, name)
    shell = os.path.join(root, "shell")
    core = os.path.join(root, "core")
    for folder in (os.path.join(shell, "data"), os.path.join(core, "accounts"),
                   os.path.join(core, "usage")):
        os.makedirs(folder, exist_ok=True)
    with io.open(os.path.join(core, "wb_proxy.py"), "w", encoding="utf-8") as fh:
        fh.write('CONFIG = {"version": "%s"}\n' % current)
    cfg = config_mod.default_config()
    effective = {
        "config": cfg,
        "core_dir": core,
        "accounts_dir": os.path.join(core, "accounts"),
        "usage_dir": os.path.join(core, "usage"),
        "warnings": [],
        "fatal": "",
    }
    service = service_mod.CoreService(effective, shell)
    return shell, effective, service


def make_updater(effective, service, shell):
    """造一个**隔离**的 Updater：账本落在临时外壳目录里。"""
    upd = updater_mod.Updater(effective, service, shell)
    upd.data_dir = os.path.join(shell, "data")
    upd.state_path = os.path.join(upd.data_dir, "update-state.json")
    upd.staging = os.path.join(upd.data_dir, "staging")
    upd.backups = os.path.join(upd.data_dir, "backups")
    return upd


class FakePlan(object):
    def __init__(self, latest="1.8.0", current="1.7.15"):
        self.latest_version = latest
        self.current_version = current
        self.tag = "v" + latest if latest else ""
        self.release_url = ""
        self.asset_name = ""
        self.expected_sha = ""
        self.asset_size = 0
        self.mirror = ""
        self.sha_source = "测试"
        self.notes = []

    @property
    def available(self):
        return bool(self.latest_version) and updater_mod.is_newer(
            self.latest_version, self.current_version)


def install_fake_network(plans=None, error=None, calls=None, data_dir=None):
    """把 Updater.check 换成桩；记录调用次数。

    `data_dir` 非空时同时把 `__init__` 里的账本路径钉到该目录——
    这样 `check_update_and_notify` 内部 `new Updater(...)` 也不会写到真账本。
    """
    plans = plans or []
    state = {"index": 0}

    def fake_check(self, timeout=None):
        if calls is not None:
            calls.append(time.time())
        if error is not None:
            raise error
        if not plans:
            return FakePlan(latest="", current=self.current_version())
        plan = plans[min(state["index"], len(plans) - 1)]
        state["index"] += 1
        self._write_state({"last_check": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "latest_version": plan.latest_version,
                           "current_version": plan.current_version,
                           "tag": plan.tag})
        return plan

    updater_mod.Updater.check = fake_check
    if data_dir:
        original_init = updater_mod.Updater.__orig_init__

        def fake_init(self, effective, service=None, shell_path=None):
            original_init(self, effective, service, shell_path)
            import os as _os
            self.data_dir = data_dir
            self.state_path = _os.path.join(data_dir, "update-state.json")
            self.staging = _os.path.join(data_dir, "staging")
            self.backups = _os.path.join(data_dir, "backups")

        updater_mod.Updater.__init__ = fake_init


def restore_network():
    updater_mod.Updater.check = updater_mod.Updater.__orig_check__
    updater_mod.Updater.__init__ = updater_mod.Updater.__orig_init__


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    logging_setup.setup(os.path.join(ROOT, "logs"))

    import __main__ as entry_mod
    entry = entry_mod
    # 本测试自己就是 __main__，必须按文件加载 launcher/__main__.py
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wb_entry_notify", os.path.join(_SHELL_DIR, "launcher", "__main__.py"))
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)

    original_check = updater_mod.Updater.check
    original_init = updater_mod.Updater.__init__
    updater_mod.Updater.__orig_check__ = original_check
    updater_mod.Updater.__orig_init__ = original_init

    # ---------------------------------------------------------------- 1
    section("1. 节流：15 分钟内不重复联网，force 可绕过")
    shell1, eff1, svc1 = make_env("throttle")
    calls1 = []
    install_fake_network([FakePlan(latest="1.8.0")], calls=calls1,
                         data_dir=os.path.join(shell1, "data"))
    try:
        r1 = entry.check_update_and_notify(eff1, svc1, tray=None,
                                           log=lambda *a, **k: None,
                                           source="测试1")
        check("第一次真的联网了", len(calls1) == 1, "calls=%d" % len(calls1))
        check("发现了新版本", r1["available"] is True, str(r1))
        r2 = entry.check_update_and_notify(eff1, svc1, tray=None,
                                           log=lambda *a, **k: None,
                                           source="测试2")
        check("第二次被节流、没再联网", len(calls1) == 1, "calls=%d" % len(calls1))
        check("节流时说明是刚查过", r2["message"] == "刚刚检查过", r2["message"])
        r3 = entry.check_update_and_notify(eff1, svc1, tray=None,
                                           log=lambda *a, **k: None,
                                           source="测试3", force=True)
        check("force=True 能绕过节流", len(calls1) == 2, "calls=%d" % len(calls1))
        check("force 后仍然报告有新版本", r3["available"] is True)
    finally:
        restore_network()

    # ---------------------------------------------------------------- 2
    section("2. 同一版本只提醒一次")
    shell2, eff2, svc2 = make_env("once")
    calls2 = []
    install_fake_network([FakePlan(latest="1.8.0")], calls=calls2,
                         data_dir=os.path.join(shell2, "data"))
    notified = []

    class FakeTray(object):
        def notify(self, message, title="WorkBuddy 网关"):
            notified.append(message)

    try:
        r1 = entry.check_update_and_notify(eff2, svc2, tray=FakeTray(),
                                           log=lambda *a, **k: None,
                                           source="测试1", force=True)
        check("第一次提醒了", r1["notified"] is True and len(notified) == 1,
              "notified=%r" % notified)
        r2 = entry.check_update_and_notify(eff2, svc2, tray=FakeTray(),
                                           log=lambda *a, **k: None,
                                           source="测试2", force=True)
        check("第二次不再提醒同一个版本", r2["notified"] is False,
              str(r2))
        check("气泡只弹过一次", len(notified) == 1, "count=%d" % len(notified))
        check("但仍然报告有新版本（状态没丢）", r2["available"] is True)
        state = updater_mod.Updater(eff2, svc2, shell2).read_state()
        check("notified_version 已落盘", state.get("notified_version") == "1.8.0",
              repr(state.get("notified_version")))
    finally:
        restore_network()

    # ---------------------------------------------------------------- 3
    section("3. 已是最新：不打扰、不写 notified")
    shell3, eff3, svc3 = make_env("latest")
    notified3 = []
    install_fake_network([], data_dir=os.path.join(shell3, "data"))
    try:
        r = entry.check_update_and_notify(eff3, svc3, tray=FakeTray(),
                                          log=lambda *a, **k: None,
                                          source="测试", force=True)
        check("没有新版本", r["available"] is False, str(r))
        check("没有弹气泡", not notified3)
        state = updater_mod.Updater(eff3, svc3, shell3).read_state()
        check("没有写 notified_version", not state.get("notified_version"),
              repr(state.get("notified_version")))
    finally:
        restore_network()

    # ---------------------------------------------------------------- 4
    section("4. 网络失败：静默，不抛、不弹")
    shell4, eff4, svc4 = make_env("offline")
    notified4 = []

    class Tray4(object):
        def notify(self, message, title=""):
            notified4.append(message)

    lines = []
    install_fake_network(error=updater_mod.UpdateError("模拟断网"),
                         data_dir=os.path.join(shell4, "data"))
    try:
        r = entry.check_update_and_notify(eff4, svc4, tray=Tray4(),
                                          log=lambda msg, level="INFO": lines.append(msg),
                                          source="测试", force=True)
        check("返回失败但没抛异常", r["ok"] is False, str(r))
        check("没有弹气泡", not notified4)
        check("写了日志（可追）", any("失败" in line for line in lines), repr(lines))
    finally:
        restore_network()

    # ---------------------------------------------------------------- 5
    section("5. cached_plan：不联网也能知道有新版本")
    shell5, eff5, svc5 = make_env("cached", current="1.7.15")
    up5 = make_updater(eff5, svc5, shell5)
    up5._write_state({"last_check": "2026-10-18 22:00:00",
                      "latest_version": "1.9.0",
                      "current_version": "1.7.15",
                      "tag": "v1.9.0"})
    plan = up5.cached_plan()
    check("能拼出 plan", plan is not None)
    if plan is not None:
        check("版本号对", plan.latest_version == "1.9.0", plan.latest_version)
        check("available 为真", plan.available is True)
    up5._write_state({"latest_version": "1.7.15"})   # 与当前相同
    check("同版本不算新版", up5.cached_plan() is None)
    up5._write_state({"latest_version": ""})
    check("没有记录时返回 None", up5.cached_plan() is None)

    # ---------------------------------------------------------------- 6
    section("6. 托盘：悬停文字与菜单标签带上「有新版 X」")
    import tray as tray_mod
    shell6, eff6, svc6 = make_env("tray")
    up6 = make_updater(eff6, svc6, shell6)
    up6._write_state({"last_check": "2026-10-18 22:00:00",
                      "latest_version": "2.0.0", "current_version": "1.7.15"})
    t = tray_mod.Tray(shell6, eff6["config"], eff6, svc6)
    check("_pending_version 读到了缓存版本", t._pending_version() == "2.0.0",
          t._pending_version())
    line = t._status_line()
    check("悬停文字里带「有新版 2.0.0」", "有新版 2.0.0" in line, line)
    # 2026-10-18：菜单文案改成「安装新版 X…（内核会重启）」——把代价写在标签上
    label = t._check_or_install_label()
    check("菜单标签提示可安装且写明代价",
          "2.0.0" in label and "重启" in label, label)
    up6._write_state({"latest_version": "1.7.15"})   # 没有新版
    check("没有新版时菜单显示「检查更新」",
          t._check_or_install_label() == "检查更新",
          t._check_or_install_label())
    check("没有新版时悬停文字不提新版", "有新版" not in t._status_line(),
          t._status_line())

    # ---------------------------------------------------------------- 7
    section("7. 底栏 payload 带 pending_version")
    import ui_web
    shell7, eff7, svc7 = make_env("bar")
    up7 = make_updater(eff7, svc7, shell7)
    up7._write_state({"last_check": "2026-10-18 22:00:00",
                      "latest_version": "3.0.0", "current_version": "1.7.15"})
    win = ui_web.WebWindow(shell7, eff7["config"], eff7, svc7)
    payload = win._state_payload()
    check("payload 里有 pending_version 字段", "pending_version" in payload,
          str(sorted(payload.keys())))
    check("值是缓存的新版本", payload.get("pending_version") == "3.0.0",
          repr(payload.get("pending_version")))
    up7._write_state({"latest_version": ""})
    check("没有新版时为空串",
          win._state_payload().get("pending_version") == "",
          repr(win._state_payload().get("pending_version")))

    # ---------------------------------------------------------------- 8
    section("8. 配置默认值与钳制")
    check("check_update_on_launch 默认开启（D）",
          config_mod.DEFAULTS["check_update_on_launch"] is True)
    check("update_check_hours 默认 6",
          config_mod.DEFAULTS["update_check_hours"] == 6)
    cfg = config_mod.default_config()
    cfg["update_check_hours"] = "999"
    config_mod._normalise(cfg, [])
    check("间隔上限一周", cfg["update_check_hours"] == 24 * 7,
          str(cfg["update_check_hours"]))
    cfg2 = config_mod.default_config()
    cfg2["update_check_hours"] = "-3"
    config_mod._normalise(cfg2, [])
    check("负数归零（=关闭周期检查）", cfg2["update_check_hours"] == 0,
          str(cfg2["update_check_hours"]))
    check("节流常量是 15 分钟", updater_mod.CHECK_MIN_INTERVAL == 900,
          str(updater_mod.CHECK_MIN_INTERVAL))

    # ---------------------------------------------------------------- 结论
    section("结论")
    # 底线断言：整整一轮测试不许碰到真实外壳的账本
    real_state = os.path.join(_SHELL_DIR, "data", "update-state.json")
    if os.path.isfile(real_state):
        with io.open(real_state, "r", encoding="utf-8") as fh:
            raw = fh.read()
        polluted = any(mark in raw for mark in ("1.8.0", "2.0.0", "3.0.0"))
        check("真 update-state.json 没有被本测试改写", not polluted,
              "含测试版本号" if polluted else "干净")
    print("  隔离根：%s" % ROOT)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
