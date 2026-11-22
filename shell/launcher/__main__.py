# -*- coding: utf-8 -*-
"""__main__.py — 外壳入口。

职责很小，只是把「读设置 → 探测环境 → 选界面 → 兜底退出」串起来：

    python launcher              # 打开窗口（P1 是 tkinter 原生窗口）
    python launcher --no-gui     # 不建窗口：启动内核、探通 /health、等待信号
    python launcher --selftest   # 自检：启动→探通→停止→复验端口释放，然后退出
    python launcher --core-dir D --port N   # 临时覆盖设置（不写盘）

设计原则：**任何界面问题都不该让内核停摆**。窗口建不起来时退回 CLI，
CLI 也起不来时才报错退出。
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

# 允许从任意工作目录启动：sys.path 里补上外壳根目录，再补 launcher 自身。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SHELL = os.path.dirname(_HERE)
if _SHELL not in sys.path:
    sys.path.insert(0, _SHELL)

import config as config_mod      # noqa: E402
import logging_setup             # noqa: E402
import paths                     # noqa: E402
import service as service_mod    # noqa: E402

VERSION = "0.1.0"

# `check_update_on_launch` 的静默检查延迟：等启动那阵忙完（拉内核、建窗）再查。
LAUNCH_UPDATE_DELAY = 8.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="wb-shell",
        description="WorkBuddy2API-Hub 网关外壳（不修改内核，只管理它的生命周期）")
    parser.add_argument("--core-dir", default=None,
                        help="内核目录（含 wb_proxy.py）；默认自动发现在外壳旁边")
    parser.add_argument("--port", type=int, default=None, help="覆盖监听端口")
    parser.add_argument("--host", default=None, help="覆盖监听地址")
    parser.add_argument("--lan", dest="lan", action="store_true", default=None,
                        help="监听所有网卡（等同 --host 0.0.0.0）")
    parser.add_argument("--accounts-dir", default=None, help="覆盖账号目录")
    parser.add_argument("--usage-dir", default=None, help="覆盖用量目录")
    parser.add_argument("--ui", choices=config_mod.UI_CHOICES, default=None,
                        help="界面后端（auto / web / native）")
    parser.add_argument("--no-gui", action="store_true",
                        help="不建窗口，只把内核跑起来并保持前台")
    parser.add_argument("--no-start", action="store_true",
                        help="只探测环境不启动内核（配合 --no-gui 做诊断）")
    parser.add_argument("--tray-only", action="store_true",
                        help="不建窗口，只驻留系统托盘（开机自启用）")
    parser.add_argument("--no-tray", action="store_true",
                        help="本次不启用系统托盘")
    parser.add_argument("--selftest", action="store_true",
                        help="启动→探通→停止→复验端口释放，打印结论后退出")
    parser.add_argument("--print-config", action="store_true",
                        help="打印生效配置（密钥打码）后退出")
    parser.add_argument("--version", action="version",
                        version="wb-shell %s" % VERSION)
    return parser.parse_args(argv)


def cli_overrides(args):
    overrides = {}
    if args.core_dir:
        overrides["core_dir"] = args.core_dir
    if args.port is not None:
        overrides["port"] = args.port
    if args.host:
        overrides["host"] = args.host
    if args.lan:
        overrides["lan"] = True
    if args.accounts_dir:
        overrides["accounts_dir"] = args.accounts_dir
    if args.usage_dir:
        overrides["usage_dir"] = args.usage_dir
    if args.ui:
        overrides["ui"] = args.ui
    return overrides


def force_utf8():
    """控制台中文输出：能改就改，改不了也不影响功能。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class ShutdownCoordinator(object):
    """退出语义的唯一裁决处。

    三种入口要分清：

    * **右上角的 ×**（web / 原生窗口都一样）：只关窗口，**不停内核、不结束外壳**。
      托盘在 → 隐藏到托盘；托盘不在 → 不能只隐藏（没有图标可以唤回窗口），
      退回"确认后退出"。
    * **退出按钮 / 托盘「退出」**：停内核 + 停托盘 + 结束外壳。
    * **窗口已销毁后**（`webview.start()` 返回，或原生窗口 mainloop 退出）：
      这里才真正收尾；没经过确认就补一个确认框。

    `request_exit()` 是"确定要退出"的唯一开关——托盘与控制层的退出都走它，
    这样 `finalize()` 就知道该不该再问一遍。
    """

    def __init__(self, shell, config, service, tray=None):
        self.shell = shell
        self.config = config
        self.service = service
        self.tray = tray
        self.explicit = False
        self._force_quit = None
        self._tray_hint_done = False
        # 「内核位置」改过盘、准备重启外壳时置位
        self.core_dir_changed = False
        # 用户明确要求重启外壳（更新之后的「重启应用」）
        self.restart_requested = False
        self._lock = threading.Lock()

    # ---- 状态 -------------------------------------------------------------

    @property
    def wants_relaunch(self):
        """要不要换一个新外壳进程：内核位置变了、或用户要求重启，都算。"""
        return bool(self.core_dir_changed or self.restart_requested)

    @property
    def tray_available(self):
        return self.tray is not None and getattr(self.tray, "available", False)

    def set_force_quit(self, callback):
        """由界面层注册：把窗口真正销毁（销毁后事件循环才会返回）。"""
        self._force_quit = callback

    def notify_tray_hint(self):
        """首次缩到托盘时弹一次气泡，否则用户会以为程序退出了。"""
        if self._tray_hint_done or not self.tray_available:
            return
        self._tray_hint_done = True
        try:
            self.tray.notify("窗口已隐藏，代理仍在后台运行",
                             "右键托盘图标可重新打开窗口或退出")
        except Exception:
            pass

    # ---- 动作 -------------------------------------------------------------

    def request_exit(self, source="unknown"):
        """明确的退出请求：现在就可以停内核了。"""
        with self._lock:
            if self.explicit:
                return
            self.explicit = True
        logging_setup.log("收到退出请求（来源：%s）" % source)
        try:
            self.service.shutdown()
        except Exception as exc:
            logging_setup.log("退出时停止内核异常：%r" % (exc,), "ERROR")
        if self._force_quit is not None:
            try:
                self._force_quit()
            except Exception as exc:
                logging_setup.log("销毁窗口失败：%r" % (exc,), "WARN")

    def confirm_if_needed(self, reason=""):
        """窗口已关、但用户没明确要求退出时，问一句。返回是否继续退出。"""
        if self.explicit:
            return True
        if self.config.get("close_to_tray", True) and self.tray_available:
            # 正常情况下 × 已经在 closing 事件里被拦下，走不到这里；
            # 真走到了说明拦截失效，按"只关窗口"的承诺处理不了，只能问。
            logging_setup.log("× 未被拦截（%s），询问用户是否退出" % reason, "WARN")
        question = ("窗口已关闭。要同时停止代理服务并退出外壳吗？\n\n"
                    "选择「否」将只关窗口、保留代理服务——"
                    "但当前没有可用的系统托盘图标，"
                    "窗口需要重新启动外壳才能打开。")
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            answer = messagebox.askyesno("WorkBuddy 网关", question, parent=root)
            root.destroy()
            if not answer:
                logging_setup.log("用户选择只关窗口：内核继续运行，外壳退出")
                return False
        except Exception as exc:
            logging_setup.log("确认框不可用（%r），按退出处理" % (exc,), "WARN")
        return True

    def finalize(self, reason=""):
        """事件循环已返回时的收尾。返回进程退出码。"""
        if self.explicit:
            return 0
        if not self.confirm_if_needed(reason):
            # 用户要的是"只关窗口"：内核留着。壳进程仍会退出
            # （窗口已销毁，没有界面可用了），这是与"× 只关窗口"最接近的可行结果。
            return 0
        self.service.shutdown()
        return 0

    # ---- 内核位置 ---------------------------------------------------------

    def kernel_info(self):
        """当前内核位置 + 所有候选，给"内核位置"对话框用。"""
        effective = self.service.effective or {}
        config = effective.get("config") or {}
        core_dir = effective.get("core_dir") or self.service.core_dir
        source = effective.get("core_source") or ""
        return {
            "ok": True,
            "shell_dir": self.shell,
            "core_dir": core_dir,
            "source": source,
            "version": self.service.status().get("core_version") or "",
            "valid": paths.is_core_dir(core_dir),
            "problem": paths.describe_core_problem(core_dir),
            "bootstrap": paths.bootstrap_path(),
            "has_bootstrap": os.path.isfile(paths.bootstrap_path()),
            "override": bool(config.get("_core_dir_override")),
            "runtime_dir": config.get("runtime_dir") or "",
            "candidates": paths.candidate_core_dirs(self.shell),
        }

    def validate_kernel_dir(self, candidate):
        """校验一个候选目录能不能当内核用。返回 (ok, message)。"""
        text = str(candidate or "").strip().strip('"')
        if not text:
            return False, "路径为空"
        if not os.path.isdir(text):
            return False, "目录不存在：%s" % text
        if paths.is_core_dir(text):
            version = service_mod.read_core_version(text)
            return True, "可用%s" % ("（内核版本 %s）" % version if version else "")
        return False, paths.describe_core_problem(text)

    def set_kernel_dir(self, candidate):
        """把内核位置钉进 bootstrap.json，并重启外壳让它生效。

        为什么必须重启外壳：内核目录是启动时解析的（服务、Python 探测、路径预检
        都在那一步定死）。改完让它立即生效只有两条路——重启外壳，或者在外面
        再启一个；后者会和当前这个抢同一个端口。所以选择重启，并且**先写盘、
        再拉起新进程、最后自己退出**：失败也只留下一个文件，界面照旧可用。
        """
        ok, message = self.validate_kernel_dir(candidate)
        if not ok:
            return {"ok": False, "message": message}
        target = os.path.abspath(str(candidate).strip().strip('"'))
        saved, detail = paths.write_bootstrap(target, self.shell)
        if not saved:
            return {"ok": False, "message": detail}
        logging_setup.log("内核位置已改为 %s（写入 %s），准备重启外壳"
                          % (target, detail))
        self.core_dir_changed = True
        return {"ok": True, "restarting": True, "path": target,
                "message": "已记住内核位置：%s\n外壳即将重启以生效。" % target}

    def relaunch_shell(self):
        """拉起一个新的外壳并把当前这个关掉（内核位置变更后调用）。"""
        return _spawn_replacement_shell(self.shell)

    def request_shell_restart(self, source="界面"):
        """重启外壳：停内核 + 停托盘 + 换一个新外壳进程。

        与「内核位置」改动走**同一条链路**（`_spawn_replacement_shell`，
        已经被 kernel_dir_test 钉住），区别只是不写 bootstrap.json。
        用途：更新内核之后，用户想要一个"从代码到界面都重新开始"的外壳
        （内核会短暂中断——这是重启的代价，所以做成让他选，而不是自动做）。
        """
        logging_setup.log("收到重启外壳请求（来源：%s）" % source)
        self.restart_requested = True
        # request_exit 会停内核、销毁窗口；主循环看到 explicit 就退出，
        # main() 的 finally 看到 wants_relaunch 就拉起新外壳。
        self.request_exit(source)
        return {"ok": True, "restarting": True,
                "message": "正在重启外壳（内核会短暂中断，几秒后自动回来）"}


# 重启外壳用到的三个可注入点（测试要能在几秒内把这条链路跑完，
# 并且不能真的把测试进程 os._exit 掉、也不能弹真提示框）。
RELAUNCH_ATTEMPTS = 3
RELAUNCH_TIMEOUT = 20.0        # 每次尝试等"新外壳启动日志"多久
RELAUNCH_PAUSE = 0.6           # 拉起前先让旧窗口/句柄再放一会儿
_EXIT = os._exit               # 测试替换它，避免真退出


def _log_new_text(log_path, offset):
    """读日志里 offset 之后的新内容（顺便处理轮转）。返回 (文本, 新 offset)。"""
    try:
        size = os.path.getsize(log_path)
        if size < offset:      # 轮转过：从头读
            offset = 0
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read(), size
    except Exception:
        return "", offset


def _wait_for_shell_start(log_path, offset, timeout=None):
    """等新外壳的启动日志出现。**这是"重启到底成没成"的唯一凭据。**

    注意 `timeout` 默认不要写成 `timeout=RELAUNCH_TIMEOUT`：那会在**函数定义时**
    就把值绑死，之后改模块级常量（测试要把它压小）就不生效了——栽过一次。
    """
    if timeout is None:
        timeout = RELAUNCH_TIMEOUT
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        text, offset = _log_new_text(log_path, offset)
        if "外壳启动 v" in text:
            return True
        time.sleep(0.4)
    return False


def _warn_restart_failed(detail):
    """重启失败时**必须让用户看见**——否则他面对的是"内核没了、外壳也没了"。"""
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(
            "WorkBuddy 网关",
            "重启外壳失败：%s\n\n请手动双击桌面「WorkBuddy 网关」。\n"
            "（内核已经停止，所以现在没有服务在跑。）" % detail,
            parent=root)
        root.destroy()
    except Exception as exc:
        logging_setup.log("失败提示框不可用：%r" % (exc,), "WARN")


def _spawn_replacement_shell(shell_dir):
    """拉起一个新外壳、**确认它真的起来了**，然后当前进程退出。

    为什么不再经 PowerShell 包一层（早期写法 `-Command "Start-Sleep …; & pythonw …"`）：
    实测（2026-10-14）父进程 `os._exit(0)` 之后，经 powershell 拉起的孙子进程
    **活不下来**——日志写着"已拉起新外壳"，实际什么也没起来。生产日志里那 107 秒
    空档（21:41:05 点「重启外壳」→ 21:42:52 才起来，还是人手动开的）就是这么来的：
    "重启"变成了"把内核停掉、外壳也不见"。
    直接 CreateProcess 一个 pythonw（GUI 子系统、无控制台）就没事（同一次实测里
    B 组通过）。延迟不再靠 wrapper 的 Start-Sleep，而是这里睡一小会儿再拉。
    """
    pythonw = os.path.join(shell_dir, "runtime", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    launcher = os.path.join(shell_dir, "launcher")
    log_path = logging_setup.log_path()
    offset = 0
    try:
        offset = os.path.getsize(log_path)
    except Exception:
        offset = 0
    detail = ""
    for attempt in range(1, max(1, int(RELAUNCH_ATTEMPTS)) + 1):
        try:
            time.sleep(max(0.0, float(RELAUNCH_PAUSE)))
        except Exception:
            pass
        try:
            subprocess.Popen(
                [pythonw, launcher],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 | 0x00000008,   # NO_WINDOW | DETACHED
                close_fds=True)
        except Exception as exc:
            detail = "%s: %s" % (type(exc).__name__, exc)
            logging_setup.log("拉起新外壳失败（第 %d 次）：%s" % (attempt, detail),
                              "ERROR")
            continue
        if _wait_for_shell_start(log_path, offset):
            logging_setup.log("新外壳已起来（第 %d 次尝试，日志里已见启动行）" % attempt)
            break
        detail = "%.0f 秒内没等到新外壳的启动日志" % RELAUNCH_TIMEOUT
        logging_setup.log("第 %d 次拉起后%s，重试" % (attempt, detail), "WARN")
        try:
            offset = os.path.getsize(log_path)
        except Exception:
            offset = 0
    else:
        logging_setup.log("拉起新外壳失败：%s" % (detail or "未知原因"), "ERROR")
        _warn_restart_failed(detail or "未知原因")
        return False, "重启失败：%s" % (detail or "未知原因")

    # 新外壳已经确认起来了，现在才可以退出：Job Object 会回收旧内核，
    # 新外壳按新位置/新版本自己拉起内核。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    _EXIT(0)


def probe_vendor():
    """把随壳依赖接进 sys.path 并如实报告可用性。失败也不抛异常。"""
    try:
        import vendor as vendor_mod
        info = vendor_mod.ensure()
        lines = vendor_mod.describe()
    except Exception as exc:
        logging_setup.log("vendor 引导异常：%r" % (exc,), "WARN")
        info = {"webview": False, "tray": False, "errors": ["%s" % exc]}
        lines = ["vendor 引导失败：%s" % exc]
    for line in lines:
        logging_setup.log(line)
    return info, lines


def print_banner(effective, service, vendor_info=None,
                 autostart_note=""):
    info = vendor_info if vendor_info is not None else (effective.get("vendor") or {})
    config = effective["config"]
    print("=" * 68)
    print("  WorkBuddy2API-Hub 网关外壳 %s" % VERSION)
    print("=" * 68)
    print("  内核目录   : %s（来源：%s）" % (effective["core_dir"],
                                            effective["core_source"]))
    print("  账号目录   : %s" % effective["accounts_dir"])
    print("  用量目录   : %s" % effective["usage_dir"])
    print("  监听       : %s:%s%s" % (config["host"], config["port"],
                                      "（LAN 模式）" if config.get("lan") else ""))
    print("  API Key    : %s" % config_mod.secret_fingerprint(config.get("api_key")))
    print("  面板密码   : %s" % ("***（已设置）" if config.get("panel_password")
                                 else "(未设置，内核默认 admin)"))
    print("  Python     : %s" % paths.describe_python(service.python))
    print("  内核版本   : %s" % (service.core_version or "未识别"))
    print("  看板/API   : %s  |  %s" % (service.status()["dashboard"],
                                        service.status()["api_base"]))
    print("  外壳日志   : %s" % logging_setup.log_path())
    print("  界面后端   : %s / 托盘 %s"
          % ("内嵌看板可用" if info.get("webview") else "内嵌看板不可用",
             "可用" if info.get("tray") else "不可用"))
    print("  托盘       : %s" % ("启用" if config.get("tray_enabled") else "关闭"))
    print("  开机自启   : %s" % ("已开启" if config.get("autostart") else "未开启"))
    if autostart_note:
        print("  [自启提示] %s" % autostart_note)
    for warning in effective.get("warnings") or []:
        print("  [提示] %s" % warning)
    for warning in (service.python.get("warnings") or []):
        print("  [提示] %s" % warning)
    print("=" * 68)


def run_selftest(service):
    """自检：给「外壳真的能管住内核」一个可复现的证据。"""
    print()
    print("--- 自检 1/4：启动内核 ---")
    ok, message = service.start()
    print("  结果：%s  说明：%s" % ("通过" if ok else "失败", message))
    if not ok:
        _dump_log_tail(service)
        return 1
    status = service.status()
    print("  pid=%s  账号=%s  可用=%s  区域=%s  版本=%s"
          % (status["pid"], status["accounts"], status["accounts_ready"],
             status["realm"], status["core_version"]))
    print("  进程组兜底（Job Object）：%s" % ("已启用" if status["used_job"] else "未启用"))

    print()
    print("--- 自检 2/4：探通 /health ---")
    health = service_mod.probe_health(service.port, service.host)
    passed = health.get("state") == "ours"
    print("  结果：%s  %s" % ("通过" if passed else "失败",
                             health.get("message") or health.get("url") or ""))

    print()
    print("--- 自检 3/4：停止内核 ---")
    ok, message = service.stop()
    print("  结果：%s  说明：%s" % ("通过" if ok else "失败", message))
    if not ok:
        _dump_log_tail(service)

    print()
    print("--- 自检 4/4：复验端口已释放 ---")
    released, detail = service.wait_port_released(timeout=10.0)
    print("  结果：%s  %s" % ("通过" if released else "失败", detail))

    if not (passed and released):
        _dump_log_tail(service)
        return 1
    print()
    print("自检全部通过。")
    return 0


def _dump_log_tail(service, lines=40):
    print()
    print("--- 内核日志尾部（%d 行）---" % lines)
    for line in service.logs(lines):
        print("  " + line)
    print("--- 完整日志：%s ---" % (logging_setup.log_path() or "(未初始化)"))


def run_headless(service, start_core=True):
    """无窗口模式：前台守着内核，Ctrl+C 退出。"""
    print_banner(service.effective, service)
    if start_core:
        print("--- 启动内核 ---")
        ok, message = service.start()
        print("  结果：%s  说明：%s" % ("通过" if ok else "失败", message))
        if not ok:
            _dump_log_tail(service)
            return 1
    else:
        print("（--no-start：只探测环境，未启动内核）")

    stop_event = threading.Event()

    def _handler(_signum, _frame):
        print()
        print("收到中断信号，正在停止内核…")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass

    print()
    print("外壳在前台运行，Ctrl+C 退出。内核日志实时写入：%s"
          % (logging_setup.log_path() or "?"))
    seen = service.log_seq()
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
            seq = service.log_seq()
            if seq != seen:
                new_lines = service.logs(0)[-(seq - seen):]
                seen = seq
                for line in new_lines:
                    print("  " + line)
            status = service.status()
            if status["state"] in (service_mod.STOPPED, service_mod.FAILED):
                print("内核已退出（%s），外壳随之退出。" % status["state_label"])
                break
    except KeyboardInterrupt:
        pass
    finally:
        service.shutdown()
    return 0


def _run_native(shell, loaded, effective, service, coordinator, tray=None,
                reason="", force_show=False, open_settings=False):
    """原生窗口（tkinter）。P1 就有，永远是兜底界面。"""
    import ui_native
    if open_settings:
        # 原生窗口里**没有设置面板**（内核位置 / 面板密码这两节活在注入层里，
        # 那是内嵌看板才有的东西）。老实说清楚 + 指明替代做法，别点了没反应。
        logging_setup.log("托盘请求打开设置，但原生窗口没有设置面板", "WARN")
        if tray is not None:
            tray.notify("原生窗口没有设置面板；请直接编辑 data\\shell.json，"
                        "或把界面切成内嵌看板（ui=web）",
                        "WorkBuddy 网关")
    if reason:
        print("已改用原生窗口：%s" % reason)
        logging_setup.log("降级到原生窗口：%s" % reason, "WARN")
    hint = ""
    if coordinator.tray_available:
        hint = "已缩到系统托盘，内核继续运行；右键托盘图标可退出。"
    # force_show：用户是**刚刚点了「显示窗口」**才走到这里的，那就别再最小化——
    # 否则点了半天窗口不出现，和"没反应"是同一个观感。
    start_minimized = bool(effective["config"].get("start_minimized")) and \
        coordinator.tray_available and not force_show
    window = ui_native.NativeWindow(shell, loaded["config"], effective, service,
                                    tray_hint=hint, coordinator=coordinator,
                                    start_minimized=start_minimized)
    if tray is not None:
        tray.on_show = window.show
    if start_minimized:
        window.hide()
    code = window.run()
    coordinator.finalize("原生窗口事件循环结束")
    return code


def _run_web(shell, loaded, effective, service, coordinator, tray=None,
             force_show=False, open_settings=False):
    """内嵌看板。返回 (exit_code, 失败原因)；正常退出时原因为空串。"""
    import ui_web
    window = ui_web.WebWindow(
        shell, loaded["config"], effective, service,
        logger=lambda text: logging_setup.log("[web] " + text),
        tray=tray,
        # 见 _run_native 的 force_show：点了「显示窗口」就不该再最小化。
        start_minimized=bool(effective["config"].get("start_minimized"))
        and not force_show)
    if tray is not None:
        tray.on_show = window.show_window
        # 托盘「设置…」走界面层：窗口在就唤醒并打开设置，窗口没了就重建后打开
        tray.on_settings = window.show_settings
    if open_settings:
        # 这一次托盘点击的目的就是"打开设置"，而窗口是**为此才刚建的**：
        # 注入层还没就绪，所以记成待办，loaded 之后由注入层自己兑现。
        window.request_settings()
    coordinator.set_force_quit(window.force_close)
    window.coordinator = coordinator
    try:
        code = window.run()
    except Exception as exc:
        logging_setup.log("内嵌看板不可用：%r" % (exc,), "WARN")
        return 1, "%s" % exc
    coordinator.finalize("内嵌窗口事件循环结束")
    return code, ""


def check_update_and_notify(effective, service, tray=None, log=None,
                            source="启动检查", force=False):
    """查一次更新，并在**发现新版本且这个版本还没提示过**时提醒一次。

    启动检查（D）和周期检查（A）共用这一份逻辑，避免两处写法漂移。

    * `force=False` 时先看节流：`updater.CHECK_MIN_INTERVAL` 内不重复联网
      （连点、频繁重启都不会打爆 GitHub 未认证限流 60 次/小时）；
    * 同一个版本**只提醒一次**（`update-state.json` 的 `notified_version`），
      否则每 6 小时的周期检查会反复弹同一个气泡，用户很快就会无视它；
    * 无新版本 / 网络失败都**不打扰**（各写一行日志）；
    * 绝不自动安装：安装必然停内核，必须用户点。

    返回 dict：ok / available / latest_version / notified / message。
    """
    log = log or logging_setup.log
    result = {"ok": False, "available": False, "latest_version": "",
              "notified": False, "message": ""}
    try:
        import updater as updater_mod
        updater = updater_mod.Updater(effective, service, paths.shell_dir())
    except Exception as exc:
        log("%s：更新模块不可用（%s）" % (source, exc), "WARN")
        result["message"] = "更新模块不可用"
        return result

    # 节流：刚查过就不重复联网（force=True 用于用户手动点「检查更新」）
    if not force:
        try:
            if updater.check_is_fresh(updater_mod.CHECK_MIN_INTERVAL):
                age = updater.seconds_since_last_check()
                log("%s：距上次检查才 %.0f 分钟，跳过联网" % (source, (age or 0) / 60))
                result["ok"] = True
                result["message"] = "刚刚检查过"
                # 即使跳过联网，也要把"有新版但没提示过"的情况补上提示
                plan = updater.cached_plan()
                if plan is not None and not updater.was_notified(
                        plan.latest_version):
                    result["available"] = True
                    result["latest_version"] = plan.latest_version
                return result
        except Exception as exc:
            log("%s：节流判断失败（%r），继续检查" % (source, exc), "WARN")

    try:
        plan = updater.check()
    except Exception as exc:
        log("%s失败：%s（不影响使用）" % (source, exc), "WARN")
        result["message"] = "检查失败：%s" % exc
        return result

    result["ok"] = True
    result["latest_version"] = getattr(plan, "latest_version", "") or ""
    if not getattr(plan, "available", False):
        log("%s：已是最新 %s" % (source, plan.current_version or "?"))
        result["message"] = "已是最新版本 %s" % (plan.current_version or "?")
        return result

    result["available"] = True
    message = "发现新内核版本 %s（当前 %s）" % (plan.latest_version,
                                             plan.current_version)
    result["message"] = message

    already = False
    try:
        already = updater.was_notified(plan.latest_version)
    except Exception:
        already = False
    if already:
        log("%s：%s 已经提示过，不再重复打扰" % (source, plan.latest_version))
        return result

    result["notified"] = True
    log("%s：%s；从托盘「检查更新」可安装" % (source, message))
    if tray is not None:
        try:
            tray.notify(message + "。从托盘菜单「检查更新」可安装。")
        except Exception:
            pass
    try:
        updater.mark_notified(plan.latest_version)
    except Exception:
        pass
    return result


def schedule_launch_update_check(effective, service, tray=None, log=None):
    """`check_update_on_launch`：启动后台静默检查一次更新。

    这个键以前是**死配置**（config.py 里定义了、没有任何代码读它，见交接文档
    §7.7 一类的死键问题）。语义按"安静"设计：延迟几秒再查（别和启动抢）、
    只有发现新版本才说话、绝不自动安装。2026-10-18 起默认开启。

    返回线程对象（测试可以 join）；未开启时返回 None。
    """
    config = effective.get("config") or {}
    if not config.get("check_update_on_launch"):
        return None
    log = log or logging_setup.log

    def runner():
        time.sleep(LAUNCH_UPDATE_DELAY)
        check_update_and_notify(effective, service, tray, log=log,
                                source="启动检查")

    thread = threading.Thread(target=runner, daemon=True,
                              name="wb-launch-update-check")
    thread.start()
    return thread


def schedule_periodic_update_check(effective, service, tray=None, log=None):
    """A：每 `update_check_hours` 小时静默查一次更新（默认 6 小时，0 = 关）。

    为什么需要它：只有"启动时查一次"的话，开机自启的外壳可能几天不重启，
    期间永远不知道有新版本。周期检查把这件事补上，且**仍然不打扰**：
    同一个版本只提示一次（见 check_update_and_notify）。

    返回线程对象；间隔为 0 时返回 None。
    """
    config = effective.get("config") or {}
    hours = config.get("update_check_hours", 6)
    try:
        hours = float(hours)
    except Exception:
        hours = 6.0
    if hours <= 0:
        return None
    log = log or logging_setup.log
    interval = hours * 3600.0
    # 起跑前先等一会儿：别和启动检查抢同一秒（启动检查 8 秒后就开始）
    first_delay = max(LAUNCH_UPDATE_DELAY + 60.0, min(interval, 15 * 60.0))

    def runner():
        time.sleep(first_delay)
        while True:
            check_update_and_notify(effective, service, tray, log=log,
                                    source="周期检查")
            time.sleep(interval)

    thread = threading.Thread(target=runner, daemon=True,
                              name="wb-periodic-update-check")
    thread.start()
    return thread


def _start_wakeup_watcher(handle, coordinator, tray):
    """盯着"叫窗口"事件；每次被叫就把窗口显示出来。

    为什么要单独一个线程：WaitForSingleObject 是阻塞的，而托盘循环和
    pywebview 事件循环都在各自的线程里。这里只等事件、然后**交给托盘**的
    `on_show`（它已经知道"窗口在就唤回、窗口没就重建"的正确做法），
    不去碰界面对象本身——避免在两个线程里同时操作窗口。
    """
    def runner():
        while True:
            try:
                if coordinator.explicit:
                    return
                if not paths.wait_wakeup_event(handle, 1000):
                    continue
            except Exception:
                return
            logging_setup.log("第二个实例请求显示窗口：转给托盘/界面层")
            try:
                if tray is not None and getattr(tray, "on_show", None):
                    tray.on_show()
                elif tray is not None:
                    tray.notify("外壳已在运行（窗口请从托盘打开）")
            except Exception as exc:
                logging_setup.log("处理叫窗口请求失败：%r" % (exc,), "WARN")

    thread = threading.Thread(target=runner, daemon=True,
                              name="wb-single-instance")
    thread.start()
    return thread


def start_tray(shell, loaded, effective, service, runtime, on_quit=None):
    """按偏好启动托盘。返回 (tray 或 None, 说明)。失败只记一行，不抛异常。

    可用性以**真的能不能 import** 为准，而不是 vendor 缓存里的结论：
    `vendor.ensure()` 会缓存第一次探测的结果，而第一次探测可能发生在依赖还没铺好、
    或换了解释器的时候。缓存说"不可用"就放弃，会把明明可用的托盘一直关掉。
    import 本身很快、第二次走缓存，所以这里直接试。
    """
    if not effective["config"].get("tray_enabled", True):
        return None, "设置里关闭了托盘"
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:
        return None, "pystray/Pillow 不可用：%s: %s" % (type(exc).__name__, exc)
    try:
        import tray as tray_mod
        handle = tray_mod.Tray(shell, loaded["config"], effective, service,
                               runtime=runtime, on_quit=on_quit)
        ok, reason = handle.start()
        return (handle if ok else None), reason
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def run_with_ui(shell, loaded, effective, service, coordinator, tray=None,
                force_show=False, open_settings=False):
    """按设置挑界面，失败就降级——**内核服务不能因为界面问题停摆**。

    auto / web：内嵌看板 → 原生窗口 → 命令行
    native    ：原生窗口 → 命令行

    `force_show=True` 只给"用户在托盘里点了「显示窗口」"这一条路用：
    那次点击的目的就是看见窗口，所以忽略 `start_minimized`。

    `open_settings=True` 只给"用户在托盘里点了「设置…」"这一条路用：
    窗口是为此才建的，建好之后要让设置对话框自己打开（内嵌看板走注入层；
    原生窗口没有设置面板，会在 `_run_native` 里明说）。
    """
    choice = (effective["config"].get("ui") or "auto").lower()
    info = effective.get("vendor") or {}
    if not info:
        info, _lines = probe_vendor()
        effective["vendor"] = info

    if choice in ("auto", "web"):
        if not info.get("webview"):
            reason = "；".join(info.get("errors") or []) or "pywebview 不可用"
            if choice == "web":
                print("无法使用内嵌看板：%s" % reason)
            return _run_native(shell, loaded, effective, service, coordinator,
                               tray, reason, force_show=force_show,
                               open_settings=open_settings)
        code, reason = _run_web(shell, loaded, effective, service, coordinator,
                                tray, force_show=force_show,
                                open_settings=open_settings)
        if not reason:
            return code
        print("内嵌看板启动失败（%s），改用原生窗口。" % reason)
        return _run_native(shell, loaded, effective, service, coordinator, tray,
                           reason, force_show=force_show,
                           open_settings=open_settings)

    return _run_native(shell, loaded, effective, service, coordinator, tray,
                       force_show=force_show, open_settings=open_settings)


def _build_window_from_tray(shell, loaded, effective, service, coordinator, tray,
                            open_settings=False):
    """托盘模式下点「显示窗口」/「设置…」：按正常降级链把界面**建出来**。

    返回本次界面循环的退出码。异常在这里被吃掉是**故意**的：界面建不起来时
    外壳必须还能继续当托盘守着内核，而不是把整个进程带走。
    """
    try:
        return run_with_ui(shell, loaded, effective, service, coordinator, tray,
                           force_show=True, open_settings=open_settings)
    except Exception as exc:
        logging_setup.log("托盘建窗失败：%r" % (exc,), "ERROR")
        try:
            if tray is not None:
                tray.notify("窗口打不开：%s" % exc, "内核与托盘仍在运行")
        except Exception:
            pass
        return 0


def run_tray_only(shell, loaded, effective, service, coordinator, tray=None):
    """只驻托盘、不建窗口。给开机自启用；托盘不可用时退回命令行守候。

    托盘菜单里的「显示窗口」在这种模式下**必须做点事**：开机自启起来的外壳
    压根没有窗口，所以这一次点击的语义是"把窗口建出来"；窗口一旦建好，
    `tray.on_show` 就被界面层换成 `WebWindow.show_window`——之后关掉窗口再点，
    才是"重建/唤回"（README 第一节那张表说的就是那一段）。

    pywebview / tkinter 的事件循环必须在主线程，所以托盘线程只置一个事件，
    由主线程（这里）建窗：这也是本函数唯一的等待点。
    """
    if tray is None or not getattr(tray, "available", False):
        # 没有托盘就没有唤回窗口的手段；这种情形下连内核也不主动拉起，
        # 用户下次正常双击启动时会照常工作。
        print("托盘不可用，改用命令行守候模式（不会自动启动内核）。")
        return run_headless(service, start_core=False)

    wake = threading.Event()
    # 托盘线程只能置事件、真正的建窗在主线程，所以"这次醒来是为了什么"记在
    # 事件旁边：False = 显示窗口，True = 显示窗口**并**打开设置。
    # 一个布尔量，托盘线程写、主线程读，不需要额外加锁。
    intent = {"settings": False}

    def wake_for_show():
        intent["settings"] = False
        wake.set()

    def wake_for_settings():
        intent["settings"] = True
        wake.set()

    tray.on_show = wake_for_show     # 先接管，别让第一次点击落空
    tray.on_settings = wake_for_settings
    print("外壳已驻留系统托盘（未建窗口）。右键托盘图标可显示窗口或退出。")
    while not coordinator.explicit:
        if not wake.is_set():
            time.sleep(0.5)
            continue
        wake.clear()
        want_settings = intent["settings"]
        logging_setup.log("托盘请求显示窗口：建窗（托盘模式下本来没有窗口）"
                          + ("，并打开设置" if want_settings else ""))
        code = _build_window_from_tray(shell, loaded, effective, service,
                                       coordinator, tray,
                                       open_settings=want_settings)
        if coordinator.explicit or coordinator.core_dir_changed:
            return code
        # 界面循环结束了，但用户并没有要退出（例如窗口建不起来）：
        # 回到托盘守候，并把「显示窗口」/「设置…」重新交回本循环——否则下一次
        # 点击会打到那个已经死掉的窗口对象上，变成"点了没反应"。
        logging_setup.log("界面循环结束，外壳回到托盘模式继续守候")
        tray.on_show = wake_for_show
        tray.on_settings = wake_for_settings
    return 0


def main(argv=None):
    force_utf8()
    args = parse_args(argv)

    shell = paths.shell_dir()
    data_dir = os.path.join(shell, paths.DATA_DIR_NAME)
    logging_setup.setup(data_dir)
    logging_setup.log("=" * 40)
    logging_setup.log("外壳启动 v%s（argv=%s）" % (VERSION, " ".join(sys.argv[1:]) or "无"))

    loaded = config_mod.load(shell)
    effective = config_mod.merged_effective(loaded["config"], shell,
                                            cli_overrides(args))
    for warning in loaded["warnings"]:
        logging_setup.log("设置提示：%s" % warning, "WARN")

    # ---- 单实例：同一个外壳目录只允许一个实例 ----
    # 只在"真的要常驻"的模式下检查；--print-config / --selftest / --no-gui
    # 这些是命令行工具，允许和常驻实例并存（自检还要用隔离端口）。
    guarded = not (args.print_config or args.selftest)
    if guarded:
        instance = paths.acquire_single_instance(shell)
        if not instance["acquired"]:
            # 已经有一个在跑：把它叫出来，然后自己安静退出。
            logging_setup.log("检测到同目录已有一个外壳在运行，本次启动改为唤醒它",
                              "WARN")
            ok, why = paths.signal_existing_instance(shell)
            if ok:
                print("外壳已经在运行，已把它的窗口叫出来。")
            else:
                print("外壳已经在运行（%s）；请从系统托盘打开它。" % why)
            return 0
        if instance.get("reason"):
            logging_setup.log("单实例检测：%s" % instance["reason"], "WARN")

    if effective["fatal"]:
        logging_setup.log("致命问题：%s" % effective["fatal"], "ERROR")
        print("无法启动：%s" % effective["fatal"])
        print()
        print("可用参数：--core-dir <内核目录> / --port <端口> / --print-config")
        return 2

    service = service_mod.CoreService(effective, shell)
    service.effective = effective

    vendor_info, vendor_lines = probe_vendor()
    service.effective = effective

    # 开机自启：先同步偏好（幂等），再查路径漂移——外壳被挪过位置时主动报出来
    autostart_note = ""
    try:
        import autostart as autostart_mod
        if autostart_mod.supported():
            exe = service.python.get("exe") or ""
            if effective["config"].get("autostart"):
                ok, message = autostart_mod.apply_autostart_preference(
                    True, shell, exe)
                if not ok:
                    autostart_note = "开机自启开启失败：%s" % message
                    logging_setup.log(autostart_note, "WARN")
            drift = autostart_mod.detect_drift(shell)
            if drift.get("stale"):
                autostart_note = ("开机自启项指向的路径已不是当前外壳"
                                  "（%s）——可在「设置」里重新开启一次以修正"
                                  % drift.get("command"))
                logging_setup.log(autostart_note, "WARN")
        else:
            note = autostart_mod.unsupported_note()
            if note:
                autostart_note = note
    except Exception as exc:
        logging_setup.log("开机自启检查异常：%r" % (exc,), "WARN")

    if args.print_config:
        print_banner(effective, service, vendor_info,
                     autostart_note=autostart_note)
        for line in vendor_lines:
            print("  " + line)
        return 0

    if args.selftest:
        print_banner(effective, service, vendor_info,
                     autostart_note=autostart_note)
        try:
            return run_selftest(service)
        finally:
            service.shutdown()

    if args.no_gui:
        return run_headless(service, start_core=not args.no_start)

    if effective["config"].get("auto_start_core", True):
        threading.Thread(target=service.start, daemon=True,
                         name="wb-auto-start").start()

    # 退出语义集中在这里：× 只关窗口、退出才停服务。界面层只负责把
    # "用户点了 ×" 与 "用户点了退出" 两件事分清楚。
    coordinator = ShutdownCoordinator(shell, effective["config"], service)

    runtime = {"python": service.python, "vendor": vendor_info}

    def _quit_from_tray():
        coordinator.request_exit("托盘菜单")

    tray = None
    if not args.no_tray:
        tray, tray_reason = start_tray(shell, loaded, effective, service,
                                       runtime, on_quit=_quit_from_tray)
        if tray is None and tray_reason:
            logging_setup.log("托盘未启用：%s" % tray_reason, "WARN")
            print("托盘未启用：%s" % tray_reason)
        effective["tray"] = tray
    coordinator.tray = tray
    if tray is not None:
        # 托盘菜单的「重启外壳」：更新完之后从托盘也能走这条路
        tray.on_restart = lambda: coordinator.request_shell_restart("托盘菜单")

    # 更新提醒：D（启动时静默查一次，默认开）+ A（每 6 小时再查一次）。
    # 两者共用 check_update_and_notify：都只在"发现新版且这个版本没提示过"时打扰。
    schedule_launch_update_check(effective, service, tray)
    schedule_periodic_update_check(effective, service, tray)

    # 单实例：第一个实例负责监听"叫窗口"请求（第二个实例发出来的），
    # 收到就把窗口显示出来——否则用户双击第二次会得到一个"什么都没发生"。
    wakeup_handle = None
    if guarded:
        handle, why = paths.create_wakeup_event(shell)
        if handle is not None:
            wakeup_handle = handle
            _start_wakeup_watcher(handle, coordinator, tray)
        elif why:
            logging_setup.log("叫窗口的命名事件创建失败：%s（不影响单实例本身）" % why,
                              "WARN")

    try:
        if args.tray_only:
            return run_tray_only(shell, loaded, effective, service, coordinator,
                                 tray)
        return run_with_ui(shell, loaded, effective, service, coordinator, tray)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging_setup.log("未预期的界面异常：%r" % (exc,), "ERROR")
        print("界面异常（%s），改用命令行模式。" % exc)
        try:
            return run_headless(service, start_core=False)
        except Exception:
            return 1
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        if coordinator.wants_relaunch:
            # 内核位置变了、或用户要求「重启外壳」：不要走正常收尾
            # （那会按旧路径把内核停掉/留下），直接拉起新外壳并让当前进程退出
            # ——Job Object 会回收旧内核，新外壳按新位置/新版本重新拉起。
            _spawn_replacement_shell(shell)
        service.shutdown()


if __name__ == "__main__":
    sys.exit(main())
