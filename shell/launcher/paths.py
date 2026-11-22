# -*- coding: utf-8 -*-
"""paths.py — 目录布局与 Python 解释器发现。

这个模块只回答两个问题：

1. 外壳自己的目录、数据目录、内核目录分别在哪；
2. 用哪个 Python 解释器跑内核。

它**不读配置**（那是 config.py 的事），但会提供 config 需要落盘的位置。
纯标准库，目标 Python 3.9+（外壳主路径是 3.14，兜底路径是 3.12，两者都要能跑）。
"""

import json
import os
import sys

# --- 固定名字 ---------------------------------------------------------------

BOOTSTRAP_NAME = "bootstrap.json"
DATA_DIR_NAME = "data"
SHELL_LOG_NAME = "shell.log"
CORE_ENTRY_NAME = "wb_proxy.py"
CORE_DASHBOARD_NAME = "dashboard.html"
MUTEX_PREFIX = "Global\\wbshell-"
# 单实例互斥体的名字（同一台机器上按"外壳目录"区分：不同的外壳目录可以各跑各的，
# 比如生产的 wb-shell 和工作区的 2api 副本；同一个目录只允许一个）。
MUTEX_BASE = "wb-shell-single-instance"
# 已有实例在跑时，用来"把它叫出来"的命名事件。
WAKEUP_EVENT_NAME = "Local\\wbshell-show-window-"

# 启动引导文件名：唯一允许放在外壳根目录的配置文件。
# 它存在的意义是「换机器 / 换位置也不用改代码」，而不是承载全部设置——
# 全部设置住在 data\shell.json。
BOOTSTRAP_KEYS = ("core_dir", "port", "ui", "tray_enabled")


def _frozen_dir():
    """打包成 exe 时的目录。

    第 2 阶段会用到（build_shell.ps1 生成的 GUI 启动器）；现在返回 None，
    让调用方走源码路径，避免引入未验证的分支。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return None


def shell_dir():
    """外壳根目录（含 launcher\\、data\\、vendor\\）。

    源码运行时是 `<shell>\\launcher\\paths.py` → 上溯一级。
    """
    frozen = _frozen_dir()
    if frozen:
        return frozen
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def launcher_dir():
    return os.path.join(shell_dir(), "launcher")


def vendor_dir():
    return os.path.join(launcher_dir(), "vendor")


def data_dir():
    return os.path.join(shell_dir(), DATA_DIR_NAME)


def bootstrap_path():
    return os.path.join(shell_dir(), BOOTSTRAP_NAME)


def read_bootstrap():
    """读根目录 bootstrap.json；不存在 / 坏了都返回 {}（不抛异常）。

    故意宽容：引导文件出问题时用户还得能用 `--core-dir` 把外壳救回来。
    """
    try:
        with open(bootstrap_path(), "r", encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def find_sibling_core(shell_path):
    """在内核可能待的地方找它。

    认三个位置，按"越贴身越优先"排序：

    1. **外壳自己的子目录**（如 `wb-shell\\wb-proxy\\`）——把内核搬进外壳里是最
       自然的整理方式，早期版本漏了这一处，导致搬进去之后外壳报"没找到内核目录"；
    2. 外壳的**兄弟目录**（`C:\\Desktop\\` 下与外壳并列的 `wb-proxy\\`）；
    3. 外壳所在目录的**上级**的子目录（多一层容错）。

    判定信号：目录里有 `wb_proxy.py`，且像内核（有 `dashboard.html` 或 `python\\`）。
    只看 `wb_proxy.py` 太松——克隆下来的仓库根目录也满足。
    """
    shell_path = os.path.abspath(shell_path)
    parent = os.path.dirname(shell_path) or os.path.abspath(os.curdir)
    grandparent = os.path.dirname(parent) or ""

    def scan(base, skip_self=False):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return ""
        for name in names:
            if name.startswith("."):
                continue
            candidate = os.path.join(base, name)
            if skip_self and os.path.abspath(candidate) == shell_path:
                continue
            if is_core_dir(candidate):
                return candidate
        return ""

    for base, skip_self in ((shell_path, False),      # ① 外壳的子目录
                            (parent, True),            # ② 外壳的兄弟目录
                            (grandparent, True)):      # ③ 上一层的兄弟目录
        if not base:
            continue
        found = scan(base, skip_self)
        if found:
            return found
    return ""


def is_core_dir(path):
    """这个目录能不能当内核目录用。"""
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, CORE_ENTRY_NAME)):
        return False
    return (os.path.isfile(os.path.join(path, CORE_DASHBOARD_NAME))
            or os.path.isdir(os.path.join(path, "python")))


def describe_core_problem(path):
    """内核目录不可用时，给人看的一句话。"""
    if not path:
        return "尚未指定内核目录"
    if not os.path.isdir(path):
        return "目录不存在：%s" % path
    if not os.path.isfile(os.path.join(path, CORE_ENTRY_NAME)):
        return "目录里没有 %s：%s" % (CORE_ENTRY_NAME, path)
    if not os.path.isfile(os.path.join(path, CORE_DASHBOARD_NAME)):
        return "目录里没有 %s（内核不完整）：%s" % (CORE_DASHBOARD_NAME, path)
    return ""


def resolve_core_dir(explicit="", shell_path=None):
    """按优先级定出内核目录，返回 (路径, 来源说明)。

    显式参数 → bootstrap.json → 外壳同级的兄弟目录。
    都不成立时返回空串，由调用方报错并让用户 `--core-dir` 指定。
    """
    if explicit:
        return os.path.abspath(explicit), "命令行 --core-dir"

    boot = read_bootstrap()
    from_boot = str(boot.get("core_dir") or "").strip()
    if from_boot:
        return os.path.abspath(from_boot), "%s" % BOOTSTRAP_NAME

    shell_path = shell_path or shell_dir()
    sibling = find_sibling_core(shell_path)
    if sibling:
        return sibling, "外壳同级目录自动发现"
    return "", "未找到"


# --- 解释器发现 -------------------------------------------------------------

def core_bundled_python(core_path):
    """内核便携包自带的解释器（官方包里的 python\\python.exe）。"""
    if not core_path:
        return ""
    candidate = os.path.join(core_path, "python", "python.exe")
    return candidate if os.path.isfile(candidate) else ""


def shell_bundled_python(shell_path=None):
    """外壳自带运行时的解释器（仅当机器上没有可用 Python 时才需要）。"""
    shell_path = shell_path or shell_dir()
    candidate = os.path.join(shell_path, "runtime", "python.exe")
    return candidate if os.path.isfile(candidate) else ""


def _python_from_registry():
    """从注册表里找系统 Python（64 位优先，版本号高的优先）。

    只读 HKCU / HKLM 的 PythonCore 分支——这是官方安装器写的标准位置，
    不猜路径、不遍历磁盘。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    found = []
    for root, view in ((winreg.HKEY_CURRENT_USER, 0),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(root, r"Software\Python\PythonCore", 0,
                                winreg.KEY_READ | view) as key:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(count):
                    try:
                        version = winreg.EnumKey(key, index)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(key, version + r"\InstallPath", 0,
                                            winreg.KEY_READ | view) as install:
                            path, _ = winreg.QueryValueEx(install, "")
                    except OSError:
                        continue
                    exe = os.path.join(str(path), "python.exe")
                    if os.path.isfile(exe):
                        found.append((_version_key(version), exe))
        except OSError:
            continue
    if not found:
        return ""
    found.sort(reverse=True)
    return found[0][1]


def _version_key(text):
    parts = []
    for chunk in str(text).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _python_from_local_appdata():
    """python.org 的 per-user 安装（`C:\\Users\\<u>\\AppData\\Local\\Python\\...`）。

    没有注册表的绿色/极简安装常落在这里，命名形如 `pythoncore-3.14-64`。
    """
    base = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Python")
    if not os.path.isdir(base):
        return ""
    best = None
    try:
        names = os.listdir(base)
    except OSError:
        return ""
    for name in sorted(names):
        exe = os.path.join(base, name, "python.exe")
        if not os.path.isfile(exe):
            continue
        key = _version_key(name.replace("pythoncore-", "").replace("-64", "")
                           .replace("-32", "").replace("-arm64", ""))
        if best is None or key > best[0]:
            best = (key, exe)
    return best[1] if best else ""


def _probe(exe, args=("--version",), timeout=8):
    """解释器能不能真的跑起来。返回版本串或空串。

    只看文件存在是不够的：残缺的运行时、被安全软件拦下的 exe 都会在
    这里露出来，总比启动内核时才炸要好。
    """
    if not exe:
        return ""
    import subprocess
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [exe] + list(args), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=flags, close_fds=True,
        )
        out, _ = proc.communicate(timeout=timeout)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    text = (out or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0] if text else "unknown"


def find_python(core_path="", runtime_dir="", shell_path=None):
    """定出跑内核用的解释器。

    返回 dict：exe / source / version / windowed / warnings。
    `windowed` 为 True 表示能无黑窗跑 GUI（同目录存在 pythonw.exe）。
    """
    warnings = []
    candidates = []
    if runtime_dir:
        candidates.append((os.path.join(runtime_dir, "python.exe"),
                           "设置里的 runtime_dir"))
    candidates.append((shell_bundled_python(shell_path), "外壳自带 runtime"))
    candidates.append((core_bundled_python(core_path), "内核便携包自带 python"))
    candidates.append((_python_from_registry(), "注册表里的系统 Python"))
    candidates.append((_python_from_local_appdata(), "本机 per-user 安装的 Python"))
    for name in ("python", "py"):
        found = _which(name)
        if found:
            candidates.append((found, "PATH 上的 %s" % name))

    seen = set()
    for exe, source in candidates:
        if not exe:
            continue
        key = os.path.normcase(os.path.abspath(exe))
        if key in seen:
            continue
        seen.add(key)
        args = ("-3", "--version") if os.path.basename(exe).lower() == "py.exe" else ("--version",)
        version = _probe(exe, args)
        if not version:
            warnings.append("%s 无法运行，已跳过（%s）" % (source, exe))
            continue
        windowed = os.path.isfile(os.path.join(os.path.dirname(exe), "pythonw.exe"))
        if not windowed and os.name == "nt":
            warnings.append("%s 旁边没有 pythonw.exe，GUI 窗口可能带黑框" % source)
        return {
            "exe": exe,
            "source": source,
            "version": version,
            "windowed": windowed,
            "py_launcher": os.path.basename(exe).lower() == "py.exe",
            "warnings": warnings,
        }
    return {"exe": "", "source": "", "version": "", "windowed": False,
            "py_launcher": False, "warnings": warnings}


def _which(name):
    for ext in (".exe", ".cmd", ".bat", ""):
        found = _which_one(name + ext)
        if found:
            return found
    return ""


def _which_one(name):
    path_env = os.environ.get("PATH") or ""
    for folder in path_env.split(os.pathsep):
        folder = folder.strip('"')
        if not folder:
            continue
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def candidate_core_dirs(shell_path):
    """列出所有"可能的内核目录"候选，供面板展示与诊断。

    顺序就是自动发现的优先序：外壳子目录 → 外壳兄弟目录 → 上一层兄弟目录。
    返回 [{path, valid, note}]，valid 表示这个目录现在能不能当内核用。
    """
    shell_path = os.path.abspath(shell_path)
    parent = os.path.dirname(shell_path) or os.path.abspath(os.curdir)
    grandparent = os.path.dirname(parent) or ""

    seen = []
    found = []
    for base, label in ((shell_path, "外壳的子目录"),
                        (parent, "外壳的兄弟目录"),
                        (grandparent, "上一层的兄弟目录")):
        if not base:
            continue
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            if name.startswith("."):
                continue
            candidate = os.path.abspath(os.path.join(base, name))
            if candidate == shell_path or candidate in seen:
                continue
            seen.append(candidate)
            valid = is_core_dir(candidate)
            if valid or os.path.isfile(os.path.join(candidate, CORE_ENTRY_NAME)):
                found.append({"path": candidate, "valid": valid, "note": label})
    return found


def write_bootstrap(core_dir, shell_path=None):
    """把内核位置写进 bootstrap.json（保留文件里其它键）。返回 (ok, message)。"""
    shell_path = shell_path or shell_dir()
    path = bootstrap_path()
    data = read_bootstrap()
    if not isinstance(data, dict):
        data = {}
    data["core_dir"] = os.path.abspath(core_dir)
    try:
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        return True, path
    except Exception as exc:
        return False, "写入 %s 失败：%s" % (BOOTSTRAP_NAME, exc)


def describe_python(info):
    if not info.get("exe"):
        return "未找到可用 Python"
    return "%s（%s，%s）" % (info.get("version") or "?", info.get("source") or "?",
                             "可无窗口运行" if info.get("windowed") else "无 pythonw.exe")


# --- 单实例 -----------------------------------------------------------------
#
# 为什么需要它：`--tray-only`（开机自启）和用户手动双击 会各起一个外壳。
# 第二个外壳发现端口上已有内核，就进入"伴随模式"继续挂着——用户得到的是
# **两个托盘图标、两个进程**，而点哪个都像是"没反应"（2026-10-17 生产日志里
# 就有一次 `端口 8788 已有内核，进入伴随模式`）。
#
# 做法：按"外壳目录"命名一个互斥体；拿不到就说明同目录已有实例在跑，于是
# **通知那个实例把窗口叫出来**，自己安静退出。不同目录（生产 / 工作区副本）
# 名字不同，互不影响。

_MUTEX_HANDLE = None


def _mutex_name(shell_path):
    """按外壳目录生成互斥体名字（同一台机器上不同目录各自独立）。"""
    import hashlib
    digest = hashlib.sha256(
        os.path.normcase(os.path.abspath(shell_path)).encode("utf-8")).hexdigest()
    return "%s%s-%s" % (MUTEX_PREFIX, MUTEX_BASE, digest[:16])


def _wakeup_event_name(shell_path):
    import hashlib
    digest = hashlib.sha256(
        os.path.normcase(os.path.abspath(shell_path)).encode("utf-8")).hexdigest()
    return "%s%s" % (WAKEUP_EVENT_NAME, digest[:16])


def acquire_single_instance(shell_path=None):
    """抢占"本外壳目录只允许一个实例"的锁。

    返回 dict：acquired / first(bool，是不是第一个) / name / reason。
    **拿不到锁不算错误**——调用方应当把它当成"已经有一个在跑"来处理。

    非 Windows / ctypes 不可用时返回 acquired=True 且 first=True（绝不让
    "检测不了"变成"起不来"）。
    """
    global _MUTEX_HANDLE
    shell_path = shell_path or shell_dir()
    name = _mutex_name(shell_path)
    if os.name != "nt":
        return {"acquired": True, "first": True, "name": name,
                "reason": "非 Windows，跳过单实例检测"}
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                     wintypes.LPCWSTR]
        ERROR_ALREADY_EXISTS = 183
        ctypes.set_last_error(0)
        handle = k32.CreateMutexW(None, True, name)
        err = ctypes.get_last_error()
        if not handle:
            return {"acquired": True, "first": True, "name": name,
                    "reason": "CreateMutexW 失败（err=%d），跳过检测" % err}
        if err == ERROR_ALREADY_EXISTS:
            # 已经有一个在跑：把这个句柄关掉，别影响对方
            try:
                k32.CloseHandle(handle)
            except Exception:
                pass
            return {"acquired": False, "first": False, "name": name,
                    "reason": "同一目录已有一个外壳在运行"}
        _MUTEX_HANDLE = handle
        return {"acquired": True, "first": True, "name": name, "reason": ""}
    except Exception as exc:
        return {"acquired": True, "first": True, "name": name,
                "reason": "单实例检测异常（%r），按单实例继续" % (exc,)}


def signal_existing_instance(shell_path=None):
    """通知同目录的第一个实例"把窗口显示出来"。返回 (ok, message)。"""
    shell_path = shell_path or shell_dir()
    if os.name != "nt":
        return False, "非 Windows"
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenEventW.restype = wintypes.HANDLE
        k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                   wintypes.LPCWSTR]
        k32.SetEvent.restype = wintypes.BOOL
        k32.SetEvent.argtypes = [wintypes.HANDLE]
        EVENT_MODIFY_STATE = 0x0002
        handle = k32.OpenEventW(EVENT_MODIFY_STATE, False,
                                _wakeup_event_name(shell_path))
        if not handle:
            return False, "没有找到在运行的外壳窗口事件"
        try:
            ok = bool(k32.SetEvent(handle))
        finally:
            try:
                k32.CloseHandle(handle)
            except Exception:
                pass
        return (True, "") if ok else (False, "SetEvent 失败")
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


def create_wakeup_event(shell_path=None):
    """第一个实例调用：建一个命名事件，用来接收"把窗口叫出来"的请求。

    返回 (handle, error)。非 Windows 返回 (None, "非 Windows")。
    """
    shell_path = shell_path or shell_dir()
    if os.name != "nt":
        return None, "非 Windows"
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                     wintypes.BOOL, wintypes.LPCWSTR]
        handle = k32.CreateEventW(None, False, False,
                                  _wakeup_event_name(shell_path))
        if not handle:
            return None, "CreateEventW 失败"
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        return handle, ""
    except Exception as exc:
        return None, "%r" % (exc,)


def wait_wakeup_event(handle, timeout_ms=1000):
    """等一次"叫窗口"请求。返回 True 表示真的被叫了（超时返回 False）。"""
    if handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        WAIT_OBJECT_0 = 0
        return k32.WaitForSingleObject(handle, int(timeout_ms)) == WAIT_OBJECT_0
    except Exception:
        return False
