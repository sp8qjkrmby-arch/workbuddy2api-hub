# -*- coding: utf-8 -*-
"""secret_store.py — 存一个**只有当前 Windows 用户能解开**的小秘密。

用途：用户勾选「记住面板密码」之后，把面板密码存下来，好在重开窗口、
重启内核、重启外壳之后自动登录。面板会话（token）在**内核内存里**，
重启必然失效、且内核作者明确说这是有意的安全取舍，所以"记住"只能记密码。

为什么不用 `shell.json`：那是个普通 JSON，明文口令放进去等于不设防
（而且它会被备份、会被截进排障信息里）。这里用 **DPAPI**
（`CryptProtectData`，用户作用域）：密文换一台机器、换一个 Windows 用户
都解不开，文件被拷走也没用。

三条约束：
1. **只在用户明确勾选后写入**——绝不自作主张记密码；
2. **密码不进日志**：本模块只返回 (ok, message)，message 里永不回显秘密；
3. **非 Windows 明确报不支持**，不假装成功（外壳的 GUI/自启本来就是 Windows 专用）。
"""

import ctypes
import os
import sys

import paths

# 密文文件名（放在 data\ 下，与 shell.json 分开：少一份被顺手拷走的机会）
SECRET_NAME = "panel-password"
SUFFIX = ".sealed"
# 不让 DPAPI 弹任何界面（它默认可能弹一个确认框，pythonw 下会很难看）
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def available():
    """当前平台能不能用 DPAPI。"""
    return os.name == "nt"


_BOUND = {}


def _dlls():
    """拿到（并声明好原型的）crypt32 / kernel32。

    ⚠️ **必须声明 argtypes**：不声明时 ctypes 把参数当 32 位 int 转换，而
    `LocalFree(blob.pbData)` 传的是一个 48 位地址的 Python int，会直接
    炸成 "argument 1: OverflowError: int too long to convert"——而且是在
    加密**成功之后**、释放内存时才炸，看起来像"加密失败"，特别误人。
    """
    if not available():
        raise OSError("当前平台没有 DPAPI（只有 Windows 有）")
    if _BOUND:
        return _BOUND["crypt32"], _BOUND["kernel32"]
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(_Blob)]
    crypt32.CryptProtectData.restype = ctypes.c_bool
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(_Blob)]
    crypt32.CryptUnprotectData.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    _BOUND["crypt32"] = crypt32
    _BOUND["kernel32"] = kernel32
    return crypt32, kernel32


def _protect(data):
    """明文 bytes → DPAPI 密文 bytes（当前用户可解）。"""
    if not data:
        raise ValueError("空内容不加密")
    crypt32, kernel32 = _dlls()
    # 注意：这个 buffer 必须在整个调用期间活着（ctypes 不会替你保住它）
    buffer_ = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer_, ctypes.c_void_p))
    blob_out = _Blob()
    ok = crypt32.CryptProtectData(ctypes.byref(blob_in),
                                  "wb-shell panel password", None, None, None,
                                  CRYPTPROTECT_UI_FORBIDDEN,
                                  ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(data):
    """DPAPI 密文 bytes → 明文 bytes（换个用户/换台机器会失败——这正是目的）。"""
    if not data:
        raise ValueError("空密文")
    crypt32, kernel32 = _dlls()
    buffer_ = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer_, ctypes.c_void_p))
    blob_out = _Blob()
    ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                    CRYPTPROTECT_UI_FORBIDDEN,
                                    ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def secret_path(name=SECRET_NAME):
    return os.path.join(paths.data_dir(), name + SUFFIX)


def has(name=SECRET_NAME):
    try:
        return os.path.isfile(secret_path(name))
    except Exception:
        return False


def saved_at(name=SECRET_NAME):
    """存下来的时间（给人看的字符串）；没有就返回空串。"""
    try:
        import time
        path = secret_path(name)
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(os.path.getmtime(path)))
    except Exception:
        return ""


def save(text, name=SECRET_NAME):
    """存一个秘密。返回 (ok, message)。message 里不含秘密本身。"""
    if not text:
        return False, "内容为空，没有保存"
    if not available():
        return False, "当前平台不支持加密存储（只有 Windows 的 DPAPI 可用）"
    path = secret_path(name)
    try:
        blob = _protect(str(text).encode("utf-8"))
    except Exception as exc:
        return False, "加密失败：%s" % exc
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception as exc:
        return False, "写盘失败：%s" % exc
    return True, "已加密保存（只有当前 Windows 用户能解开）"


def load(name=SECRET_NAME):
    """取回秘密。返回 (text, message)：出错时 text 为空、message 说明原因。"""
    path = secret_path(name)
    if not os.path.isfile(path):
        return "", ""
    if not available():
        return "", "当前平台不支持解密"
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
        return _unprotect(blob).decode("utf-8"), ""
    except Exception as exc:
        # 换用户/换机器/文件损坏都会走到这里，属于预期内
        return "", "解不开（%s）——可能换了 Windows 用户或换了机器" % exc


def clear(name=SECRET_NAME):
    """删掉存下来的秘密。返回 (ok, message)。"""
    path = secret_path(name)
    try:
        if os.path.isfile(path):
            os.remove(path)
        return True, "已忘记"
    except Exception as exc:
        return False, "删除失败：%s" % exc


def main(argv=None):
    """命令行自检（给自己排障用，不打印秘密本身）。"""
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "status":
        print("平台支持 : %s" % available())
        print("密文路径 : %s" % secret_path())
        print("已保存   : %s%s" % (has(),
                                   ("（%s）" % saved_at()) if has() else ""))
        return 0
    if args and args[0] == "clear":
        ok, message = clear()
        print(message)
        return 0 if ok else 1
    text, message = load()
    print("平台支持 : %s" % available())
    print("密文路径 : %s" % secret_path())
    print("已保存   : %s" % has())
    print("能解开   : %s%s" % (bool(text), ("——" + message) if message else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
