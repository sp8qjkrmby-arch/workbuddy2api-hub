# -*- coding: utf-8 -*-
"""autostart.py — 开机自启。

Windows 上只用一个地方：`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`。
选它的理由很实际——**纯用户级、不需要管理员**，而且是"每用户登录时启动"的
标准位置，卸载时删掉值就干净了。

三条自我约束：
1. **勾选态以注册表为准**，不靠配置文件记忆。你在注册表编辑器里手删了，
   界面就得显示未勾选，而不是继续骗你说开着。
2. **写入后读回核实**。组策略禁用 Run 键、或权限不足时，如实报错，不假装成功。
3. **路径漂移自愈**。外壳被挪过位置后，值里记的旧路径会失效；启动时比对
   当前路径，不一致就提示修正，而不是静默失效。
"""

import os
import subprocess
import sys

import logging_setup
import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WorkBuddy2API-Hub"
# 登录自启只驻托盘（不建窗口）；内核是否同时拉起由 data\shell.json 的
# auto_start_core 决定（默认 true = 登录即代理可用）。想改成"登录只驻托盘、
# 内核等我手动点"，把 auto_start_core 设成 false，不用改这里。
TRAY_FLAG = "--tray-only"


def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def supported():
    return os.name == "nt" and _winreg() is not None


def pythonw_for(exe):
    """同一个解释器旁边的 pythonw.exe（无控制台版本）。

    便携运行时不自带它，这时返回空串，由调用方走 vbs 兜底。
    """
    if not exe:
        return ""
    folder = os.path.dirname(os.path.abspath(exe))
    candidate = os.path.join(folder, "pythonw.exe")
    if os.path.isfile(candidate):
        return candidate
    if os.path.basename(exe).lower() == "pythonw.exe":
        return exe
    return ""


def build_command(shell_path=None, python_exe="", prefer_vbs=False):
    """拼出开机自启要跑的命令行。返回 (命令, 说明)。

    优先 pythonw.exe：它没有控制台窗口，登录时不会有黑框一闪。
    没有 pythonw.exe 时退回 `启动.vbs`（wscript + 隐藏窗口），
    再不行就退回 python.exe（会有黑框，但功能正常）。
    """
    shell_path = shell_path or paths.shell_dir()
    launcher = os.path.join(shell_path, "launcher")
    vbs = os.path.join(shell_path, "启动.vbs")

    if not prefer_vbs:
        pythonw = pythonw_for(python_exe)
        if pythonw:
            return ('"%s" "%s" %s' % (pythonw, launcher, TRAY_FLAG),
                    "pythonw.exe（无控制台窗口）")
    if os.path.isfile(vbs):
        return ('wscript.exe "%s" %s' % (vbs, TRAY_FLAG),
                "启动.vbs（wscript 隐藏窗口）")
    if python_exe:
        return ('"%s" "%s" %s' % (python_exe, launcher, TRAY_FLAG),
                "python.exe（可能带控制台窗口）")
    return "", "没有可用的启动方式"


def _open_key(winreg, create=False):
    access = winreg.KEY_READ
    if create:
        access |= winreg.KEY_WRITE
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def read_value():
    """读回自启项。返回 dict：exists / command / value_name。"""
    result = {"exists": False, "command": "", "value_name": VALUE_NAME}
    if not supported():
        return result
    winreg = _winreg()
    try:
        with _open_key(winreg) as key:
            command, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        result["exists"] = bool(command)
        result["command"] = str(command or "")
    except FileNotFoundError:
        pass
    except OSError as exc:
        result["error"] = str(exc)
    return result


def is_enabled():
    return bool(read_value().get("exists"))


def enable(shell_path=None, python_exe="", prefer_vbs=False):
    """写入自启项并读回核实。返回 (ok, message)。"""
    if not supported():
        return False, "当前平台不支持通过注册表设置开机自启"
    shell_path = shell_path or paths.shell_dir()
    command, how = build_command(shell_path, python_exe, prefer_vbs)
    if not command:
        return False, how
    winreg = _winreg()
    try:
        with _open_key(winreg, create=True) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        logging_setup.log("写自启项失败：%r" % (exc,), "ERROR")
        return False, ("写入注册表失败：%s（组策略可能禁用了 Run 键）" % exc)
    back = read_value()
    if not back.get("exists") or back.get("command") != command:
        return False, "写入后读回不一致，未生效（当前值：%s）" % back.get("command")
    logging_setup.log("已开启开机自启：%s（%s）" % (command, how))
    return True, "已开启开机自启（%s）" % how


def disable():
    """删除自启项。值本来就不存在也算成功。返回 (ok, message)。"""
    if not supported():
        return False, "当前平台不支持通过注册表设置开机自启"
    winreg = _winreg()
    existed = is_enabled()
    try:
        with _open_key(winreg, create=True) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True, "开机自启本来就是关闭的"
    except OSError as exc:
        logging_setup.log("删自启项失败：%r" % (exc,), "ERROR")
        return False, "删除注册表项失败：%s" % exc
    if is_enabled():
        return False, "删除后读回仍然存在，未生效"
    logging_setup.log("已关闭开机自启")
    return True, "已关闭开机自启" if existed else "开机自启本来就是关闭的"


def detect_drift(shell_path=None):
    """检查自启项里的路径是否还指向当前外壳。返回 dict。

    stale=True 表示：项存在，但里面记的路径不是当前外壳目录——
    通常是你把外壳文件夹挪了位置或改了名。
    """
    shell_path = os.path.abspath(shell_path or paths.shell_dir())
    info = read_value()
    info["shell_path"] = shell_path
    info["stale"] = False
    if not info.get("exists"):
        return info
    command = (info.get("command") or "").replace("/", "\\").lower()
    if shell_path.replace("/", "\\").lower() in command:
        return info
    info["stale"] = True
    return info


def apply_autostart_preference(enabled, shell_path=None, python_exe=""):
    """按偏好设置自启（幂等）。目标是让注册表与偏好一致。"""
    if enabled:
        if is_enabled():
            drift = detect_drift(shell_path)
            if not drift.get("stale"):
                return True, "开机自启已开启"
            return enable(shell_path, python_exe)
        return enable(shell_path, python_exe)
    if not is_enabled():
        return True, "开机自启未开启"
    return disable()


# --- 其它平台：留接口，未实现 -----------------------------------------------

def unsupported_note():
    if os.name == "nt":
        return ""
    if sys.platform == "darwin":
        return ("macOS 自启应写 ~/Library/LaunchAgents/<label>.plist —— "
                "本次未实现，请手工配置")
    return ("Linux 自启应写 ~/.config/autostart/<name>.desktop —— "
            "本次未实现，请手工配置")
