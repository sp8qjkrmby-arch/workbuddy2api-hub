# -*- coding: utf-8 -*-
"""panel_secret_test.py — "记住面板密码"这条链路：DPAPI 密封 + 三个接口的契约。

这是外壳里唯一碰"秘密"的功能，所以规矩要在测试里钉住：

1. **只有当前 Windows 用户能解开**（DPAPI 用户作用域）；密文里**不含明文**；
2. **开关关着时，取口令的接口一律拒绝**——`panel_secret` 等价于"取口令"，
   不能让看板上随便一段脚本白拿；
3. **关掉开关要连密文一起删掉**；`forget` 只删密文、不动开关；
4. 每次页面加载都会回调的"记住"路径**不能反复写盘**（密码没变就不重写）。

全程在临时目录里跑：临时"外壳目录"+ 临时 data（`paths.data_dir` 被打桩），
所以**不会碰真外壳的 `data\\shell.json`**，也不会碰真内核。

用法：runtime\\python.exe tests\\panel_secret_test.py
"""

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
import secret_store              # noqa: E402
import service as service_mod    # noqa: E402
import ui_web                    # noqa: E402

SHELL = _SHELL_DIR
REAL_CORE = _KERNEL_DIR
PORT = 18878                     # 没人监听：面板状态探针应当安静地返回空
PASSWORD = "测试口令-9f3a"
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
    root = os.path.join(tempfile.gettempdir(), "wb-panel-secret")
    shutil.rmtree(root, ignore_errors=True)
    data = os.path.join(root, "data")
    shell = os.path.join(root, "shell")          # 假的"外壳目录"：写盘全落这里
    accounts = os.path.join(root, "accounts")
    usage = os.path.join(root, "usage")
    for folder in (data, shell, accounts, usage):
        os.makedirs(folder, exist_ok=True)
    logging_setup.setup(data)
    real_data_dir = paths.data_dir
    paths.data_dir = lambda: data                # secret_store 也落在临时目录
    try:
        return run(shell, data, accounts, usage)
    finally:
        paths.data_dir = real_data_dir


def run(shell, data, accounts, usage):
    loaded = config_mod.load(SHELL)
    eff = config_mod.merged_effective(loaded["config"], SHELL, {
        "port": PORT, "core_dir": REAL_CORE,
        "accounts_dir": accounts, "usage_dir": usage,
    })
    svc = service_mod.CoreService(eff, SHELL)
    window = ui_web.WebWindow(shell, loaded["config"], eff, svc,
                              logger=lambda t: logging_setup.log("[ui] " + t))
    # 开关的默认值必须是关的（不勾就绝不记密码）
    eff["config"].pop("panel_password_remember", None)
    window.config["panel_password_remember"] = False

    section("1. DPAPI 密封：能存能取，密文里没有明文")
    check("平台支持 DPAPI（Windows）", secret_store.available())
    check("一开始没有密文", not secret_store.has())
    ok, message = secret_store.save(PASSWORD)
    check("保存成功", ok, message)
    check("文件出现了", secret_store.has(), secret_store.secret_path())
    got, why = secret_store.load()
    check("取回的就是原值", got == PASSWORD, why or ("长度 %d" % len(got)))
    with open(secret_store.secret_path(), "rb") as handle:
        blob = handle.read()
    check("密文里搜不到明文", PASSWORD.encode("utf-8") not in blob,
          "%d 字节密文" % len(blob))
    check("记下了保存时间", bool(secret_store.saved_at()), secret_store.saved_at())
    check("空内容拒绝保存", not secret_store.save("")[0])
    secret_store.clear()
    check("清空后文件消失", not secret_store.has())

    section("2. 开关关着时，拒绝交出/写入任何口令")
    off = window.api_panel_secret()
    check("panel_secret 被拒绝",
          off.get("ok") is False and "没有开启" in (off.get("message") or ""),
          off.get("message"))
    refused = window.api_panel_remember_password({"password": PASSWORD})
    check("没开开关时拒绝记住密码",
          refused.get("ok") is False and "没有开启" in (refused.get("message") or ""),
          refused.get("message"))
    check("而且确实没写盘", not secret_store.has())

    section("3. 打开开关（写盘落到临时外壳目录）")
    # 真实的 config_mod.save 会写到 <shell>\data\shell.json；这里 shell 是临时目录，
    # 所以不会碰你正在用的那份。顺便确认它真的被调用了。
    calls = []
    real_save = config_mod.save

    def spy_save(config, shell_path=None):
        calls.append(shell_path)
        return real_save(config, shell_path)

    ui_web.config_mod.save = spy_save
    try:
        on = window.api_panel_remember_password({"remember": True})
    finally:
        ui_web.config_mod.save = real_save
    check("开启成功", bool(on.get("ok")), str(on.get("message")))
    check("开关落到配置里", window.config.get("panel_password_remember") is True)
    check("写盘用的是外壳目录（不是真外壳）",
          calls and os.path.normcase(calls[0]) == os.path.normcase(shell),
          str(calls))
    written = os.path.join(shell, "data", "shell.json")
    check("临时 shell.json 真的写出来了", os.path.isfile(written), written)
    if os.path.isfile(written):
        saved = config_mod.load(shell)["config"]
        check("里面记着开关是开的",
              saved.get("panel_password_remember") is True,
              str(saved.get("panel_password_remember")))

    section("4. 记住密码：写盘一次，重复回调不重写")
    first = window.api_panel_remember_password({"password": PASSWORD})
    check("记住成功", bool(first.get("ok")), str(first.get("message")))
    check("密文已存在", secret_store.has())
    check("取回一致", secret_store.load()[0] == PASSWORD)
    stamp = os.path.getmtime(secret_store.secret_path())
    time.sleep(1.1)
    again = window.api_panel_remember_password({"password": PASSWORD})
    check("同一个密码：判为「不用重记」",
          bool(again.get("ok")) and "没变" in (again.get("message") or ""),
          again.get("message"))
    check("也没有重新写盘（mtime 未变）",
          os.path.getmtime(secret_store.secret_path()) == stamp)

    # 在看板里改过密码之后：外壳记的旧口令必须能被新口令顶掉
    # （否则每次自动登录都失败，用户只能永远手输）
    changed = window.api_panel_remember_password({"password": PASSWORD + "-换过"})
    check("换了密码会覆盖旧密文",
          bool(changed.get("ok"))
          and secret_store.load()[0] == PASSWORD + "-换过",
          str(changed.get("message")))
    window.api_panel_remember_password({"password": PASSWORD})
    check("再换回来也可以", secret_store.load()[0] == PASSWORD)

    section("5. 自动登录用的取口令接口（开关开着才给）")
    secret = window.api_panel_secret()
    check("拿到口令", bool(secret.get("ok")) and secret.get("password") == PASSWORD,
          str(secret.get("message") or ""))

    section("6. 状态接口把三件事说清")
    status = window.api_panel_status()
    print("    %s" % {k: status.get(k) for k in
                      ("remember", "have_secret", "saved_at", "required", "is_default")})
    check("remember=True", status.get("remember") is True)
    check("have_secret=True", status.get("have_secret") is True)
    check("带保存时间", bool(status.get("saved_at")))
    check("端口没人监听时 required 为 None（不误报）",
          status.get("required") is None, str(status.get("required")))
    check("密文路径可展示", str(status.get("sealed_path") or "").endswith(".sealed"),
          str(status.get("sealed_path")))

    real_probe = service_mod.probe_panel_status
    service_mod.probe_panel_status = lambda *a, **k: {
        "panel_password_required": True, "panel_password_is_default": False}
    try:
        live = window.api_panel_status()
    finally:
        service_mod.probe_panel_status = real_probe
    check("内核说需要密码时如实转述",
          live.get("required") is True and live.get("is_default") is False,
          str(live.get("required")))

    section("7. forget 只删密文；关开关连密文一起删")
    forgot = window.api_panel_remember_password({"forget": True})
    check("forget 成功", bool(forgot.get("ok")), str(forgot.get("message")))
    check("密文没了", not secret_store.has())
    check("开关还开着（下次手输还会被记）",
          window.config.get("panel_password_remember") is True)
    check("此时取口令要有明确说明，而不是空口令",
          window.api_panel_secret().get("ok") is False)

    window.api_panel_remember_password({"password": PASSWORD})
    check("再记一次又有了", secret_store.has())
    off = window.api_panel_remember_password({"remember": False})
    check("关开关成功", bool(off.get("ok")), str(off.get("message")))
    check("关开关时密文也删了", not secret_store.has())
    check("开关状态是关", window.config.get("panel_password_remember") is False)
    check("关着时取口令被拒", window.api_panel_secret().get("ok") is False)

    section("结论")
    print("  隔离根：%s" % os.path.dirname(data))
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
