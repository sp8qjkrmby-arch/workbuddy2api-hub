# -*- coding: utf-8 -*-
"""logging_setup.py — 外壳自己的日志。

与内核的日志分开：内核写自己的 stdout/stderr（由 service.py 抓成 core-<port>.log），
外壳的诊断信息写 shell.log。分开的理由是排障时能一眼分清「谁在说话」。

三条约束：
1. 绝不因为写日志而抛异常——日志是诊断手段，不是功能；
2. 单文件到 2MB 就轮转，最多留 3 份，磁盘不会被撑爆；
3. 任何密钥都不进日志（调用方用 config.redact / secret_fingerprint）。
"""

import os
import sys
import threading
import time

LOG_NAME = "shell.log"
MAX_BYTES = 2 * 1024 * 1024
KEEP = 3

_lock = threading.Lock()
_state = {"path": "", "ready": False}


def _timestamp():
    now = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + \
        ".%03d" % int((now % 1) * 1000)


def setup(data_dir_path):
    """确定日志文件并保证目录存在。返回日志完整路径（失败返回空串）。"""
    try:
        os.makedirs(data_dir_path, exist_ok=True)
    except Exception:
        return ""
    path = os.path.join(data_dir_path, LOG_NAME)
    with _lock:
        _state["path"] = path
        _state["ready"] = True
    return path


def log_path():
    return _state["path"]


def _rotate(path):
    """path → path.1 → path.2 …，丢弃最旧的一份。"""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < MAX_BYTES:
            return
        oldest = "%s.%d" % (path, KEEP)
        if os.path.isfile(oldest):
            os.remove(oldest)
        for index in range(KEEP - 1, 0, -1):
            source = "%s.%d" % (path, index)
            if os.path.isfile(source):
                os.replace(source, "%s.%d" % (path, index + 1))
        os.replace(path, path + ".1")
    except Exception:
        pass


def log(message, level="INFO", echo=False):
    """写一行。`echo=True` 时同时打到 stderr（CLI / 自检模式用）。"""
    line = "%s  %-5s  %s" % (_timestamp(), level, message)
    if echo:
        try:
            sys.stderr.write("[wb-shell] " + line + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    path = _state["path"]
    if not path or not _state["ready"]:
        return
    try:
        with _lock:
            _rotate(path)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def tail_lines(count=200, path=None):
    """读日志尾部若干行（给窗口日志面板兜底用）。"""
    path = path or _state["path"]
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return []
    if count and count > 0:
        return lines[-count:]
    return lines
