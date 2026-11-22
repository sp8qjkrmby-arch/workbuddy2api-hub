# -*- coding: utf-8 -*-
"""service.py — 内核的生命周期。

这个模块承担整个方案里最关键的职责：**内核一定是你双击 .bat 时那个内核**。
它只通过命令行参数与内核交互（`--port/--host/--lan/--api-key/
--accounts-dir/--usage-dir/--panel-password`），不导入内核的任何模块、
不写内核的任何文件。所以外壳再怎么改、内核怎么升级，两边都不会互相绊住。

四个要点：
* 启动后轮询 `GET /health`，用内核自己的判据（`accounts` 字段）区分
  「我们的内核」和「别人占了端口」；
* stdout+stderr 合成一根管道，逐行落 `data\\core-<port>.log` 并进内存环形缓冲；
* 停止是三级降级（TerminateProcess → taskkill /T /F → Job 兜底），
  停止后必须复验端口真的释放了；
* 内核版本从 `/health` 拿不到时，正则读 `wb_proxy.py` 的版本串。
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import logging_setup
import paths
import proc

# 状态
STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
FAILED = "failed"
EXTERNAL = "external"      # 端口上已经有我们自己的内核在跑（非本外壳所启）

STATE_LABELS = {
    STOPPED: "已停止",
    STARTING: "启动中",
    RUNNING: "运行中",
    STOPPING: "停止中",
    FAILED: "启动失败",
    EXTERNAL: "已在运行（外部启动）",
}

LOG_BUFFER_LINES = 3000
START_TIMEOUT = 30.0
STOP_GRACE = 8.0
POLL_INTERVAL = 1.0

# 内核守护（watchdog）的重启退避：单位秒。最后一项用完后就不再自动拉起，
# 只留一条"需要人工介入"的提示——避免内核因为配置错误（端口被占、内核文件坏了）
# 无限重启，把日志刷爆、把用户晾在"一直在重启"的假象里。
WATCHDOG_BACKOFF = (5.0, 15.0, 60.0)
# 用户可配的"最多自动重启几次"的钳制范围与默认值。
WATCHDOG_MAX_RETRIES = 3
WATCHDOG_RETRY_RANGE = (0, 10)

# 内核 stdout 日志（data\core-<port>.log）的轮转阈值与保留份数。
CORE_LOG_MAX_BYTES = 8 * 1024 * 1024
CORE_LOG_KEEP = 3


def _as_int_config(value, fallback):
    """把配置里的"看起来像整数"的值取成 int；取不到就用 fallback。

    故意不让它抛异常：配置是从用户手编的 shell.json 来的，坏值是常态
    （config.py 已经做过一遍归一化，但 service 也可能被测试直接构造）。
    """
    if isinstance(value, bool):
        return fallback
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback

VERSION_RE = re.compile(r'"version"\s*:\s*"([0-9][^"]*)"')


_VERSION_CACHE = {}


def read_core_version(core_dir):
    """"从 wb_proxy.py 里读版本串；读不到返回空串。

    带 `(mtime, size)` 缓存：`status()` 每次都会问一遍版本（底栏每 0.5 秒推一次
    状态），不做缓存就等于每半秒重读一遍内核源码。文件一改缓存自动失效
    （更新覆盖、手工替换都算"改"）。
    """
    if not core_dir:
        return ""
    entry = os.path.join(core_dir, paths.CORE_ENTRY_NAME)
    try:
        stat = os.stat(entry)
        key = (stat.st_mtime_ns, stat.st_size)
        cached = _VERSION_CACHE.get(entry)
        if cached and cached[0] == key:
            return cached[1]
    except Exception:
        return ""
    try:
        with open(entry, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(400000)
    except Exception:
        return ""
    match = VERSION_RE.search(head)
    version = match.group(1).strip() if match else ""
    _VERSION_CACHE[entry] = (key, version)
    return version


class _NoProxy(urllib.request.ProxyHandler):
    """本机回环请求不能被系统代理劫持。"""

    def __init__(self):
        urllib.request.ProxyHandler.__init__(self, {})


_opener = urllib.request.build_opener(_NoProxy())


def probe_health(port, host="127.0.0.1", timeout=2.0):
    """探一次 /health。

    返回 dict：state ∈ {"ours", "foreign", "down", "error"}，其余字段按需。
    """
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = "http://%s:%d/health" % (target, port)
    try:
        with _opener.open(url, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
        data = json.loads(body)
    except urllib.error.HTTPError as exc:
        return {"state": "error", "message": "HTTP %d" % exc.code}
    except Exception as exc:
        return {"state": "down", "message": "%s: %s" % (type(exc).__name__, exc)}
    if not isinstance(data, dict):
        return {"state": "foreign", "message": "回环端口上的服务不是 JSON 对象"}
    if data.get("service") or "accounts" not in data:
        return {"state": "foreign",
                "message": "回环端口已被其它程序占用：%s"
                           % (data.get("service") or "未知 HTTP 服务")}
    data["state"] = "ours"
    data["url"] = url
    return data


def probe_panel_status(port, host="127.0.0.1", timeout=2.0):
    """问一次 `/panel/status`——这个接口**不需要登录**就能问。

    返回 dict；问不到就返回空 dict。外壳拿它来判断"面板到底要不要密码、
    这次窗口有没有登录"，而不是去猜看板的 DOM。
    """
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = "http://%s:%d/panel/status" % (target, port)
    try:
        with _opener.open(url, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
        data = json.loads(body)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class CoreService(object):
    """内核子进程 + 状态 + 日志。线程安全，UI 层只读它。"""

    def __init__(self, effective, shell_path=None):
        self.lock = threading.RLock()
        self.effective = effective
        self.shell_path = shell_path or paths.shell_dir()
        config = effective["config"]
        self.core_dir = effective["core_dir"]
        self.accounts_dir = effective["accounts_dir"]
        self.usage_dir = effective["usage_dir"]
        self.port = config["port"]
        self.host = config["host"]
        self.lan = bool(config.get("lan"))
        self.api_key = config.get("api_key")
        self.panel_password = config.get("panel_password")

        self.python = paths.find_python(
            core_path=self.core_dir,
            runtime_dir=config.get("runtime_dir") or "",
            shell_path=self.shell_path)
        self.core_version = read_core_version(self.core_dir)

        self.state = STOPPED
        self.detail = ""
        self.pid = 0
        self.started_at = 0.0
        self.stopped_at = 0.0
        self.exit_code = None
        self.health = {}
        self.used_job = False
        self.command = []

        self._spawned = None
        self._job = None
        self._reader = None
        self._log_lines = []
        self._log_seq = 0
        self._log_file = None
        self._log_size = 0
        self._log_lock = threading.Lock()
        self._subscribers = []
        self._health_stop = threading.Event()

        # --- 内核守护 -----------------------------------------------------
        # `_stop_state`：记录"这次退出是用户/外壳要求的"还是"内核自己没了"。
        # `stop()` 会先把 state 置成 STOPPING，`_read_loop` 看到 STOPPING 就不重启；
        # 看到 RUNNING/STARTING 消失 = 意外退出 → 交给守护线程退避重启。
        self._stop_requested = False
        self._watchdog_stop = threading.Event()
        self._watchdog_thread = None
        self.watchdog = bool(config.get("core_watchdog", True))
        retries = _as_int_config(config.get("core_watchdog_retries"),
                                 WATCHDOG_MAX_RETRIES)
        self.watchdog_retries = max(WATCHDOG_RETRY_RANGE[0],
                                    min(WATCHDOG_RETRY_RANGE[1], retries))
        # 运行期记账（给界面看"又在重启"这件事）
        self.restart_count = 0
        self.watchdog_note = ""
        self._watchdog_failures = 0

    # ---- 订阅与只读视图 ---------------------------------------------------

    def subscribe(self, callback):
        """注册日志行回调。回调抛异常只记一行，不影响其它订阅者。"""
        with self.lock:
            self._subscribers.append(callback)

    def _publish(self, line):
        with self.lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(line)
            except Exception as exc:
                logging_setup.log("日志订阅者异常：%r" % (exc,), "WARN")

    def logs(self, count=400):
        with self.lock:
            if count and count > 0:
                return list(self._log_lines[-count:])
            return list(self._log_lines)

    def log_seq(self):
        with self.lock:
            return self._log_seq

    def status(self):
        """给 UI 的状态快照。

        `core_version` 在这里**每次都重新确定**：`/health` 报了版本就信它，
        否则读盘（带 mtime 缓存，不贵）。以前只在构造时读一次是错的——
        内核一更新，底栏、菜单、「检查更新」里的"当前版本"会一直报旧号，
        看起来像更新根本没装上去，于是用户去重启内核，它还说旧号
        （2026-10-14 实测：更新到 1.7.4 后界面仍说 1.7.3）。
        """
        with self.lock:
            health = dict(self.health or {})
            uptime = (time.time() - self.started_at) if self.started_at else 0.0
            version = (health.get("version")
                       or read_core_version(self.core_dir)
                       or self.core_version)
            if version and version != self.core_version:
                self.core_version = version
            return {
                "state": self.state,
                "state_label": STATE_LABELS.get(self.state, self.state),
                "detail": self.detail,
                "pid": self.pid,
                "port": self.port,
                "host": self.host,
                "uptime": uptime,
                "exit_code": self.exit_code,
                "core_dir": self.core_dir,
                "core_version": version,
                "accounts": health.get("accounts"),
                "accounts_ready": health.get("accounts_ready"),
                "realm": health.get("realm"),
                "api_key_required": health.get("api_key_required"),
                "used_job": self.used_job,
                "watchdog": self.watchdog,
                "watchdog_retries": self.watchdog_retries,
                "watchdog_note": self.watchdog_note,
                "restart_count": self.restart_count,
                "python": self.python.get("version") or "",
                "python_source": self.python.get("source") or "",
                "dashboard": "http://127.0.0.1:%d/" % self.port,
                "api_base": "http://127.0.0.1:%d/v1" % self.port,
                "log_file": self._log_file,
                "command": list(self.command),
            }

    # ---- 命令行 -----------------------------------------------------------

    def build_command(self):
        """拼内核命令行。只加显式给了值的参数，尽量贴近原始 .bat 行为。"""
        if not self.python.get("exe"):
            return []
        entry = os.path.join(self.core_dir, paths.CORE_ENTRY_NAME)
        exe = self.python["exe"]
        argv = [exe]
        if self.python.get("py_launcher"):
            argv.append("-3")
        argv.append(entry)
        argv += ["--port", str(self.port)]
        if self.lan:
            argv.append("--lan")
        elif self.host and self.host != "127.0.0.1":
            argv += ["--host", self.host]
        if self.accounts_dir:
            argv += ["--accounts-dir", self.accounts_dir]
        if self.usage_dir:
            argv += ["--usage-dir", self.usage_dir]
        if self.api_key:
            argv += ["--api-key", str(self.api_key)]
        if self.panel_password:
            argv += ["--panel-password", str(self.panel_password)]
        return argv

    def describe_command(self):
        """给日志看的命令行：密钥打码。"""
        argv = self.build_command()
        safe = []
        hide_next = False
        for index, item in enumerate(argv):
            if hide_next:
                safe.append("***")
                hide_next = False
                continue
            if index > 0 and argv[index - 1] in ("--api-key", "--panel-password"):
                safe.append("***")
                continue
            safe.append(item)
        return " ".join(safe)

    # ---- 启动 -------------------------------------------------------------

    def ensure_dirs(self):
        """数据目录可写性预检。返回 (ok, message)。"""
        for label, folder in (("账号目录", self.accounts_dir), ("用量目录", self.usage_dir)):
            if not folder:
                return False, "%s未确定" % label
            try:
                os.makedirs(folder, exist_ok=True)
                probe = os.path.join(folder, ".wb-shell-write-probe")
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(probe)
            except Exception as exc:
                return False, "%s不可写：%s（%s）" % (label, folder, exc)
        return True, ""

    def start(self):
        """启动内核。返回 (ok, message)。已在运行 / 启动失败都不抛异常。"""
        with self.lock:
            if self.state in (RUNNING, STARTING):
                return False, "内核实为 %s，未重复启动" % STATE_LABELS[self.state]
            if self.state == EXTERNAL:
                return False, "端口上已有外部启动的内核，未接管"

        existing = probe_health(self.port, self.host)
        if existing.get("state") == "ours":
            with self.lock:
                self.state = EXTERNAL
                self.health = existing
                self.detail = "端口 %d 上已有内核在跑（外部启动）" % self.port
            logging_setup.log("端口 %d 已有内核，进入伴随模式" % self.port, "WARN")
            return True, self.detail
        if existing.get("state") == "foreign":
            with self.lock:
                self.state = FAILED
                self.detail = existing.get("message") or "端口被占用"
            return False, self.detail

        ok, message = self.ensure_dirs()
        if not ok:
            with self.lock:
                self.state = FAILED
                self.detail = message
            return False, message

        argv = self.build_command()
        if not argv:
            with self.lock:
                self.state = FAILED
                self.detail = "没有可用的 Python 解释器"
            return False, self.detail
        with self.lock:
            self.command = argv
            self.state = STARTING
            self.detail = "正在启动内核…"
            self.exit_code = None
            # 新的一轮启动：把"上次是用户要求停的"这个标记清掉，否则下一次
            # 内核意外退出会被误判成"用户停的"而不触发守护。
            self._stop_requested = False

        self._open_log_file()
        self._append_log("$ " + self.describe_command())
        logging_setup.log("启动内核：%s" % self.describe_command())

        if self._job is None:
            self._job = proc.JobObject()
            if not self._job.available:
                logging_setup.log("Job Object 不可用：%s" % self._job.error, "WARN")

        try:
            spawned = proc.spawn(argv, self.core_dir, job=self._job)
        except Exception as exc:
            with self.lock:
                self.state = FAILED
                self.detail = "启动失败：%s" % exc
            logging_setup.log("启动失败：%s" % exc, "ERROR")
            return False, self.detail

        with self.lock:
            self._spawned = spawned
            self.pid = spawned.pid
            self.started_at = time.time()
            self.used_job = spawned.used_job
        # Job Object 是"外壳一死内核就被回收"的唯一保证，属于关键路径，
        # 成功与失败都要留痕——静默地降级成 taskkill 兜底是查不出来的。
        logging_setup.log(
            "Job Object：%s（pid=%s）"
            % ("已绑定，外壳退出时内核会被系统回收" if spawned.used_job
               else "未绑定，将依赖 taskkill 兜底", spawned.pid),
            "INFO" if spawned.used_job else "WARN")
        for note in spawned.notes:
            self._append_log("[shell] " + note)
            logging_setup.log(note, "WARN")

        self._reader = threading.Thread(target=self._read_loop,
                                       args=(spawned,), daemon=True,
                                       name="wb-core-log")
        self._reader.start()
        self._health_stop.clear()
        threading.Thread(target=self._health_loop, daemon=True,
                         name="wb-core-health").start()

        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            with self.lock:
                if self.state == FAILED:
                    return False, self.detail
            info = probe_health(self.port, self.host)
            if info.get("state") == "ours":
                with self.lock:
                    self.state = RUNNING
                    self.health = info
                    self.detail = "内核已就绪"
                logging_setup.log("内核已就绪：账号 %s 个（可用 %s），版本 %s"
                                  % (info.get("accounts"), info.get("accounts_ready"),
                                     read_core_version(self.core_dir) or "?"))
                return True, self.detail
            if info.get("state") == "foreign":
                with self.lock:
                    self.state = FAILED
                    self.detail = info.get("message") or "端口被其它程序占用"
                return False, self.detail
            if spawned.poll() is not None:
                with self.lock:
                    self.state = FAILED
                    self.exit_code = spawned.poll()
                    self.detail = ("内核启动后立即退出（退出码 %s），"
                                   "请看下方日志" % self.exit_code)
                return False, self.detail
            time.sleep(POLL_INTERVAL)

        with self.lock:
            self.detail = "启动超时（%d 秒内未探通 /health），内核可能仍在初始化" % START_TIMEOUT
        logging_setup.log(self.detail, "WARN")
        return False, self.detail

    # ---- 停止 -------------------------------------------------------------

    def stop(self):
        """停止内核。三级降级，结束后复验端口释放。返回 (ok, message)。"""
        with self.lock:
            if self.state == EXTERNAL:
                return False, "内核是外部启动的，本外壳不负责停止"
            if self.state == STOPPED:
                return True, "内核本来就已停止"
            spawned = self._spawned
            # 先把"这次退出是我们要求的"钉死：`_read_loop` / 守护线程靠它
            # 区分"用户停的"和"内核自己崩了"，否则手动停止会被当成崩溃而拉起。
            self._stop_requested = True
            self.state = STOPPING
            self.detail = "正在停止内核…"
        self._health_stop.set()

        if spawned is None:
            with self.lock:
                self.state = STOPPED
                self.detail = ""
            return True, "没有正在运行的进程"

        logging_setup.log("停止内核 pid=%s" % spawned.pid)
        spawned.terminate(exit_code=0)
        code = spawned.wait(timeout=STOP_GRACE)
        used = "TerminateProcess"
        if code is None:
            logging_setup.log("TerminateProcess 后 %d 秒仍未退出，改用 taskkill /T /F"
                              % STOP_GRACE, "WARN")
            spawned.kill_tree()
            code = spawned.wait(timeout=8.0)
            used = "taskkill /T /F"
        if code is None and self._job is not None and self._job.available:
            logging_setup.log("taskkill 仍未生效，改用 Job Object 整体终止", "WARN")
            self._job.terminate_all()
            code = spawned.wait(timeout=5.0)
            used = "Job Object"

        with self.lock:
            self.exit_code = code
            self.pid = 0
            self.stopped_at = time.time()
            self.started_at = 0.0
            self.health = {}
            self._spawned = None
        spawned.close_handles()

        released, message = self.wait_port_released(timeout=10.0)
        with self.lock:
            if code is None:
                self.state = FAILED
                self.detail = "停止失败：进程仍未退出，端口%s" % ("已释放" if released else "仍被占用")
            else:
                self.state = STOPPED
                self.detail = "已停止（%s）" % used if released else "已停止，但端口仍被占用"
        if not released:
            logging_setup.log("停止后端口 %d 仍被占用：%s" % (self.port, message), "WARN")
        logging_setup.log("内核已停止：%s" % self.detail)
        return released, self.detail

    def restart(self):
        ok, message = self.stop()
        if not ok:
            return False, "重启前的停止未成功：%s" % message
        time.sleep(0.4)
        return self.start()

    def wait_port_released(self, timeout=10.0):
        """等端口真的不再响应 /health。返回 (released, message)。"""
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            info = probe_health(self.port, self.host, timeout=1.0)
            if info.get("state") in ("down", "error"):
                return True, ""
            last = info.get("message") or info.get("state") or ""
            time.sleep(0.4)
        return False, last

    def shutdown(self):
        """退出路径统一走这里：停内核 + 释放 Job + 归档日志文件。"""
        try:
            self.stop()
        except Exception as exc:
            logging_setup.log("shutdown 停止内核异常：%r" % (exc,), "ERROR")
        with self.lock:
            job = self._job
            self._job = None
            log_file = self._log_file
            self._log_file = None
        if job is not None:
            job.close()
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        logging_setup.log("外壳已退出")

    # ---- 日志 -------------------------------------------------------------

    def _log_path(self):
        return os.path.join(paths.data_dir(), "core-%d.log" % self.port)

    def _rotate_core_log(self):
        """内核日志按大小滚动：core-8788.log → .1 → .2 → .3（保留 3 份）。

        外壳自己的 `data\\shell.log` 一直有轮转（logging_setup._rotate），但内核
        stdout 落盘这条路没有——只增不减。生产里已经涨到 1.5 MB 且每天都在涨，
        长期会把盘吃掉；而这份日志正是排障时要看的东西，不能任它无限增长。
        调用方已持锁；任何异常都吞掉（日志写不进去不能影响内核）。
        """
        with self.lock:
            handle = self._log_file
            path = self._log_path()
        if handle is None:
            return
        try:
            if os.path.getsize(path) < CORE_LOG_MAX_BYTES:
                return
        except OSError:
            return
        try:
            handle.close()
        except Exception:
            pass
        try:
            oldest = "%s.%d" % (path, CORE_LOG_KEEP)
            if os.path.isfile(oldest):
                os.remove(oldest)
            for index in range(CORE_LOG_KEEP - 1, 0, -1):
                source = "%s.%d" % (path, index)
                if os.path.isfile(source):
                    os.replace(source, "%s.%d" % (path, index + 1))
            os.replace(path, path + ".1")
            logging_setup.log("内核日志已轮转：%s（超过 %.0f MB）"
                              % (os.path.basename(path),
                                 CORE_LOG_MAX_BYTES / 1048576.0))
        except Exception as exc:
            try:
                logging_setup.log("内核日志轮转失败：%r" % (exc,), "WARN")
            except Exception:
                pass
        with self.lock:
            try:
                self._log_file = open(path, "a", encoding="utf-8",
                                      errors="replace", newline="\n")
            except Exception:
                self._log_file = None

    def _open_log_file(self):
        with self.lock:
            if self._log_file is not None:
                return
            try:
                path = self._log_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._log_file = open(path, "a", encoding="utf-8",
                                      errors="replace", newline="\n")
                self._log_file.write("\n===== %s  外壳启动内核 (pid 待定) =====\n"
                                     % time.strftime("%Y-%m-%d %H:%M:%S"))
                self._log_file.flush()
            except Exception as exc:
                logging_setup.log("内核日志文件打开失败：%r" % (exc,), "WARN")
                self._log_file = None

    def _append_log(self, line):
        text = line.rstrip("\r\n")
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self._log_seq += 1
            self._log_lines.append("%s %s" % (stamp, text))
            if len(self._log_lines) > LOG_BUFFER_LINES:
                del self._log_lines[:len(self._log_lines) - LOG_BUFFER_LINES]
            handle = self._log_file
            self._log_size += len(text) + 32
            need_rotate = self._log_size >= CORE_LOG_MAX_BYTES
        if handle is not None and need_rotate:
            self._rotate_core_log()
            with self.lock:
                self._log_size = 0
                handle = self._log_file
        if handle is not None:
            try:
                handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
                handle.flush()
            except Exception:
                pass
        self._publish("%s %s" % (stamp, text))

    def _read_loop(self, spawned):
        stream = spawned.file
        try:
            for raw in stream:
                self._append_log(raw)
        except Exception as exc:
            self._append_log("[shell] 日志读取结束：%r" % (exc,))
        code = spawned.poll()
        if code is None:
            try:
                code = spawned.wait(timeout=3.0)
            except Exception:
                code = None
        self._append_log("[shell] 内核输出流已关闭（退出码 %s）" % code)
        with self.lock:
            crashed = False
            if self.state in (RUNNING, STARTING) and not self._stop_requested:
                # 用户没要求停、状态还是"运行中/启动中" → 内核是自己没的。
                # 这条路以前只改状态、界面上什么都不会发生（图标默默变灰），
                # 用户要等到客户端报错才知道 8788 断了。
                crashed = True
            if self.state in (RUNNING, STARTING, STOPPING):
                self.exit_code = code
                self.state = STOPPED if code == 0 else FAILED
                self.detail = ("内核已退出（退出码 %s）" % code)
                self.pid = 0
                self.health = {}
                self.started_at = 0.0
        if crashed:
            self._on_core_crashed(code)

    def _on_core_crashed(self, code):
        """内核意外退出：先喊人，再按退避自动拉起。

        这里刻意**不**在 `_read_loop` 里同步调用 `start()`：那个线程还握着
        管道读端的收尾逻辑，而且 start() 会等最多 30 秒。交给独立的守护线程。

        "喊人"通过 `_publish` 走日志订阅者（界面/CLI 都在听），另外写一行
        外壳日志——托盘气泡由上层（tray）根据状态变化自己弹，避免重复。
        """
        logging_setup.log("内核意外退出（退出码 %s），进入自动恢复流程" % code,
                          "ERROR")
        self._publish("[shell] 内核意外退出（退出码 %s）" % code)
        if not self.watchdog:
            self._publish("[shell] 内核守护已关闭：不自动重启，请从托盘手动「启动内核」")
            return
        if self._watchdog_failures >= self.watchdog_retries:
            self.watchdog_note = ("已自动重启 %d 次仍未成功，停止自动恢复"
                                  "（需要人工介入：请检查内核目录/端口后手动启动）"
                                  % self._watchdog_failures)
            logging_setup.log(self.watchdog_note, "ERROR")
            self._publish("[shell] " + self.watchdog_note)
            return
        delay = WATCHDOG_BACKOFF[min(self._watchdog_failures,
                                     len(WATCHDOG_BACKOFF) - 1)]
        self._publish("[shell] 内核守护：%d 秒后自动重启（第 %d/%d 次）"
                      % (delay, self._watchdog_failures + 1, self.watchdog_retries))
        threading.Thread(target=self._watchdog_restart, args=(delay,),
                         daemon=True, name="wb-core-watchdog").start()

    def _watchdog_restart(self, delay):
        """等一会儿再拉起内核；期间如果用户手动操作了（停止/退出）就放弃。"""
        if self._watchdog_stop.wait(delay):
            return
        with self.lock:
            if self.state not in (STOPPED, FAILED):
                return          # 用户已经手动启起来了，别再插一脚
            if self._stop_requested:
                return          # 期间被要求停过
        logging_setup.log("内核守护：自动重启内核（第 %d 次）"
                          % (self._watchdog_failures + 1))
        ok, message = self.start()
        with self.lock:
            if ok:
                self.restart_count += 1
                self._watchdog_failures = 0
                self.watchdog_note = ""
            else:
                self._watchdog_failures += 1
                self.watchdog_note = "自动重启失败（%s）" % message
        if ok:
            logging_setup.log("内核守护：内核已恢复（累计自动重启 %d 次）"
                              % self.restart_count)
            self._publish("[shell] 内核守护：内核已恢复")
        else:
            logging_setup.log("内核守护：自动重启失败：%s" % message, "ERROR")
            self._publish("[shell] 内核守护：自动重启失败：%s" % message)
            # 失败也算一次"意外退出"→ 继续退避重试，直到用完配额
            self._on_core_crashed(None)

    def _health_loop(self):
        """运行期间每秒刷新一次 /health，供状态栏显示账号数/区域。"""
        while not self._health_stop.wait(POLL_INTERVAL):
            with self.lock:
                if self.state not in (RUNNING, EXTERNAL):
                    return
                port, host = self.port, self.host
            info = probe_health(port, host, timeout=1.5)
            with self.lock:
                if info.get("state") == "ours":
                    self.health = info
                    if self.state == EXTERNAL:
                        pass
                elif self.state == RUNNING:
                    self.detail = "内核在运行，但 /health 暂时探不通"
