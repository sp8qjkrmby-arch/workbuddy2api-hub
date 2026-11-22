# -*- coding: utf-8 -*-
"""clipboard.py — 往 Windows 剪贴板写/读**纯文本**（ctypes，零依赖）。

为什么外壳要自己干这件事（2026-10-15 用户报障"无法复制"）：

看板跑在 WebView2 里，"复制"通常走 `navigator.clipboard.writeText`，但它可能
被权限拦下（WebView2 对 clipboard 权限有自己的说法）；而**异步失败之后再退回
`document.execCommand('copy')` 是错的**——`execCommand` 同样要求"用户手势"，等
Promise 被 reject 时那个手势早就过期了，于是两条路一起失败。用户看到的就是
"点了没反应/复制失败"。

外壳用 Win32 自己写剪贴板就绕开了这一整类问题：不看浏览器脸色，结果还能验证
（`SetClipboardData` 返回真假）。

只处理 `CF_UNICODETEXT`（纯文本）。提供 `get_text()` 是为了让测试能"先存一份、
用完还回去"，不给用户留垃圾（也绝不把要复制的内容写进日志——那可能是 API Key）。
"""

import ctypes
import ctypes.wintypes as wt
import time

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 声明 argtypes/restype 不是洁癖：不声明时 64 位句柄会被按 32 位截断，
# 而且往往"写成功了、释放内存时才炸"（交接文档 §6.1 记过同样的坑）。
_user32.OpenClipboard.argtypes = [wt.HWND]
_user32.OpenClipboard.restype = wt.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wt.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wt.BOOL
_user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_user32.SetClipboardData.restype = wt.HANDLE
_user32.GetClipboardData.argtypes = [wt.UINT]
_user32.GetClipboardData.restype = wt.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
_user32.IsClipboardFormatAvailable.restype = wt.BOOL
_user32.EnumClipboardFormats.argtypes = [wt.UINT]
_user32.EnumClipboardFormats.restype = wt.UINT

_kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wt.HGLOBAL
_kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalUnlock.restype = wt.BOOL
_kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
_kernel32.GlobalFree.restype = wt.HGLOBAL


def _open(retries=10, wait=0.05):
    """剪贴板是全局独占资源：别的程序正拿着时 OpenClipboard 会失败，重试几次。

    返回 True 表示**已经打开**（调用方负责 CloseClipboard）。
    """
    for _ in range(max(1, retries)):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(wait)
    return False


def set_text(text):
    """把文本放进剪贴板。返回 (ok, message)；message 面向用户、不含正文。"""
    text = "" if text is None else str(text)
    if not text:
        return False, "没有要复制的内容"
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False, "分配剪贴板内存失败"
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        _kernel32.GlobalFree(handle)
        return False, "锁定剪贴板内存失败"
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        _kernel32.GlobalUnlock(handle)

    if not _open():
        _kernel32.GlobalFree(handle)
        return False, "剪贴板被别的程序占着，稍后再试"
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False, "写入剪贴板失败"
        # 成功之后内存归系统所有，**不能**再 GlobalFree（那会留下一段悬空数据）
        handle = None
    finally:
        _user32.CloseClipboard()
    return True, "已复制 %d 个字符" % len(text)


def has_text():
    """剪贴板里现在是不是文本（用来决定"要不要动用户的剪贴板"）。"""
    return bool(_user32.IsClipboardFormatAvailable(CF_UNICODETEXT))


def is_empty():
    """剪贴板是不是空的（任何格式都没有）。

    用途：测试前判断"能不能安全地写一下再还原"——如果里面是一张图，
    我们是没法把它还原回去的，那就别碰（宁可少测一项，也别毁用户的东西）。

    ⚠️ `EnumClipboardFormats` **必须先 OpenClipboard**，否则它照样返回 0
    ——那样"空"和"打不开"就分不清了（第一次就是这么写错的：明明剪贴板里有文本，
    却同时报告 has_text=True、is_empty=True）。
    """
    if not _open():
        return False          # 打不开就当"里面有东西"：保守，不碰
    try:
        return not _user32.EnumClipboardFormats(0)
    finally:
        _user32.CloseClipboard()


def clear():
    """清空剪贴板。返回 (ok, message)。"""
    if not _open():
        return False, "剪贴板被别的程序占着"
    try:
        _user32.EmptyClipboard()
    finally:
        _user32.CloseClipboard()
    return True, "已清空"


def get_text():
    """读回剪贴板里的文本。返回 (ok, text)。读不到就 (False, "")。"""
    if not has_text():
        return False, ""
    if not _open():
        return False, ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return False, ""
        # GetClipboardData 给的是 HGLOBAL；锁住它按 UTF-16 读出来。
        # 这份内存**属于剪贴板**，读完只解锁，不释放。
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return False, ""
        try:
            return True, ctypes.c_wchar_p(pointer).value or ""
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()
