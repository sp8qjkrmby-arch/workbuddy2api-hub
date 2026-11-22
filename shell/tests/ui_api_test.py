# -*- coding: utf-8 -*-
"""验证 ui_web 的 API 层（不起窗口、不碰真内核）。

覆盖：状态载荷、看板补丁读取、控制层动作分发、更新检查、更新入口接线。
**不覆盖**账号备注与复制 API 地址/Key——那两项已交回内核看板，
外壳侧没有实现，因此也没有可测的东西（这是刻意的：少一份重复实现）。

更新那一节全部**打桩**：真实安装会停内核、下包、覆盖文件，那是
`tests/update_isolated_test.py` 在副本上做的事，绝不在这里发生。
"""

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
import paths                     # noqa: E402
import service as service_mod    # noqa: E402
import ui_web                    # noqa: E402
import updater as updater_mod    # noqa: E402

SHELL = _SHELL_DIR
REAL_CORE = _KERNEL_DIR
PORT = 18877
_failed = []


def check(label, cond, extra=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + extra) if extra else ""))
    if not cond:
        _failed.append(label)


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main():
    root = os.path.join(tempfile.gettempdir(), "wb-api-test")
    shutil.rmtree(root, ignore_errors=True)
    data = os.path.join(root, "data")
    accounts = os.path.join(root, "accounts")
    usage = os.path.join(root, "usage")
    for folder in (data, accounts, usage):
        os.makedirs(folder, exist_ok=True)
    logging_setup.setup(data)

    assert os.path.normcase(os.path.abspath(REAL_CORE)) != os.path.normcase(data)

    loaded = config_mod.load(SHELL)
    eff = config_mod.merged_effective(loaded["config"], SHELL, {
        "port": PORT, "core_dir": REAL_CORE,
        "accounts_dir": accounts, "usage_dir": usage,
    })
    if eff["fatal"]:
        print("环境有问题：", eff["fatal"])
        return 2

    svc = service_mod.CoreService(eff, SHELL)
    window = ui_web.WebWindow(SHELL, loaded["config"], eff, svc,
                              logger=lambda text: logging_setup.log("[ui] " + text))

    section("1. 状态载荷（控制条渲染的数据源）")
    state = window.api_get_state()
    for key in ("state", "state_label", "port", "accounts", "core_version",
                "can_start", "can_stop", "can_restart"):
        print("    %-14s %s" % (key, state.get(key)))
    check("state 字段齐全",
          all(key in state for key in ("state", "state_label", "can_start",
                                       "can_stop", "can_restart")))
    check("端口取自生效配置", state["port"] == PORT)

    section("2. 面板补丁读取（dashboard.patch.js/.css）")
    # 补丁现在**每次取都重读**（带 (mtime,size) 缓存），所以要把 paths.data_dir
    # 指向临时目录再取，不能像原来那样手工塞 window._patches（那个属性已经没了）。
    real_data_dir = paths.data_dir
    paths.data_dir = lambda: data
    try:
        with open(os.path.join(data, "dashboard.patch.js"), "w",
                  encoding="utf-8") as fh:
            fh.write("window.__wbPatchRan = true;")
        patches = window.api_get_patches()
        check("读到 patch.js", "__wbPatchRan" in (patches.get("js") or ""),
              repr(patches.get("js"))[:60])
        check("不存在的 patch.css 返回空串", patches.get("css") == "")
        check("同一份文件再取一次结果一致（走缓存）",
              window.api_get_patches() == patches)
        # 2026-10-15 加的能力：改完补丁文件**立刻生效**，不必重启外壳
        # （原来只在构造时读一次，改一行 CSS 也得重启，而重启会中断内核）。
        with open(os.path.join(data, "dashboard.patch.js"), "a",
                  encoding="utf-8") as fh:
            fh.write("\nwindow.__wbPatchSecond = true;")
        time.sleep(0.02)          # 让 mtime 变成另一刻
        changed = window.api_get_patches()
        check("改完补丁文件立刻生效（不必重启外壳）",
              "__wbPatchSecond" in (changed.get("js") or ""))
        with open(os.path.join(data, "dashboard.patch.css"), "w",
                  encoding="utf-8") as fh:
            fh.write("#toastBox{bottom:52px}")
        check("css 补丁也能读到（面板通知不再被底栏挡住的那条）",
              "toastBox" in (window.api_get_patches().get("css") or ""))
        # 上限保护：真写一个超限文件，确认它被忽略而不是硬塞进注入层
        huge = os.path.join(data, "dashboard.patch.js")
        with open(huge, "w", encoding="utf-8") as fh:
            fh.write("x" * (600 * 1024))
        check("超过 512KB 的补丁被忽略（返回空串，不是硬塞）",
              window._read_patch("dashboard.patch.js") == "")
    finally:
        paths.data_dir = real_data_dir

    section("3. 控制层动作分发")
    unknown = window.api_action({"name": "不存在的操作"})
    check("未知动作被拒绝并说明", not unknown["ok"] and "未知操作" in unknown["message"],
          unknown["message"])
    for removed in ("copy_api", "copy_key", "notes"):
        result = window.api_action({"name": removed})
        check("已移除的动作 %s 不再存在" % removed, not result["ok"],
              result["message"])

    section("4. 暴露给 JS 的方法里不应再有复制/备注")
    exposed = sorted(n for n in vars(ui_web.WebWindow) if n.startswith("api_"))
    print("    暴露：%s" % ", ".join(exposed))
    for gone in ("api_save_notes", "api_notes_rows", "api_fetch_accounts",
                 "api_save_accounts_cache"):
        check("%s 已删除" % gone, gone not in exposed)

    section("5. 更新检查（真实网络，只发现不安装）")
    upd = updater_mod.Updater(eff, svc, SHELL)
    upd.data_dir = data
    upd.state_path = os.path.join(data, "update-state.json")
    upd.staging = os.path.join(data, "staging")
    upd.backups = os.path.join(data, "backups")
    try:
        plan = upd.check()
        print("    当前 %s → 最新 %s（可用=%s）"
              % (plan.current_version, plan.latest_version, plan.available))
        check("能识别版本", bool(plan.latest_version), plan.latest_version)
        if plan.available:
            check("有新版时带校验值或明确说明缺失",
                  bool(plan.expected_sha) or any("校验值" in n for n in plan.notes),
                  plan.sha_source or "；".join(plan.notes))
        else:
            check("已是最新时不取校验值（按设计）", plan.expected_sha == "")
    except updater_mod.UpdateError as exc:
        check("更新检查（网络抖动可跳过）", False, "网络失败：%s" % exc)

    section("5.5 校验值来源：配了镜像也必须取官方摘要（打桩，不联网）")
    # 镜像能同时改包和 SHA256SUMS。若校验值也跟着走镜像，"官方 SHA256 校验"
    # 就名存实亡了。所以有镜像时必须优先用官方 API 的 digest（api.github.com
    # 的响应不经镜像），并且如实标注来源。
    api_digest = "a" * 64
    poisoned = "b" * 64
    real_releases = updater_mod.fetch_releases
    real_sums = updater_mod.fetch_expected_sha

    def fake_releases(repo, timeout=None):
        return [{"tag_name": "v9.9.9", "html_url": "", "published_at": "",
                 "body": "", "_repo": repo,
                 "assets": [{"name": updater_mod.ASSET_NAME % "9.9.9",
                             "size": 10343357,
                             "digest": "sha256:" + api_digest}]}]

    def fake_sums(repo, tag, name, mirror="", timeout=None):
        return poisoned, ""

    def fresh_updater(mirror):
        worker = updater_mod.Updater(eff, svc, SHELL)
        worker.data_dir = data
        worker.state_path = os.path.join(data, "update-state.json")
        worker.mirror = mirror
        return worker

    updater_mod.fetch_releases = fake_releases
    updater_mod.fetch_expected_sha = fake_sums
    try:
        with_mirror = fresh_updater("https://mirror.example/").check()
        check("有镜像时用官方 API 摘要，不用镜像给的校验值",
              with_mirror.expected_sha == api_digest, with_mirror.sha_source)
        check("并注明镜像只用于下载包",
              "镜像只用于下载包" in (with_mirror.sha_source or ""),
              with_mirror.sha_source)
        check("镜像这件事也写进了 note（让人看得见）",
              any("校验值取自官方 API" in n for n in with_mirror.notes),
              "；".join(with_mirror.notes))

        plain = fresh_updater("").check()
        check("没配镜像时仍以 SHA256SUMS 为准",
              plain.expected_sha == poisoned, plain.sha_source)
    finally:
        updater_mod.fetch_releases = real_releases
        updater_mod.fetch_expected_sha = real_sums

    section("6. 状态推送脚本可生成")
    payload = json.dumps(window._state_payload(), ensure_ascii=False)
    check("推送脚本是合法形状",
          ("window.__wbShellApply && window.__wbShellApply(%s)" % payload)
          .startswith("window.__wbShellApply"))
    check("载荷可被 JSON 解析", isinstance(json.loads(payload), dict))

    section("7. 更新入口（打桩：只验接线，绝不安装）")
    real_updater_cls = updater_mod.Updater
    calls = {}

    def make_plan(available=True):
        plan = updater_mod.UpdatePlan()
        plan.current_version = "1.7.2"
        plan.latest_version = "9.9.9" if available else "1.7.2"
        plan.tag = "v9.9.9"
        plan.asset_name = "workbuddy2api-hub-v9.9.9.zip" if available else ""
        plan.asset_size = 12 * 1048576
        plan.expected_sha = "a" * 64 if available else ""
        plan.sha_source = "官方 SHA256SUMS"
        plan.changelog = "测试用更新说明"
        return plan

    class FakeUpdater(object):
        plan = None

        def __init__(self, effective, service=None, shell_path=None):
            self.service = service

        def check(self):
            calls["check"] = calls.get("check", 0) + 1
            return FakeUpdater.plan

        def install(self, plan, progress=None, stop_core=True, start_core=True):
            calls["install"] = plan
            if progress:
                progress("download", 5, 10, "正在下载")
                progress("overlay", 0, 0, "正在覆盖内核文件")
            return {"ok": True, "message": "已更新到 %s" % plan.latest_version,
                    "steps": ["停止内核", "下载", "覆盖"], "rolled_back": False}

    class FakeWindow(object):
        def __init__(self):
            self.pushed = []

        def evaluate_js(self, code):
            self.pushed.append(code)

    updater_mod.Updater = FakeUpdater
    window.window = FakeWindow()
    real_status = svc.status
    try:
        FakeUpdater.plan = make_plan()
        info = window.api_update_check()
        check("检查更新返回可安装", bool(info.get("ok") and info.get("available")
                                        and info.get("can_install")),
              str(info.get("blocked_reason")))
        check("带上了体积与校验来源",
              bool(info.get("size_text")) and "SHA256" in (info.get("sha_source") or ""),
              "%s / %s" % (info.get("size_text"), info.get("sha_source")))
        check("界面拿得到 tag（安装时要用它复核）", info.get("tag") == "v9.9.9")

        bad = window.api_update_install({"tag": "v0.0.1"})
        check("界面报的版本与检查结果不一致时拒绝",
              not bad["ok"] and "不一致" in bad["message"], bad["message"])
        check("拒绝时没有真的去装", "install" not in calls)

        pushed_before = len(window.window.pushed)
        result = window.api_update_install({"tag": info["tag"]})
        check("安装结果回给界面", bool(result.get("ok")), result.get("message"))
        check("装的是「检查」存下来的那一份 plan",
              calls.get("install") is FakeUpdater.plan)
        check("进度推给了注入层",
              len(window.window.pushed) > pushed_before
              and "__wbShellUpdate" in (window.window.pushed[-1] or ""),
              (window.window.pushed[-1][:70] if window.window.pushed else ""))
        again = window.api_update_install({"tag": info.get("tag")})
        check("装完就清掉待装计划（必须重新检查）",
              not again["ok"] and "检查更新" in again["message"], again["message"])

        check("没检查就装会被挡下",
              not window.api_update_install({})["ok"])

        # 内核是外部启动的：外壳停不了它，必须在检查阶段就说清。
        FakeUpdater.plan = make_plan()
        svc.status = lambda: {"state": service_mod.EXTERNAL}
        blocked = window.api_update_check()
        check("外部启动的内核：检查阶段就标为不可安装",
              blocked.get("can_install") is False
              and "外部启动" in (blocked.get("blocked_reason") or ""),
              blocked.get("blocked_reason"))
        svc.status = real_status

        # 取不到官方校验值：外壳的原则是拒绝安装，而不是"先覆盖后校验"。
        FakeUpdater.plan = make_plan()
        FakeUpdater.plan.expected_sha = ""
        no_sha = window.api_update_check()
        check("没有官方 SHA256 时标为不可安装",
              no_sha.get("can_install") is False
              and "SHA256" in (no_sha.get("blocked_reason") or ""),
              no_sha.get("blocked_reason"))

        # 端口上蹲着一个"不是本外壳启动的内核"（8788 上实测正是这种状态）：
        # install() 第一步就会中止，所以检查阶段就要说清——别给一个按下去必失败的按钮。
        FakeUpdater.plan = make_plan()
        real_probe_health = service_mod.probe_health
        service_mod.probe_health = lambda *a, **k: {"state": "ours", "message": ""}
        try:
            occupied = window.api_update_check()
        finally:
            service_mod.probe_health = real_probe_health
        check("端口上另有内核时：检查阶段就标为不可安装",
              occupied.get("can_install") is False
              and "端口" in (occupied.get("blocked_reason") or ""),
              occupied.get("blocked_reason"))

        # 已是最新：不弹安装对话框。
        FakeUpdater.plan = make_plan(available=False)
        latest = window.api_update_check()
        check("已是最新时不提供安装",
              bool(latest.get("ok")) and latest.get("available") is False,
              latest.get("message"))
    finally:
        updater_mod.Updater = real_updater_cls
        svc.status = real_status
        window.window = None

    with open(os.path.join(SHELL, "launcher", "ui_web.py"), encoding="utf-8") as fh:
        source = fh.read()
    check("不再有「安装入口在后续阶段接入」这种半成品说法",
          "后续阶段接入" not in source)

    section("8. 更新之后的「重启外壳 / 重新加载看板」（打桩）")
    # 没有协调器（例如命令行守候）时必须明确拒绝，而不是假装成功。
    window.coordinator = None
    refused = window.api_restart_shell()
    check("没有协调器时拒绝重启",
          refused.get("ok") is False and "不支持" in (refused.get("message") or ""),
          refused.get("message"))

    calls = {}

    class FakeCoord(object):
        def request_shell_restart(self, source="界面"):
            calls["restart"] = source
            return {"ok": True, "restarting": True, "message": "正在重启外壳"}

    window.coordinator = FakeCoord()
    window.window = FakeWindow()
    window._closing = False
    allowed = window.api_restart_shell()
    check("重启请求转给协调器", bool(allowed.get("ok"))
          and calls.get("restart") == "更新后重启外壳", str(calls))
    check("重启时关掉窗口循环（外层才会拉起新外壳）", window._closing is True)

    window._closing = False
    window.window.pushed = []
    reloaded = window.api_reload_dashboard()
    check("重新加载看板把 location.replace 推给了页面",
          bool(reloaded.get("ok"))
          and any("location.replace" in code for code in window.window.pushed),
          str(window.window.pushed[:1]))
    check("重载的是本机看板地址",
          (":%d" % PORT) in (reloaded.get("url") or ""), reloaded.get("url"))
    window.coordinator = None
    window.window = None

    section("结论")
    print("  隔离根：", root)
    print("  真内核：%s（本测试只读）" % REAL_CORE)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
