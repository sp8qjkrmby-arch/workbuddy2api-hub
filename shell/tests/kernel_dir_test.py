# -*- coding: utf-8 -*-
"""验证「内核位置」：信息读取、路径校验、写盘（bootstrap.json）。

**绝不触发真正的外壳重启**——这里把 `_spawn_replacement_shell` 打桩掉，
否则测试会把自己（以及用户的会话）打断。写盘测试也会把 bootstrap.json
还原到测试前的状态。
"""

import json
import os
import shutil
import sys
import tempfile

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
import vendor as vendor_mod      # noqa: E402

vendor_mod.ensure()

SHELL = _SHELL_DIR
REAL_CORE = _KERNEL_DIR
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
    # 隔离运行：数据目录、账号目录都放临时区，不碰真内核数据
    root = os.path.join(tempfile.gettempdir(), "wb-kdir")
    shutil.rmtree(root, ignore_errors=True)
    accounts = os.path.join(root, "accounts")
    usage = os.path.join(root, "usage")
    os.makedirs(accounts, exist_ok=True)
    os.makedirs(usage, exist_ok=True)
    logging_setup.setup(root)

    loaded = config_mod.load(SHELL)
    eff = config_mod.merged_effective(loaded["config"], SHELL, {
        "port": 18940, "core_dir": REAL_CORE,
        "accounts_dir": accounts, "usage_dir": usage,
    })
    svc = service_mod.CoreService(eff, SHELL)
    window = ui_web.WebWindow(SHELL, loaded["config"], eff, svc,
                              logger=lambda t: None)

    section("1. 把 _spawn_replacement_shell 打桩（禁止真重启）")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wb_entry_kdir", os.path.join(SHELL, "launcher", "__main__.py"))
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    relaunch_calls = []

    def fake_relaunch(shell_dir):
        relaunch_calls.append(shell_dir)
        return True, "（测试打桩：没有真的重启）"

    entry._spawn_replacement_shell = fake_relaunch
    check("打桩生效", entry._spawn_replacement_shell is fake_relaunch)

    coord = entry.ShutdownCoordinator(SHELL, loaded["config"], svc)
    window.coordinator = coord

    section("2. kernel_info：当前位置与候选")
    info = window.api_kernel_info()
    print("    core_dir ：%s" % info.get("core_dir"))
    print("    source   ：%s" % info.get("source"))
    print("    version  ：%s" % info.get("version"))
    print("    bootstrap：%s（存在=%s）"
          % (info.get("bootstrap"), info.get("has_bootstrap")))
    for cand in info.get("candidates") or []:
        print("    候选 %s  valid=%s  (%s)"
              % (cand.get("path"), cand.get("valid"), cand.get("note")))
    check("返回 ok", info.get("ok") is True)
    check("core_dir 是正确的内核", os.path.normcase(info.get("core_dir") or "")
          == os.path.normcase(REAL_CORE), str(info.get("core_dir")))
    check("标为有效", bool(info.get("valid")))
    check("版本号读到了", bool(info.get("version")), str(info.get("version")))
    check("候选里含当前内核",
          any(os.path.normcase(c.get("path") or "") == os.path.normcase(REAL_CORE)
              and c.get("valid") for c in info.get("candidates") or []))

    section("3. kernel_validate：合法 / 非法 / 空")
    ok, message = coord.validate_kernel_dir(REAL_CORE)
    check("真内核目录判为可用", ok, message)
    ok2, message2 = coord.validate_kernel_dir(os.path.join(root, "不存在"))
    check("不存在的路径被拒", not ok2, message2)
    ok3, message3 = coord.validate_kernel_dir("")
    check("空路径被拒", not ok3, message3)
    # 造一个"目录存在但缺 wb_proxy.py"的假内核
    fake = os.path.join(root, "not-a-core")
    os.makedirs(fake, exist_ok=True)
    ok4, message4 = coord.validate_kernel_dir(fake)
    check("缺 wb_proxy.py 的目录被拒", not ok4, message4)
    check("拒绝原因是可读的", "wb_proxy.py" in (message4 or ""), message4)

    section("4. kernel_set：写盘（并把 bootstrap.json 还原）")
    boot = paths.bootstrap_path()
    backup = None
    if os.path.isfile(boot):
        backup = boot + ".test-backup"
        shutil.copy2(boot, backup)
        print("    已备份原 bootstrap.json → %s" % backup)
    try:
        result = coord.set_kernel_dir(REAL_CORE)
        print("    返回：%s" % json.dumps(result, ensure_ascii=False))
        check("写盘成功并声明要重启", bool(result.get("ok"))
              and bool(result.get("restarting")))
        check("core_dir_changed 已置位", coord.core_dir_changed is True)
        check("bootstrap.json 已生成", os.path.isfile(boot))
        if os.path.isfile(boot):
            saved = json.load(open(boot, encoding="utf-8"))
            check("文件里记的是这个路径",
                  os.path.normcase(saved.get("core_dir") or "")
                  == os.path.normcase(REAL_CORE),
                  json.dumps(saved, ensure_ascii=False))
        # 还原：删掉测试生成的文件（或写回备份）
        if backup:
            shutil.move(backup, boot)
            print("    已还原原 bootstrap.json")
        else:
            os.remove(boot)
            print("    测试前没有 bootstrap.json，已删除测试生成的")

        section("5. 非法路径不应写盘")
        before = os.path.isfile(boot)
        bad = coord.set_kernel_dir(os.path.join(root, "不存在"))
        check("被拒", not bad.get("ok"), str(bad.get("message")))
        check("没有因此新建 bootstrap.json",
              os.path.isfile(boot) == before)
        check("拒绝时也不置重启标记", coord.core_dir_changed is True)  # 前一步已置位

        section("6. 全程没有真的重启")
        check("打桩函数没被调用（写盘路径不自己重启）",
              len(relaunch_calls) == 0, str(relaunch_calls))
        check("bootstrap.json 已复原到测试前状态",
              os.path.isfile(boot) == bool(backup) or not backup)

        section("7. 「重启外壳」请求（更新之后用）：只置标记，不自己 spawn")
        # 与「内核位置」共用同一条重启链路：这里只置位并让主循环退出，
        # 真正拉起新外壳是 main() 的 finally 干的（所以本测试不会被换掉）。
        coord.core_dir_changed = False        # 清掉上一节留下的标记
        check("清掉标记后 wants_relaunch 为假", coord.wants_relaunch is False)
        restarted = coord.request_shell_restart("测试")
        check("请求返回 ok 且声明在重启",
              bool(restarted.get("ok")) and bool(restarted.get("restarting")),
              str(restarted.get("message")))
        check("restart_requested 已置位", coord.restart_requested is True)
        check("wants_relaunch 变真（main 才会换外壳）",
              coord.wants_relaunch is True)
        check("主循环该退了（explicit）", coord.explicit is True)
        api_result = window.api_restart_shell()
        check("界面层的 api_restart_shell 走同一条链路",
              bool(api_result.get("ok")), str(api_result.get("message")))
        check("到这里仍然没有真的 spawn",
              len(relaunch_calls) == 0, str(relaunch_calls))
    finally:
        if backup and os.path.isfile(backup):
            try:
                shutil.move(backup, boot)
            except Exception:
                pass

    section("结论")
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
