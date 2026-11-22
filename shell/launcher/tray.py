# -*- coding: utf-8 -*-
"""tray.py — 系统托盘常驻。

实现选型：pystray（随壳 vendor 分发）+ Pillow 画图标，跑在**独立线程**里
（`run_detached()`）。主线程留给 pywebview / tkinter 的事件循环——实测两者
同进程互不干扰。

三条约束（都来自"稳定性第一"）：
1. **托盘失败不阻塞任何其它功能**。pystray 缺了、图标坏了、`run_detached`
   抛错——一律记一行、返回一个空实现，窗口与内核照常。
2. **菜单回调绝不阻塞**。`service.stop()` 最长等 8 秒，`check()` 要联网；
   这些全部丢到工作线程里，托盘消息循环永远不卡住。
3. **勾选态每开一次菜单重新读**（如"开机自启"直接问注册表），
   不在内存里维护第二份真相。
"""

import os
import threading
import time

import logging_setup
import paths
import service as service_mod

STATE_COLORS = {
    "running": (47, 158, 68, 255),
    "starting": (210, 155, 0, 255),
    "stopping": (210, 155, 0, 255),
    "stopped": (138, 143, 152, 255),
    "failed": (224, 49, 49, 255),
    "external": (28, 126, 214, 255),
}


def _icon_image(state, size=64):
    """按状态画一个图标：圆角方块 + 状态色圆点 + 三条"网关"横线。"""
    from PIL import Image, ImageDraw

    color = STATE_COLORS.get(state, STATE_COLORS["stopped"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(2, size // 32)
    draw.rounded_rectangle((pad, pad, size - pad, size - pad),
                           radius=size // 6, fill=(35, 38, 44, 255),
                           outline=color, width=max(2, size // 16))
    line_height = max(3, size // 12)
    for index, y in enumerate((0.36, 0.5, 0.64)):
        width = 0.52 if index == 0 else 0.36
        draw.rounded_rectangle(
            (size * 0.2, size * y - line_height / 2,
             size * (0.2 + width), size * y + line_height / 2),
            radius=line_height // 2, fill=(215, 218, 224, 255))
    radius = size * 0.1
    draw.ellipse((size * 0.66, size * 0.56, size * 0.66 + radius * 2,
                  size * 0.56 + radius * 2), fill=color)
    return image


class Tray(object):
    """托盘图标 + 菜单。线程安全：所有状态都问 service / autostart。"""

    def __init__(self, shell, config, effective, service, on_show=None,
                 on_quit=None, runtime=None, on_restart=None, on_settings=None):
        self.shell = shell
        self.config = config
        self.effective = effective
        self.service = service
        self.on_show = on_show
        self.on_quit = on_quit
        # 「重启外壳」：更新之后想要一个从代码到界面都重新开始的外壳时用
        self.on_restart = on_restart
        # 「设置…」：与「显示窗口」同一类——界面层接上它就是"把设置打开"，
        # 没接上（命令行守候模式）也要说一句话，不能静默（见 _settings）。
        self.on_settings = on_settings
        self.runtime = runtime or {}
        self.icon = None
        self.available = False
        self.reason = ""
        self._last_state = ""
        self._lock = threading.Lock()
        self._busy = False
        # 「检查更新」查到的、还没安装的那一份计划。安装只认它——
        # 与界面层同样的规矩：发现与安装分两步，中间由人决定。
        self._pending_plan = None
        self._pending_lock = threading.Lock()

    # ---- 状态 -------------------------------------------------------------

    def _status_line(self):
        status = self.service.status()
        bits = [status["state_label"]]
        if status.get("port"):
            bits.append("端口 %s" % status["port"])
        accounts = status.get("accounts")
        if accounts is not None:
            bits.append("账号 %s/%s" % (accounts, status.get("accounts_ready")))
        if status.get("core_version"):
            bits.append("内核 %s" % status["core_version"])
        pending = self._pending_version()
        if pending:
            # B：悬停文字里常驻「有新版」，用户不点菜单也能一眼看到。
            bits.append("有新版 %s" % pending)
        return " · ".join(str(bit) for bit in bits)

    def _pending_version(self):
        """有没有"已知的新版本"（**不联网**，只读上次检查存下的状态）。

        为什么单独一个方法：托盘 title 每 2 秒刷新一次，绝不能在这里联网。
        `updater.cached_plan()` 读的是 update-state.json，纯本地。
        也把「检查更新」刚查到的 `_pending_plan`（内存里的结果）算进去。
        """
        plan = self._pending_plan_snapshot()
        if plan is not None and getattr(plan, "latest_version", ""):
            return plan.latest_version
        try:
            import updater as updater_mod
            cached = updater_mod.Updater(self.effective, self.service,
                                         self.shell).cached_plan()
            if cached is not None:
                return cached.latest_version
        except Exception:
            pass
        return ""

    def _work(self, label, action):
        """把阻塞动作丢到线程里跑，避免卡住托盘消息循环。"""
        with self._lock:
            if self._busy:
                self._notify("上一个操作还没结束")
                return
            self._busy = True

        def runner():
            try:
                result = action()
                if isinstance(result, tuple):
                    ok, message = result
                else:
                    ok, message = bool(result), ""
            except Exception as exc:
                ok, message = False, "%s: %s" % (type(exc).__name__, exc)
            self._busy = False
            self._notify(message or ("完成" if ok else "失败"))
            logging_setup.log("托盘操作 %s → %s" % (label, message or ok))
            self.refresh()

        threading.Thread(target=runner, daemon=True, name="wb-tray-action").start()

    def _notify(self, message):
        if not message:
            return
        try:
            if self.icon is not None:
                self.icon.notify(str(message), "WorkBuddy 网关")
        except Exception:
            pass

    def notify(self, message, title="WorkBuddy 网关"):
        """给外部调用者（如界面层"已缩到托盘"的提示）弹一次气泡。"""
        try:
            if self.icon is not None:
                self.icon.notify(str(message), str(title))
        except Exception:
            pass

    # ---- 菜单动作 ---------------------------------------------------------

    def _open_dashboard(self):
        import webbrowser
        url = self.service.status()["dashboard"]
        try:
            webbrowser.open(url)
            self._notify("已打开 %s" % url)
        except Exception as exc:
            self._notify("打开失败：%s" % exc)

    def _check_or_install_label(self, item=None):
        """菜单项：「检查更新」；已知有新版时变成「安装新版 X…」（会弹确认）。

        标签里带上「内核会重启」，让用户点之前就知道代价（与界面上
        「安装更新」那个按钮同一套纪律：代价写在按钮上，别替用户选）。
        """
        pending = self._pending_version()
        if pending:
            return "安装新版 %s…（内核会重启）" % pending
        return "检查更新"

    def _check_or_install(self):
        """点这一项：已知有新版就直接进安装流程，否则重新检查。"""
        if self._pending_version():
            self._install_update()
            return
        self._work("检查更新", self._check_update)

    def _check_update(self):
        """只检查、不安装。查到新版就把它记下来，菜单里会多出一项「安装更新」。"""
        try:
            import updater as updater_mod
        except Exception as exc:
            return False, "更新模块不可用：%s" % exc
        worker = updater_mod.Updater(self.effective, self.service, self.shell)
        try:
            plan = worker.check()
        except updater_mod.UpdateError as exc:
            return False, "检查更新失败：%s" % exc
        if not plan.latest_version:
            return False, "未能识别最新版本"
        if not plan.available:
            with self._pending_lock:
                self._pending_plan = None
            return True, "已是最新版本 %s" % plan.current_version
        with self._pending_lock:
            self._pending_plan = plan
        # 托盘没有对话框，所以把"能不能装、不能装是为什么"一次说在气泡里。
        blocked = updater_mod.install_blocked_reason(plan, self.service)
        if blocked:
            return False, ("发现新版本 %s，但现在不能安装：%s"
                           % (plan.latest_version, blocked))
        return True, ("发现新版本 %s（当前 %s）——托盘菜单里点「安装更新」"
                      % (plan.latest_version, plan.current_version))

    def _pending_plan_snapshot(self):
        with self._pending_lock:
            return self._pending_plan

    def _install_label(self, item=None):
        pending = self._pending_version()
        if not pending:
            return "安装更新"
        return "安装更新 %s" % pending

    def _install_enabled(self, item=None):
        # 磁盘缓存里已知有新版时也算"能装"（见 _plan_for_install 的说明）
        return bool(self._pending_version())

    def _plan_for_install(self):
        """要装的那份 plan：优先内存里的（刚点过「检查更新」），否则用磁盘缓存。

        为什么需要兜底（2026-10-18 用户实测报的）：启动检查 / 周期检查（A/D）
        只写 `update-state.json`，**不写内存里的 `_pending_plan`**。于是托盘
        显示「有新版 1.7.16 · 点此安装」，点下去却只弹一句"请先点检查更新"——
        纯假把式。`cached_plan()` 读的是那次检查存下来的账本（纯本地、不联网），
        装的时候 `install()` 还会用官方 API 重新取 digest 校验，安全性不打折。
        """
        plan = self._pending_plan_snapshot()
        if plan is not None:
            return plan
        try:
            import updater as updater_mod
            cached = updater_mod.Updater(self.effective, self.service,
                                         self.shell).cached_plan()
        except Exception as exc:
            logging_setup.log("读取缓存的更新计划失败：%r" % (exc,), "WARN")
            return None
        if cached is not None:
            # 记回内存：后续「安装更新」菜单项也就能用了
            with self._pending_lock:
                self._pending_plan = cached
        return cached

    def _confirm(self, title, question):
        """托盘专用的确认框。返回 True 表示用户确认。

        **为什么不用 tkinter**（2026-10-19 生产实测踩的坑）：
        托盘菜单回调跑在 pystray 自己的线程里，不是主线程。在非主线程
        `tkinter.Tk()` 建出来的对话框"能画出但收不到点击"——用户看到的是
        "点是/否都没反应、也关不掉"；而且 `messagebox.askyesno` 卡住时
        不会返回，`finally: root.destroy()` 也就执行不到，于是在外壳进程里
        堆了一层僵尸 `TkTopLevel`（当时实测残留 2 个框 + 2 个隐藏 root）。
        tkinter 的 Tk 必须跑在主线程，这是它的硬约束，不是调参能解决的。

        **改用 Win32 原生 MessageBoxW**：
        * 单个阻塞调用，内部有系统消息循环，**跨线程安全**；
        * 不创建 Tk root，**不可能留下僵尸窗口**；
        * 用 `MB_YESNOCANCEL`（不是 `MB_YESNO`）：显式给一个「取消」按钮，
          而且点右上角 × 会稳定映射到 `IDCANCEL`。实测 `MB_YESNO` 在
          跨线程 + TOPMOST 下，× 有时关不掉（框会一直挂着）——
          这正是用户反馈"点是或否都没动作、也关不掉"的一部分。

        只有 `IDYES` 才算确认；`IDNO` / `IDCANCEL` / 关窗口 一律不安装。

        失败时**返回 False**：装更新会让内核短停，宁可什么都不做，
        也不在"确认框弹不出来"时替用户做主。
        """
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.MessageBoxW.restype = ctypes.c_int
            user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                           wintypes.LPCWSTR, wintypes.UINT]

            MB_YESNOCANCEL = 0x00000003
            MB_ICONQUESTION = 0x00000020
            MB_SETFOREGROUND = 0x00010000
            MB_TOPMOST = 0x00040000
            IDYES = 6

            flags = (MB_YESNOCANCEL | MB_ICONQUESTION
                     | MB_SETFOREGROUND | MB_TOPMOST)
            result = user32.MessageBoxW(None, str(question), str(title), flags)
            logging_setup.log(
                "确认框返回：%d（6=是 7=否 2=取消/关闭）" % result)
            return result == IDYES
        except Exception as exc:
            logging_setup.log("托盘确认框不可用（%r），按“取消”处理" % (exc,), "WARN")
            try:
                self._notify("无法弹出确认框，已取消安装（请用窗口里的「检查更新」）")
            except Exception:
                pass
            return False

    def _install_update(self):
        """把「已知的新版本」装上。菜单回调只负责丢线程，安装本身在工作线程里跑。

        装之前**必问一次**：安装必然停内核（Windows 文件占用），
        等于用户的模型请求会断几秒。菜单标签上也写了代价，但误点的代价太大，
        所以再确认一次（用户 2026-10-18 明确要求）。
        """
        plan = self._plan_for_install()
        if plan is None:
            self._notify("还没有可用的更新信息，请先点「检查更新」")
            return

        # 确认放在**开线程之前**：这是主（托盘回调）线程，弹框才不会被事件循环吞掉。
        question = ("要安装内核 %s 吗（当前 %s）？\n\n"
                    "安装会停止内核并覆盖文件，期间模型请求会中断几秒，"
                    "完成后内核会自动重启。"
                    % (plan.latest_version, plan.current_version or "?"))
        if not self._confirm("安装内核更新", question):
            logging_setup.log("用户取消了内核更新安装（%s）" % plan.latest_version)
            return

        def action():
            try:
                import updater as updater_mod
            except Exception as exc:
                return False, "更新模块不可用：%s" % exc
            blocked = updater_mod.install_blocked_reason(plan, self.service)
            if blocked:
                return False, blocked
            worker = updater_mod.Updater(self.effective, self.service, self.shell)
            result = worker.install(plan, progress=self._update_progress)
            if result.get("ok"):
                with self._pending_lock:
                    self._pending_plan = None
            # 气泡只放第一行：更新失败时的完整说明（含备份路径）本来就是
            # 多行的，托盘气泡显示不下，完整内容在 data\shell.log 里。
            message = (result.get("message") or "").strip()
            first = message.splitlines()[0] if message else ""
            if result.get("ok"):
                first += "　如界面仍是旧的，可在托盘点「重启外壳」"
            return bool(result.get("ok")), first

        self._work("安装更新", action)

    def _restart_shell(self):
        """重启外壳（更新之后用）：停内核 + 停托盘 + 换一个新进程。"""
        if self.on_restart is None:
            self._notify("当前外壳不支持重启")
            return
        self._work("重启外壳", self.on_restart)

    def _update_progress(self, stage, done=0, total=0, text=""):
        """托盘的进度只体现在悬停文字上（气泡会被系统限流，标题不会）。"""
        line = text or stage or ""
        if total:
            try:
                line += " %d%%" % int(int(done) * 100 / max(1, int(total)))
            except Exception:
                pass
        try:
            if self.icon is not None:
                self.icon.title = "WorkBuddy 网关 — 更新中：%s" % line
        except Exception:
            pass

    def _open_folder(self, folder):
        try:
            os.startfile(folder)
        except Exception as exc:
            self._notify("打开目录失败：%s" % exc)

    def _toggle_autostart(self, icon=None, item=None):
        """切换开机自启。勾选态直接问注册表，不在内存里维护第二份真相。"""
        def action():
            import autostart as autostart_mod
            if not autostart_mod.supported():
                return False, autostart_mod.unsupported_note() or "当前平台不支持"
            if autostart_mod.is_enabled():
                return autostart_mod.disable()
            exe = (self.runtime.get("python") or {}).get("exe") or ""
            return autostart_mod.enable(self.shell, exe)
        self._work("切换开机自启", action)

    # ---- 菜单 -------------------------------------------------------------

    def _build_menu(self):
        import pystray

        autostart_state = False
        try:
            import autostart as autostart_mod
            autostart_state = autostart_mod.is_enabled()
        except Exception:
            pass

        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_line(),
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("显示窗口", lambda *a: self._show(), default=True),
            pystray.MenuItem("打开看板（浏览器）", lambda *a: self._open_dashboard()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("启动内核",
                             lambda *a: self._work("启动", self.service.start),
                             enabled=lambda item: self._state_is(
                                 "stopped", "failed")),
            pystray.MenuItem("停止内核", lambda *a: self._work("停止", self.service.stop),
                             enabled=lambda item: self._state_is(
                                 "running", "starting", "external")),
            pystray.MenuItem("重启内核", lambda *a: self._work("重启", self.service.restart),
                             enabled=lambda item: self._state_is(
                                 "running", "starting", "external")),
            # 「重启外壳」按语义属于**内核/进程操作**（它会让内核短停一次），
            # 原来放在最下面的设置区、和「设置 / 退出」并排，容易被当成"设置类";
            # 2026-10-18 按用户要求移到「重启内核」下方。
            pystray.MenuItem("重启外壳", lambda *a: self._restart_shell()),
            pystray.Menu.SEPARATOR,
            # 复制 API 地址 / API Key 交给内核看板（网关页与设置页各有「复制」），
            # 外壳不再重复一份。
            # B：已知有新版时，这一项直接显示版本号并**真的能装**（点它先弹确认）；
            #    平时就是「检查更新」。2026-10-18 把原来那一项独立的「安装更新」
            #    去掉了：两项都指向同一个动作，菜单里留一行就够。
            pystray.MenuItem(lambda item: self._check_or_install_label(),
                             lambda *a: self._check_or_install()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自启", self._toggle_autostart,
                             checked=lambda item: _autostart_checked()),
            pystray.MenuItem("打开数据目录",
                             lambda *a: self._open_folder(paths.data_dir())),
            pystray.MenuItem("打开内核目录",
                             lambda *a: self._open_folder(self.service.core_dir)),
            # 设置原来是"只能从窗口底栏右键进"——而 `--tray-only`（开机自启那种）
            # 压根没有窗口，够不到。放进托盘就补上了这个缺口：点它会先把窗口
            # 建出来，再打开同一个设置对话框（见 __main__.run_tray_only）。
            pystray.MenuItem("设置…", lambda *a: self._settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda *a: self._quit()),
        )

    def _show(self, *args):
        if self.on_show is None:
            # 没有可显示的界面（例如命令行守候模式）。**别静默**：静默正是
            # "点了没反应"里最难查的那一种——日志里连一行都不留。
            logging_setup.log("托盘请求显示窗口，但当前没有可显示的界面", "WARN")
            self._notify("当前模式没有窗口可显示")
            return
        try:
            self.on_show()
        except Exception as exc:
            logging_setup.log("托盘唤起窗口失败：%r" % (exc,), "WARN")
            self._notify("唤起窗口失败：%s" % exc)

    def _settings(self, *args):
        """托盘「设置…」：把设置界面打开（窗口不在就先建出来，再打开）。

        与 `_show` 同一条纪律：**没接上回调时不许静默**——命令行守候、或
        原生窗口兜底（那种界面里没有设置面板）都只能明说，不能点了没反应。
        """
        if self.on_settings is None:
            logging_setup.log("托盘请求打开设置，但当前界面没有设置面板", "WARN")
            self._notify("当前模式没有设置面板")
            return
        try:
            self.on_settings()
        except Exception as exc:
            logging_setup.log("托盘打开设置失败：%r" % (exc,), "WARN")
            self._notify("打开设置失败：%s" % exc)

    def _quit(self, *args):
        logging_setup.log("托盘请求退出")
        if self.on_quit is not None:
            try:
                self.on_quit()
                return
            except Exception as exc:
                logging_setup.log("托盘退出回调异常：%r" % (exc,), "ERROR")
        self.stop()

    # ---- 生命周期 ---------------------------------------------------------

    def start(self):
        """建图标并 `run_detached()`。返回 (ok, message)。"""
        try:
            import pystray
        except Exception as exc:
            self.reason = "pystray 不可用：%s: %s" % (type(exc).__name__, exc)
            logging_setup.log("托盘未启用：" + self.reason, "WARN")
            return False, self.reason
        try:
            status = self.service.status()
            image = _icon_image(status["state"])
            icon = pystray.Icon("wb-shell", image, "WorkBuddy 网关",
                                self._build_menu())
            icon.run_detached()
            self.icon = icon
            self.available = True
            self._last_state = status["state"]
            logging_setup.log("托盘已启用（pystray %s）"
                              % getattr(pystray, "__version__", "?"))
            threading.Thread(target=self._refresh_loop, daemon=True,
                             name="wb-tray-state").start()
            return True, ""
        except Exception as exc:
            self.reason = "%s: %s" % (type(exc).__name__, exc)
            logging_setup.log("托盘启动失败：" + self.reason, "WARN")
            self.available = False
            return False, self.reason

    def _state_is(self, *states):
        """菜单项 enabled 回调：当前内核状态是否在这几个之一。

        为什么不在菜单里写死：pystray 的 enabled 每次展开菜单都会重新求值，
        所以这里只读一次 status()（轻量，带锁的内部快照）。
        """
        try:
            return self.service.status()["state"] in states
        except Exception:
            return False

    def refresh(self):
        """状态变了就换图标配色与悬停文字；**内核意外退出时弹一次气泡**。

        以前这里只换图标颜色：内核崩了，屏幕上什么都不会发生，用户要等到
        客户端报错才知道 8788 断了（交接文档 §9 记过"断流没人知道"）。
        现在把"从运行中变成失败/停止"这一刻变成一次明确的通知；到底是不是
        意外由 service 的守护状态决定（用户主动停的不弹，免得吵）。
        """
        if not self.available or self.icon is None:
            return
        try:
            status = self.service.status()
            state = status["state"]
            previous = self._last_state
            if state != self._last_state:
                self._last_state = state
                self.icon.icon = _icon_image(state)
                self._announce_state_change(previous, status)
            self.icon.title = "WorkBuddy 网关 — %s" % self._status_line()
        except Exception:
            pass

    def _announce_state_change(self, previous, status):
        """状态跃迁里真正值得打扰用户的那几种，弹气泡。"""
        state = status["state"]
        was_live = previous in ("running", "starting")
        if not was_live:
            return
        if state == "failed":
            detail = status.get("detail") or "内核已退出"
            note = status.get("watchdog_note") or ""
            message = "内核异常退出：%s" % detail
            if status.get("watchdog"):
                message += "。正在自动恢复…"
            elif note:
                message += "。" + note
            self._notify(message)
        elif state == "stopped":
            # 正常停止（用户在托盘点的）不吵；这里只提醒"它自己停了"
            self._notify("内核已停止（不是外壳主动停的），"
                         "请从托盘「启动内核」重新拉起")

    def _refresh_loop(self):
        while self.available:
            self.refresh()
            time.sleep(2.0)

    def stop(self):
        self.available = False
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None


def _autostart_checked():
    try:
        import autostart as autostart_mod
        return bool(autostart_mod.is_enabled())
    except Exception:
        return False
