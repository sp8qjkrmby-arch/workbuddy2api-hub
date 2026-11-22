"""安装/回滚演练：在生产的“克隆”上跑一遍真实更新链路（绝不碰 8788）。

布局要求：
    <clone>\\release-manifest.json      （从生产复制）
    <clone>\\wb-proxy\\...               （从生产复制，含 python\\ 运行时）

流程：
    1. 在 8799 起克隆实例（生产同款 .bat 启动器）
    2. run_update(...)：下载 → 校验 SHA256 → 解压 → validate_stage → 备份 → 停 → 换文件 → 重启 → 健康检查（版本必须 == 1.10.9）
    3. 校验 manifest / 备份包 / 端到端 chat
    4. run_rollback(...)：用更新留下的备份回到 1.10.8
    5. 校验 manifest 回到 1.10.8 + 端到端 chat
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

CLONE = r"C:\Desktop\2api维护\.build\prod-clone"
APP = os.path.join(CLONE, "wb-proxy")
PORT = 8799
REPO = "sp8qjkrmby-arch/workbuddy2api-hub"
TAG = "v1.10.9"
VERSION = "1.10.9"
OLD_VERSION = "1.10.8"

sys.path.insert(0, APP)
import wb_update_portable as U  # noqa: E402


def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)[:80]}


def manifest_version():
    path = os.path.join(CLONE, "release-manifest.json")
    if not os.path.isfile(path):
        return "(缺失)"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("version")


def port_pid():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue).OwningProcess" % PORT],
                         capture_output=True, text=True)
    text = (out.stdout or "").strip().splitlines()
    return int(text[0]) if text and text[0].isdigit() else 0


def kill_port():
    pid = port_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        for _ in range(20):
            time.sleep(0.5)
            if not port_pid():
                return pid
    return pid


def status():
    path = os.path.join(APP, "updates", U.STATUS_NAME)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def smoke():
    out = subprocess.run([os.path.join(APP, "python", "python.exe"),
                          r"C:\Desktop\2api维护\.build\smoke.py", str(PORT),
                          os.path.join(APP, "accounts")],
                         capture_output=True, text=True, timeout=180)
    tail = [l for l in (out.stdout or "").splitlines() if l.strip().startswith(("health", "models", "chat", "RESULT"))]
    return out.returncode, tail


def step(title):
    print("\n=== %s ===" % title, flush=True)


def main():
    step("0) 初始状态")
    print("clone manifest version =", manifest_version())
    print("port %d pid = %s" % (PORT, port_pid() or "(未启动)"))

    if not port_pid():
        step("1) 起克隆实例（8799）")
        U.start_service(APP, PORT, False)
        print("wait_health =", U.wait_health(PORT, timeout=60))
        print("pid =", port_pid(), "version =", health()[1].get("version"))

    pid = port_pid()
    step("2) run_update → %s" % VERSION)
    started = time.time()
    code = U.run_update(APP, CLONE, TAG, VERSION, PORT, False, pid, REPO,
                        keep_backups=3)
    print("run_update 返回 %s，耗时 %.1f 秒" % (code, time.time() - started))
    print("status =", status())

    step("3) 校验安装结果")
    print("manifest version =", manifest_version())
    status_code, body = health()
    print("health = HTTP %s version=%s" % (status_code, body.get("version")))
    backups = U.list_backups(APP)
    print("备份条目:", [b.get("version") for b in backups] if backups else "(无)")
    rc, lines = smoke()
    print("smoke rc=%s" % rc)
    for l in lines:
        print("   ", l)

    installed = manifest_version() == VERSION and status_code == 200
    print("\nINSTALL %s" % ("PASS" if (code == 0 and installed and rc == 0) else "FAIL"))

    if not installed:
        return 1

    step("4) run_rollback → %s" % OLD_VERSION)
    kill_port()
    started = time.time()
    code2 = U.run_rollback(APP, CLONE, OLD_VERSION, PORT, port_pid() or 0,
                           keep_backups=3)
    print("run_rollback 返回 %s，耗时 %.1f 秒" % (code2, time.time() - started))
    print("manifest version =", manifest_version())
    status_code, body = health()
    print("health = HTTP %s version=%s" % (status_code, body.get("version")))
    rc2, lines2 = smoke()
    print("smoke rc=%s" % rc2)
    for l in lines2:
        print("   ", l)

    rolled = manifest_version() == OLD_VERSION and status_code == 200
    print("\nROLLBACK %s" % ("PASS" if (code2 == 0 and rolled and rc2 == 0) else "FAIL"))
    return 0 if rolled else 1


if __name__ == "__main__":
    sys.exit(main())
