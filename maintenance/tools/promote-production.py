"""把 v1.10.9 载荷应用到生产内核目录（复用产品自带的备份/安装代码）。

前置条件：8788 必须已经停止（外壳已退出），否则文件会被占用/被看门狗回写。
行为：
    1. 校验包内 manifest 与 wb_proxy.py 版本一致（防止装错包）
    2. backup_current() → 生成 core 备份（可回退）
    3. apply_stage()  → 只动 manifest.files 列出的文件 + release-manifest.json
    4. 任一步失败 → restore_backup() 自动还原，并以非 0 退出
"""
import json
import os
import shutil
import socket
import sys

ROOT = r"C:\Desktop\wb-shell\wb-proxy"      # 生产 app_root
PACKAGE = ROOT                              # 生产的 manifest 就在 wb-proxy\ 里（与便携包布局不同）
ZIP = r"C:\Desktop\2api维护\repo\dist\workbuddy2api-hub-v1.10.9.zip"
STAGE = r"C:\Desktop\2api维护\.build\promote-stage"
VERSION = "1.10.9"
PORT = 8788

sys.path.insert(0, ROOT)
import wb_update_portable as U  # noqa: E402


def port_open(port):
    with socket.socket() as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    if port_open(PORT):
        print("拒绝执行：%d 仍在监听，请先退出外壳（否则会被看门狗/文件占用打断）" % PORT)
        return 2

    print("当前 manifest.version =", U.load_manifest(os.path.join(PACKAGE, U.MANIFEST_NAME)).get("version"))

    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE, exist_ok=True)
    print("解压包…")
    import zipfile
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(STAGE)

    zip_manifest = U.read_zip_manifest(ZIP)
    root_name = zip_manifest.get("root")
    manifest = U.validate_stage(STAGE, VERSION, root_name)
    files = U.profile_files(manifest, None)
    print("包内 manifest：version=%s tag=%s files=%d" % (manifest.get("version"), manifest.get("tag"), len(files)))

    print("备份当前程序文件…")
    backup, saved, absent = U.backup_current(ROOT, PACKAGE, files, VERSION)
    print("备份目录 =", backup, "| 已存文件 =", len(saved), "| 原本缺失 =", len(absent))

    try:
        print("应用新版本…")
        U.apply_stage(STAGE, ROOT, PACKAGE, manifest, files)
    except Exception as exc:  # noqa: BLE001
        print("应用失败（%s），正在还原…" % exc)
        U.restore_backup(backup, ROOT, PACKAGE, saved, absent)
        print("已还原到上一版本")
        return 1

    now = U.load_manifest(os.path.join(PACKAGE, U.MANIFEST_NAME)).get("version")
    with open(os.path.join(ROOT, "wb_proxy.py"), encoding="utf-8") as fh:
        text = fh.read()
    import re
    m = re.search(r'server_version\s*=\s*"wb-proxy/([^"]+)"', text)
    print("安装后 manifest.version =", now, "| wb_proxy.server_version =", m.group(1) if m else "?")
    ok = now == VERSION and m and m.group(1) == VERSION
    print("PROMOTE %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
