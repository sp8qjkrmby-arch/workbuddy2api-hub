# -*- coding: utf-8 -*-
"""ui_native.py — tkinter 原生窗口（P1 的界面，也是永远可用的兜底界面）。

设计上刻意只做「控制条 + 状态 + 日志」三件事：

* 状态与操作：启停、重启、打开看板、复制 API 地址；
* 日志：内核 stdout/stderr 的实时视图（看板自带的 /logs 才是权威）；
* **不重复看板的功能**——账号管理、签到、任务、代理出口全部留给看板本体。

这样这个窗口可以整块替换掉（P3 会加 WebView2 内嵌看板，P4 加托盘），
而内核、更新链路、数据一条都不受影响。
"""

import os
import sys
import time

import tkinter as tk
from tkinter import messagebox, ttk

import logging_setup
import service as service_mod

COLORS = {
    "stopped": "#8a8f98",
    "starting": "#d29b00",
    "running": "#2f9e44",
    "stopping": "#d29b00",
    "failed": "#e03131",
    "external": "#1c7ed6",
}

POLL_MS = 700


def human_uptime(seconds):
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return "%d天%02d:%02d:%02d" % (days, hours, minutes, secs)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


class NativeWindow(object):
    """主窗口。UI 层不做任何业务判断，全部问 service。"""

    def __init__(self, shell, config, effective, service, on_exit=None,
                 tray_hint=None, coordinator=None, start_minimized=False):
        self.shell = shell
        self.config = config
        self.effective = effective
        self.service = service
        self.on_exit = on_exit
        self.tray_hint = tray_hint
        self.coordinator = coordinator
        self.start_minimized = bool(start_minimized)
        self._closing = False
        self._hidden_to_tray = False
        self._last_seq = service.log_seq()
        self._busy = False

        self.root = tk.Tk()
        self.root.title("WorkBuddy2API-Hub 网关")
        self.root.minsize(760, 480)
        self._restore_geometry()
        self._set_icon()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._tick)
        self._append("[shell] 外壳已启动。内核目录：%s" % service.core_dir)
        self._append("[shell] Python：%s" % self._describe_python())
        for warning in effective.get("warnings") or []:
            self._append("[shell] 提示：%s" % warning)
        for warning in service.python.get("warnings") or []:
            self._append("[shell] 提示：%s" % warning)

    # ---- 窗口骨架 ---------------------------------------------------------

    def _describe_python(self):
        info = self.service.python
        if not info.get("exe"):
            return "未找到可用 Python"
        return "%s @ %s（%s）" % (info.get("version") or "?", info.get("exe"),
                                 info.get("source") or "?")

    def _restore_geometry(self):
        geometry = (self.config.get("window_geometry") or "").strip()
        if geometry and "x" in geometry:
            try:
                self.root.geometry(geometry)
                return
            except Exception:
                pass
        self.root.geometry("980x660")

    def _set_icon(self):
        for name in ("app.ico", "tray.ico"):
            candidate = os.path.join(self.shell, name)
            if os.path.isfile(candidate):
                try:
                    self.root.iconbitmap(default=candidate)
                    return
                except Exception:
                    pass

    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Big.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Hint.TLabel", foreground="#6b7280")

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        # 状态区
        status = ttk.LabelFrame(outer, text="状态", padding=8)
        status.pack(fill="x")
        row = ttk.Frame(status)
        row.pack(fill="x")
        self.dot = tk.Canvas(row, width=14, height=14, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 6))
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=COLORS["stopped"],
                                           outline="")
        self.state_label = ttk.Label(row, text="已停止", style="Big.TLabel")
        self.state_label.pack(side="left")

        self.detail_label = ttk.Label(status, text="", style="Hint.TLabel",
                                      wraplength=920, justify="left")
        self.detail_label.pack(fill="x", pady=(4, 0))

        grid = ttk.Frame(status)
        grid.pack(fill="x", pady=(6, 0))
        self.fields = {}
        specs = [("端口", 0, 0), ("账号", 1, 0), ("区域", 2, 0),
                 ("内核版本", 0, 1), ("运行时长", 1, 1), ("Python", 2, 1)]
        for name, column, row_index in specs:
            cell = ttk.Frame(grid)
            cell.grid(row=row_index, column=column, sticky="w", padx=(0, 18), pady=1)
            ttk.Label(cell, text=name + "：", style="Hint.TLabel").pack(side="left")
            value = ttk.Label(cell, text="—", style="Status.TLabel")
            value.pack(side="left")
            self.fields[name] = value
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)

        # 操作区
        actions = ttk.LabelFrame(outer, text="操作", padding=8)
        actions.pack(fill="x", pady=(8, 0))
        line1 = ttk.Frame(actions)
        line1.pack(fill="x")
        self.btn_start = ttk.Button(line1, text="启动内核", command=self.do_start)
        self.btn_stop = ttk.Button(line1, text="停止内核", command=self.do_stop)
        self.btn_restart = ttk.Button(line1, text="重启内核", command=self.do_restart)
        self.btn_dashboard = ttk.Button(line1, text="打开看板", command=self.do_dashboard)
        for widget in (self.btn_start, self.btn_stop, self.btn_restart,
                       self.btn_dashboard):
            widget.pack(side="left", padx=(0, 6))

        line2 = ttk.Frame(actions)
        line2.pack(fill="x", pady=(6, 0))
        self.btn_open_data = ttk.Button(line2, text="打开数据目录",
                                        command=self.do_open_data)
        self.btn_reload = ttk.Button(line2, text="重新探测",
                                     command=self.do_reload)
        self.btn_exit = ttk.Button(line2, text="退出", command=self.exit_application)
        for widget in (self.btn_open_data, self.btn_reload):
            widget.pack(side="left", padx=(0, 6))
        self.btn_exit.pack(side="right")

        self.hint = ttk.Label(
            actions,
            text="账号管理、签到、任务、代理出口、备注、复制 API 地址 / API Key "
                 "都在内核看板里（点「打开看板」）——外壳不重复造这些轮子。",
            style="Hint.TLabel", wraplength=920, justify="left")
        self.hint.pack(fill="x", pady=(6, 0))

        # 日志区
        logs = ttk.LabelFrame(outer, text="内核日志（最近 3000 行，完整日志见数据目录）",
                              padding=6)
        logs.pack(fill="both", expand=True, pady=(8, 0))
        wrap = ttk.Frame(logs)
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=16, wrap="none", undo=False,
                                background="#1e1f22", foreground="#d7dae0",
                                insertbackground="#d7dae0",
                                font=("Consolas", 9), borderwidth=0)
        scroll_y = ttk.Scrollbar(wrap, orient="vertical",
                                 command=self.log_text.yview)
        scroll_x = ttk.Scrollbar(wrap, orient="horizontal",
                                 command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=scroll_y.set,
                                xscrollcommand=scroll_x.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.log_text.configure(state="disabled")

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(4, 0))
        self.status_line = ttk.Label(bottom, text="就绪", style="Hint.TLabel")
        self.status_line.pack(side="left")
        ttk.Button(bottom, text="清空视图", command=self.do_clear).pack(side="right")

    # ---- 界面动作 ---------------------------------------------------------

    def _set_status_line(self, text):
        try:
            self.status_line.configure(text=text)
        except Exception:
            pass

    def _run_async(self, action, label):
        """把可能阻塞的操作（启停、探测）放到线程里，避免界面卡死。"""
        if self._busy:
            self._set_status_line("上一个操作还没结束，请稍候…")
            return
        self._busy = True
        self._set_status_line(label + "…")

        def worker():
            try:
                result = action()
                ok, message = result if isinstance(result, tuple) else (bool(result), "")
            except Exception as exc:
                ok, message = False, "%s: %s" % (type(exc).__name__, exc)
            self._busy = False
            text = message if message else ("完成" if ok else "失败")
            if self.root is not None:
                try:
                    self.root.after(0, lambda t=text: self._set_status_line(t))
                except Exception:
                    pass

        import threading
        threading.Thread(target=worker, daemon=True, name="wb-shell-action").start()

    def do_start(self):
        self._run_async(self.service.start, "正在启动内核")

    def do_stop(self):
        if not messagebox.askyesno("确认", "停止内核后所有模型请求都会失败，确定停止？",
                                   parent=self.root):
            return
        self._run_async(self.service.stop, "正在停止内核")

    def do_restart(self):
        if not messagebox.askyesno("确认", "重启期间会有几秒不可用，确定重启？",
                                   parent=self.root):
            return
        self._run_async(self.service.restart, "正在重启内核")

    def do_dashboard(self):
        url = self.service.status()["dashboard"]
        try:
            os.startfile(url)
            self._set_status_line("已在默认浏览器打开 %s" % url)
        except Exception as exc:
            self._set_status_line("打开看板失败：%s" % exc)

    def do_open_data(self):
        folder = os.path.dirname(logging_setup.log_path()) if logging_setup.log_path() \
            else self.shell
        try:
            os.startfile(folder)
            self._set_status_line("已打开数据目录：%s" % folder)
        except Exception as exc:
            self._set_status_line("打开目录失败：%s" % exc)

    def do_reload(self):
        """重新探测 Python 与内核目录——用户在别处改了环境时不用重启外壳。"""
        import config as config_mod

        try:
            fresh = config_mod.load(self.shell)
            merged = config_mod.merged_effective(fresh["config"], self.shell)
            if merged.get("fatal"):
                self._set_status_line("重新探测失败：%s" % merged["fatal"])
                return
            self.service.__init__(merged, self.shell)
            self._append("[shell] 已重新探测：%s" % self._describe_python())
            self._set_status_line("重新探测完成")
        except Exception as exc:
            self._set_status_line("重新探测失败：%s" % exc)

    def do_clear(self):
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    # ---- 周期性刷新 -------------------------------------------------------

    def _tick(self):
        if self._closing:
            return
        try:
            self._refresh_status()
            self._drain_logs()
        except Exception as exc:
            logging_setup.log("界面刷新异常：%r" % (exc,), "WARN")
        finally:
            if not self._closing:
                try:
                    self.root.after(POLL_MS, self._tick)
                except Exception:
                    pass

    def _refresh_status(self):
        status = self.service.status()
        color = COLORS.get(status["state"], COLORS["stopped"])
        try:
            self.dot.itemconfigure(self.dot_id, fill=color)
        except Exception:
            pass
        self.state_label.configure(text=status["state_label"])
        self.detail_label.configure(text=status["detail"] or "")
        self.fields["端口"].configure(text=str(status["port"]))
        accounts = status.get("accounts")
        if accounts is None:
            self.fields["账号"].configure(text="—")
        else:
            self.fields["账号"].configure(
                text="%s（可用 %s）" % (accounts, status.get("accounts_ready")))
        self.fields["区域"].configure(text=status.get("realm") or "—")
        self.fields["内核版本"].configure(text=status.get("core_version") or "未知")
        self.fields["运行时长"].configure(
            text=human_uptime(status["uptime"]) if status["uptime"] else "—")
        self.fields["Python"].configure(
            text="%s（%s）" % (status.get("python") or "?",
                              status.get("python_source") or "?"))

        running = status["state"] == service_mod.RUNNING
        external = status["state"] == service_mod.EXTERNAL
        starting = status["state"] == service_mod.STARTING
        stopping = status["state"] == service_mod.STOPPING
        idle = status["state"] in (service_mod.STOPPED, service_mod.FAILED)
        self.btn_start.configure(state="normal" if idle and not self._busy else "disabled")
        self.btn_restart.configure(state="normal" if (running or external) and
                                   not self._busy else "disabled")
        self.btn_stop.configure(state="normal" if (running or starting) and
                                not self._busy else "disabled")
        self.btn_reload.configure(state="normal" if not self._busy else "disabled")

    def _drain_logs(self):
        seq = self.service.log_seq()
        if seq == self._last_seq:
            return
        lines = self.service.logs(0)
        self._last_seq = seq
        for line in lines[-200:]:
            self._append(line, timestamped=False)

    def _append(self, text, timestamped=True):
        try:
            self.log_text.configure(state="normal")
            if timestamped:
                text = "%s %s" % (time.strftime("%H:%M:%S"), text)
            self.log_text.insert("end", text + "\n")
            # 只保留 4000 行，界面内存不无限涨。
            total = int(self.log_text.index("end-1c").split(".")[0])
            if total > 4000:
                self.log_text.delete("1.0", "%d.0" % (total - 4000))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    # ---- 退出 -------------------------------------------------------------

    def show(self):
        """从托盘唤起：还原并置顶。"""
        try:
            self._hidden_to_tray = False
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    def hide(self):
        """缩到托盘（窗口只是隐藏，内核继续服务）。"""
        try:
            self._hidden_to_tray = True
            self.root.withdraw()
            if self.tray_hint:
                self._set_status_line(self.tray_hint)
        except Exception:
            pass

    def on_close(self):
        """右上角的 ×：**只关窗口**（隐藏到托盘），不停内核。

        托盘不可用时不能只隐藏——没有图标可以唤回窗口。这时交回协调器去问，
        让它按同一套语义处理（宁可问一次，也不把用户关进没有出口的状态）。
        """
        if self._closing:
            return
        if self.coordinator is not None and self.coordinator.tray_available \
                and self.effective["config"].get("close_to_tray", True):
            self.hide()
            self.coordinator.notify_tray_hint()
            return
        if self.coordinator is not None:
            if not self.coordinator.confirm_if_needed("原生窗口 ×"):
                # 用户选择只关窗口：保留内核，只销毁界面
                self._closing = True
                try:
                    self.config["window_geometry"] = self.root.geometry()
                except Exception:
                    pass
                self.root.destroy()
                return
            self._closing = True
            self.coordinator.request_exit("原生窗口 ×（已确认）")
            try:
                self.root.destroy()
            except Exception:
                pass
            return

        # 没有协调器（理论上不会走到）：保守处理，问一句再退。
        state = self.service.status()["state"]
        if state in (service_mod.RUNNING, service_mod.STARTING):
            if not messagebox.askyesno(
                    "退出",
                    "内核正在运行。退出外壳会一并停止内核，模型请求将中断。\n\n确定退出？",
                    parent=self.root):
                return
        self._closing = True
        self.service.shutdown()
        try:
            self.root.destroy()
        except Exception:
            pass

    def exit_application(self):
        """底部「退出」按钮：停内核 + 结束外壳（与托盘「退出」同一语义）。"""
        if self._closing:
            return
        state = self.service.status()["state"]
        if state in (service_mod.RUNNING, service_mod.STARTING):
            if not messagebox.askyesno(
                    "退出",
                    "退出会停止内核，模型请求将中断。\n\n确定退出？",
                    parent=self.root):
                return
        self._closing = True
        try:
            self.config["window_geometry"] = self.root.geometry()
        except Exception:
            pass
        if self.coordinator is not None:
            self.coordinator.request_exit("原生窗口退出按钮")
        else:
            self.service.shutdown()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()
        return 0
