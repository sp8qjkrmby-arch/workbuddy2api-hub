# -*- coding: utf-8 -*-
"""proc.py — Windows 原生进程创建：让内核绝不会变成残留进程。

为什么不用 subprocess：
    要根除「外壳被强杀 → 内核变孤儿继续占端口」这个故障，唯一可靠的手段是
    Windows 的 **Job Object + KILL_ON_JOB_CLOSE**。而要把进程放进 Job，
    它必须在没有被调度前就被指派——也就是 CREATE_SUSPENDED + AssignProcess
    到 Job 之后才 Resume。subprocess 不给这个窗口（Python 3.7+ 起
    STARTUPINFOEX 也不在标准库里暴露）。

所以这里直接调 CreateProcessW，并且把 stdout 接进一根匿名管道，
既拿到「无黑窗」（CREATE_NO_WINDOW）又拿到「日志可捕获」。

非 Windows 平台（仅便于开发期跑测试）自动退回 subprocess，功能等价但
没有 Job Object 兜底。
"""

import os
import sys
import threading
import time

IS_WINDOWS = os.name == "nt"

# --- Win32 常量 --------------------------------------------------------------

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259

HANDLE_FLAG_INHERIT = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002

JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800


class ProcessSpawnError(RuntimeError):
    """CreateProcessW 或管道准备失败。消息里带可读的 Win32 说明。"""


def format_winerror(code):
    try:
        import ctypes
        return "%s (错误码 %d)" % (ctypes.FormatError(code).strip(), code)
    except Exception:
        return "错误码 %d" % code


# --- Job Object --------------------------------------------------------------

class JobObject(object):
    """一个「关掉就全杀」的进程组。

    外壳进程一旦消失（正常退出、崩溃、被任务管理器强杀），最后一个 Job 句柄
    自动关闭，内核被系统回收。不靠轮询、不靠父进程存活检测，因此没有假阳性。
    """

    def __init__(self, name="wb-shell"):
        self.handle = None
        self.name = name
        self.error = ""
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                            ("WriteOperationCount", ctypes.c_ulonglong),
                            ("OtherOperationCount", ctypes.c_ulonglong),
                            ("ReadTransferCount", ctypes.c_ulonglong),
                            ("WriteTransferCount", ctypes.c_ulonglong),
                            ("OtherTransferCount", ctypes.c_ulonglong)]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                            ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                self.error = format_winerror(ctypes.get_last_error())
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK)
            ok = k32.SetInformationJobObject(
                handle, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                self.error = format_winerror(ctypes.get_last_error())
                k32.CloseHandle(handle)
                return
            self.handle = handle
            self._k32 = k32
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)

    @property
    def available(self):
        return self.handle is not None

    def assign(self, process_handle):
        """把已创建但尚未运行的进程放进来。返回 (ok, message)。"""
        if not self.available:
            return False, self.error or "Job Object 不可用"
        try:
            import ctypes
            from ctypes import wintypes
            self._k32.AssignProcessToJobObject.restype = wintypes.BOOL
            self._k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,
                                                           wintypes.HANDLE]
            if not self._k32.AssignProcessToJobObject(self.handle, process_handle):
                code = ctypes.get_last_error()
                return False, format_winerror(code)
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)
        return True, ""

    def terminate_all(self, exit_code=0):
        """杀光组内所有进程。外壳主动关停时用（比逐个 terminate 快且干净）。"""
        if not self.available:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            self._k32.TerminateJobObject.restype = wintypes.BOOL
            self._k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self._k32.TerminateJobObject(self.handle, exit_code)
            return True
        except Exception:
            return False

    def close(self):
        if self.handle is not None:
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


# --- 匿名管道 ----------------------------------------------------------------

def _make_pipe():
    """可被子进程继承的匿名管道，返回 (read_fd, write_handle)。"""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    k32.CreatePipe.restype = wintypes.BOOL
    k32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                               ctypes.POINTER(wintypes.HANDLE),
                               ctypes.POINTER(SECURITY_ATTRIBUTES),
                               wintypes.DWORD]
    k32.SetHandleInformation.restype = wintypes.BOOL
    k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        wintypes.DWORD]

    attrs = SECURITY_ATTRIBUTES()
    attrs.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    attrs.bInheritHandle = True
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    if not k32.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle),
                          ctypes.byref(attrs), 0):
        raise ProcessSpawnError("CreatePipe 失败：" +
                                format_winerror(ctypes.get_last_error()))
    # 读端留给自己，不能被子进程继承（否则管道永不关闭）。
    k32.SetHandleInformation(read_handle, HANDLE_FLAG_INHERIT, 0)
    read_fd = msvcrt.open_osfhandle(read_handle.value, os.O_RDONLY | os.O_BINARY)
    return read_fd, write_handle.value


# --- 启动 --------------------------------------------------------------------

class Spawned(object):
    """一次创建的结果：进程句柄、管道读端、Job。"""

    def __init__(self):
        self.pid = 0
        self.process_handle = None
        self.thread_handle = None
        self.file = None          # 文本模式的 stdout 读取对象
        self.job = None
        self.used_job = False
        self.notes = []           # 给日志用的一句句说明
        self._closed = False
        self._k32 = None

    # 进程状态
    def poll(self):
        """返回退出码；仍在运行返回 None。"""
        if not IS_WINDOWS:
            return self._proc.poll() if getattr(self, "_proc", None) else None
        if self.process_handle is None:
            return None
        import ctypes
        from ctypes import wintypes
        k32 = self._k32
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(self.process_handle, ctypes.byref(code)):
            return None
        if code.value == STILL_ACTIVE:
            return None
        return int(code.value)

    def wait(self, timeout=None):
        """等进程结束，返回退出码；超时返回 None。"""
        if not IS_WINDOWS:
            try:
                return self._proc.wait(timeout=timeout)
            except Exception:
                return None
        import ctypes
        millis = INFINITE if timeout is None else max(0, int(timeout * 1000))
        self._k32.WaitForSingleObject(self.process_handle, millis)
        return self.poll()

    def terminate(self, exit_code=1):
        """TerminateProcess。"""
        if not IS_WINDOWS:
            try:
                self._proc.terminate()
                return True
            except Exception:
                return False
        if self.process_handle is None:
            return False
        return bool(self._k32.TerminateProcess(self.process_handle, exit_code))

    def kill_tree(self):
        """最后手段：taskkill /T /F（连子进程一起）。"""
        if not IS_WINDOWS or not self.pid:
            return False
        import subprocess
        flags = CREATE_NO_WINDOW
        try:
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=flags,
                           timeout=15)
            return True
        except Exception:
            return False

    def close_handles(self):
        if self._closed:
            return
        self._closed = True
        if IS_WINDOWS and self._k32 is not None:
            for handle in (self.thread_handle, self.process_handle):
                if handle:
                    try:
                        self._k32.CloseHandle(handle)
                    except Exception:
                        pass
            self.thread_handle = None
            self.process_handle = None
        if self.file is not None:
            try:
                self.file.close()
            except Exception:
                pass
            self.file = None


def spawn(argv, cwd, job=None, extra_env=None, encoding="utf-8"):
    """无黑窗启动一个子进程，stdout+stderr 合成一根管道。

    返回 Spawned。失败抛 ProcessSpawnError（消息可直接展示给用户）。
    优先走 CREATE_SUSPENDED + AssignProcessToJobObject，失败则退回
    subprocess（功能一样，只是少了进程组兜底），并把原因记在 notes 里。
    """
    if not argv:
        raise ProcessSpawnError("启动命令为空")
    if not IS_WINDOWS:
        return _spawn_subprocess(argv, cwd, extra_env, encoding)

    import ctypes
    from ctypes import wintypes

    notes = []
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD),
                    ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD),
                    ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD),
                    ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE),
                    ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]

    k32.CreateProcessW.restype = wintypes.BOOL
    k32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.ResumeThread.restype = wintypes.DWORD
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    read_fd, write_handle = _make_pipe()
    read_file = os.fdopen(read_fd, "r", encoding=encoding, errors="replace",
                          buffering=1, newline=None)

    devnull = None
    try:
        devnull = open(os.devnull, "rb")
        stdin_handle = _crt_handle(devnull.fileno())
    except Exception:
        stdin_handle = None

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdInput = stdin_handle or 0
    startup.hStdOutput = write_handle
    startup.hStdError = write_handle

    info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(_command_line(argv))
    flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT

    # 环境块走继承（lpEnvironment=None）：子进程拿到与本外壳完全一致的环境，
    # 与用户当前双击 .bat 的行为一致。需要改写环境时再加显式块，并确保
    # 缓冲区在本调用期间始终被引用。
    created = k32.CreateProcessW(None, command_line, None, None, True, flags,
                                 None, cwd, ctypes.byref(startup),
                                 ctypes.byref(info))
    if not created:
        code = ctypes.get_last_error()
        _close_quiet(k32, write_handle)
        if devnull is not None:
            devnull.close()
        read_file.close()
        raise ProcessSpawnError(
            "CreateProcessW 失败：%s\n命令：%s\n工作目录：%s"
            % (format_winerror(code), " ".join(argv), cwd))

    _close_quiet(k32, write_handle)
    if devnull is not None:
        devnull.close()

    spawned = Spawned()
    spawned.pid = int(info.dwProcessId)
    spawned.process_handle = info.hProcess
    spawned.thread_handle = info.hThread
    spawned.file = read_file
    spawned._k32 = k32
    spawned.job = job

    job_ok = False
    if job is not None and job.available:
        job_ok, job_message = job.assign(info.hProcess)
        if not job_ok:
            notes.append("未能加入 Job Object：%s（将改用 taskkill 兜底）" % job_message)
    elif job is not None and job.error:
        notes.append("Job Object 不可用：%s（将改用 taskkill 兜底）" % job.error)
    spawned.used_job = job_ok

    if k32.ResumeThread(info.hThread) == 0xFFFFFFFF:
        notes.append("ResumeThread 失败，进程可能未开始运行")
    spawned.notes = notes
    return spawned


def _spawn_subprocess(argv, cwd, extra_env, encoding):
    import subprocess
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=CREATE_NO_WINDOW, close_fds=True,
                            bufsize=1, text=True, encoding=encoding,
                            errors="replace")
    spawned = Spawned()
    spawned.pid = proc.pid
    spawned.file = proc.stdout
    spawned._proc = proc
    spawned.notes = ["非 Windows 平台：使用 subprocess（无 Job Object 兜底）"]
    return spawned


def _close_quiet(k32, handle):
    if handle:
        try:
            k32.CloseHandle(handle)
        except Exception:
            pass


def _crt_handle(fd):
    """把 CRT 文件描述符转成可继承的 Win32 句柄。"""
    import msvcrt
    handle = msvcrt.get_osfhandle(fd)
    return handle


def _command_line(argv):
    """按 Windows argv 规则拼命令行。

    规则来自 CreateProcessW 的解析约定：有空白或引号就用双引号包住，
    内部的引号按 2n+1 个反斜杠转义。
    """
    parts = []
    for item in argv:
        text = str(item)
        if text and not any(ch in text for ch in ' \t\n\v"'):
            parts.append(text)
            continue
        result = ['"']
        backslashes = 0
        for ch in text:
            if ch == "\\":
                backslashes += 1
                continue
            if ch == '"':
                result.append("\\" * (backslashes * 2 + 1))
                result.append('"')
            else:
                result.append("\\" * backslashes)
                result.append(ch)
            backslashes = 0
        result.append("\\" * (backslashes * 2))
        result.append('"')
        parts.append("".join(result))
    return " ".join(parts)


def _environment_block(extra_env):
    """构造 CREATE_UNICODE_ENVIRONMENT 要的 UTF-16 环境块。"""
    import ctypes
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in extra_env.items()})
    # 键排序不必要，但必须大写去重（Windows 环境变量大小写不敏感）。
    seen = {}
    for key, value in env.items():
        seen[key.upper()] = (key, value)
    chunks = []
    for upper in sorted(seen):
        key, value = seen[upper]
        chunks.append("%s=%s" % (key, value))
    text = "\0".join(chunks) + "\0\0"
    buffer = ctypes.create_unicode_buffer(text)
    return ctypes.cast(buffer, ctypes.c_void_p), buffer
