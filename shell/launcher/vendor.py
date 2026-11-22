# -*- coding: utf-8 -*-
"""vendor.py — 把随壳分发的依赖接进 sys.path。

依赖以目录形式住在 `launcher/vendor/`：

    vendor/py      纯 Python 部分（webview / pystray / clr_loader / bottle / pythonnet）
    vendor/py312   cp312 的二进制（cffi、Pillow）
    vendor/py314   cp314 的二进制

按解释器版本挑二进制目录，是因为 `_cffi_backend.cp314-*.pyd`、`PIL\\_imaging.*.pyd`
是按 cpXXX 编译的，混放会让 import 直接失败。纯 Python 那份两版本共用。

**不联网、不装包**：这里只做 `sys.path` 插入。依赖升级 = 整块替换 vendor 目录，
本身就是可回退的动作。
"""

import os
import sys

import paths

_ready = {"done": False, "webview": False, "tray": False, "errors": []}


def _abi_dir():
    tag = "py%d%d" % (sys.version_info[0], sys.version_info[1])
    return tag


def add_paths(extra_dirs=None):
    """把 vendor 目录插到 sys.path 前面。返回实际插入的目录列表。

    插到**前面**：外壳自带依赖必须优先于系统 site-packages 里的同名包，
    否则机器上装过旧版 pywebview 就会把随壳那份顶掉。
    """
    added = []
    vendor = paths.vendor_dir()
    candidates = [os.path.join(vendor, "py"),
                  os.path.join(vendor, _abi_dir())]
    for extra in (extra_dirs or []):
        candidates.append(extra)
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        if folder in sys.path:
            continue
        sys.path.insert(0, folder)
        added.append(folder)
    return added


def ensure():
    """插入路径并探测可用后端。幂等，可以反复调。

    返回 dict：done / webview / tray / errors / abi / paths。
    **任何失败都不抛异常**——依赖缺失是"降级用别的界面"，不是"外壳起不来"。
    """
    if _ready["done"]:
        return dict(_ready)
    _ready["abi"] = _abi_dir()
    _ready["paths"] = add_paths()
    _ready["errors"] = []

    if not os.path.isdir(paths.vendor_dir()):
        _ready["errors"].append("vendor 目录不存在：%s" % paths.vendor_dir())

    try:
        import webview  # noqa: F401
        _ready["webview"] = True
        _ready["webview_version"] = getattr(webview, "__version__", "unknown")
    except Exception as exc:
        _ready["webview"] = False
        _ready["errors"].append("pywebview 不可用：%s: %s" % (type(exc).__name__, exc))

    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        _ready["tray"] = True
    except Exception as exc:
        _ready["tray"] = False
        _ready["errors"].append("pystray/Pillow 不可用：%s: %s"
                                % (type(exc).__name__, exc))

    _ready["done"] = True
    return dict(_ready)


def describe():
    info = ensure()
    lines = ["vendor 目录：%s（ABI: %s）" % (paths.vendor_dir(), info.get("abi"))]
    lines.append("  pywebview：%s" % ("可用 %s" % info.get("webview_version", "")
                                      if info.get("webview") else "不可用"))
    lines.append("  托盘依赖：%s" % ("可用" if info.get("tray") else "不可用"))
    for error in info.get("errors") or []:
        lines.append("  [提示] %s" % error)
    return lines
