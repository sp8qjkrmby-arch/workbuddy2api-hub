# -*- coding: utf-8 -*-
"""下载护栏：**截断必须被发现，而且能断点续传补回来**。

来自真实故障（2026-10-14）：9.9 MB 的官方包被直链分别在 4.7 MB / 2.8 MB 处
切断，旧的 `_download` 把半包当成下载成功，错误只好拖到 SHA256 那一步，
在界面上报成一句指不到原因的"校验失败"。

本脚本用**本地 HTTP 服务**复现同样的故障（请求发一半就断开连接），验证三件事：

1. 字节数对不上 **不算成功** → 自动带 `Range` 续传 → 最终文件与原文逐字节一致；
2. 怎么断都补不回来 → 抛 UpdateError，消息里明说"下载不完整 x/y MB"；
3. 服务器不认 Range（总是返回 200 整包）→ 从头重下，**不能拼出双倍内容**。

全程只用 127.0.0.1，不联网、不碰内核、不碰任何生产目录。

用法：runtime\\python.exe tests\\download_resume_test.py
"""

import hashlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
               or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配（repo/shell/tests → 内核在 repo 根；生产布局在 <shell>/wb-proxy）
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))

import updater as updater_mod    # noqa: E402

# 本地测试别被系统代理带跑（urllib 默认会读 HTTP_PROXY 这类环境变量）。
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
             "https_proxy", "all_proxy"):
    os.environ.pop(name, None)

PAYLOAD = b"".join(b"wb-shell-download-guard-%06d\n" % i for i in range(40000))
_failed = []


def check(label, cond, extra=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + extra) if extra else ""))
    if not cond:
        _failed.append(label)


class FlakyHandler(BaseHTTPRequestHandler):
    """按需"发一半就断"的下载服务。断几次、认不认 Range 都由类属性控制。"""

    payload = PAYLOAD
    drop_bytes = 0          # 每次请求最多只发这么多字节就断（0 = 不断）
    max_drops = 0           # 一共断几次
    ignore_range = False    # True = 假装不认 Range（永远返回 200 整包）
    requests = []           # 每次请求的 Range 头
    drops_done = [0]
    lock = threading.Lock()

    def log_message(self, *args):     # 别把访问日志打到测试输出里
        pass

    def do_GET(self):
        with FlakyHandler.lock:
            FlakyHandler.requests.append(self.headers.get("Range") or "-")
        payload = FlakyHandler.payload
        start = 0
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            try:
                start = int(rng[6:].split("-")[0])
            except Exception:
                start = 0
        if FlakyHandler.ignore_range and start:
            start = 0
        body = payload[start:]

        drop = 0
        with FlakyHandler.lock:
            if FlakyHandler.drop_bytes and FlakyHandler.drops_done[0] < FlakyHandler.max_drops:
                FlakyHandler.drops_done[0] += 1
                drop = min(len(body), FlakyHandler.drop_bytes)

        if start:
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d"
                             % (start, len(payload) - 1, len(payload)))
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            self.wfile.write(body[:drop] if drop else body)
            self.wfile.flush()
        except Exception:
            pass
        if drop:
            # 声明了完整长度却只发一部分，然后关连接——这正是真实故障的样子
            # （客户端 `read(amt)` 拿到短数据且**不会**抛异常）。
            self.close_connection = True


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d/asset.zip" % server.server_address[1]


def reset(drop_bytes=0, max_drops=0, ignore_range=False):
    FlakyHandler.drop_bytes = drop_bytes
    FlakyHandler.max_drops = max_drops
    FlakyHandler.ignore_range = ignore_range
    FlakyHandler.requests = []
    FlakyHandler.drops_done = [0]


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main():
    root = os.path.join(os.environ["TEMP"], "wb-download-test")
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, "asset.part")
    if os.path.exists(target):
        os.remove(target)

    server, url = serve()
    want = hashlib.sha256(PAYLOAD).hexdigest()
    print("假服务：%s" % url)
    print("原文：%d 字节（%.1f MB），sha256=%s"
          % (len(PAYLOAD), len(PAYLOAD) / 1048576.0, want[:16] + "…"))

    try:
        # ---------- 1. 断两次，靠续传补全 ----------
        section("1. 中断两次 → 必须续传补全（逐字节一致）")
        reset(drop_bytes=len(PAYLOAD) // 3, max_drops=2)
        if os.path.exists(target):
            os.remove(target)
        seen = []

        def progress(done, total):
            seen.append((done, total))

        ok = True
        try:
            updater_mod._download(url, target, size_hint=len(PAYLOAD),
                                  progress=progress, attempts=4, backoff=0.05)
        except Exception as exc:
            ok = False
            print("    异常：%r" % (exc,))
        got = os.path.getsize(target) if os.path.exists(target) else 0
        digest = hashlib.sha256(open(target, "rb").read()).hexdigest() if got else ""
        check("下载最终成功", ok)
        check("体积与原文一致", got == len(PAYLOAD),
              "%d / %d" % (got, len(PAYLOAD)))
        check("内容逐字节一致", digest == want, digest[:16] + "…")
        check("确实断过两次（%d 次断流）" % FlakyHandler.drops_done[0],
              FlakyHandler.drops_done[0] == 2)
        check("重试时带了 Range 续传",
              sum(1 for r in FlakyHandler.requests if r.startswith("bytes=")) >= 2,
              str(FlakyHandler.requests))
        check("进度回调拿到过完整体积",
              bool(seen) and seen[-1][1] == len(PAYLOAD),
              "最后一次 %s" % (seen[-1] if seen else None,))
        check("进度单调不回退",
              all(b[0] >= a[0] for a, b in zip(seen, seen[1:])))

        # ---------- 2. 怎么断都补不回来 → 必须报"下载不完整" ----------
        section("2. 一直断 → 必须失败，并说清是「下载不完整」而不是「校验失败」")
        reset(drop_bytes=len(PAYLOAD) // 3, max_drops=99)
        if os.path.exists(target):
            os.remove(target)
        message = ""
        raised = False
        try:
            updater_mod._download(url, target, size_hint=len(PAYLOAD),
                                  attempts=2, backoff=0.05)
        except updater_mod.UpdateError as exc:
            raised = True
            message = str(exc)
        except Exception as exc:
            message = "非 UpdateError：%r" % (exc,)
        check("抛的是 UpdateError", raised, message[:80])
        check("消息里说明「下载不完整」", "下载不完整" in message, message[:120])
        check("消息里带上实际/期望体积", "MB" in message and "/" in message,
              message[:120])
        check("没有把半包留在 target 里当成功",
              (os.path.getsize(target) if os.path.exists(target) else 0)
              < len(PAYLOAD))

        # ---------- 3. 服务器不认 Range → 整包重下，不能拼出双倍 ----------
        section("3. 服务器不认 Range（永远 200 整包）→ 重下而不是拼接")
        reset(drop_bytes=len(PAYLOAD) // 2, max_drops=1, ignore_range=True)
        if os.path.exists(target):
            os.remove(target)
        ok = True
        try:
            updater_mod._download(url, target, size_hint=len(PAYLOAD),
                                  attempts=3, backoff=0.05)
        except Exception as exc:
            ok = False
            print("    异常：%r" % (exc,))
        got = os.path.getsize(target) if os.path.exists(target) else 0
        digest = hashlib.sha256(open(target, "rb").read()).hexdigest() if got else ""
        check("下载最终成功", ok)
        check("体积没有变成两倍", got == len(PAYLOAD), "%d / %d" % (got, len(PAYLOAD)))
        check("内容逐字节一致（拼接会在这里露馅）", digest == want)
        check("第二次请求确实带过 Range（只是被服务器忽略）",
              any(r.startswith("bytes=") for r in FlakyHandler.requests),
              str(FlakyHandler.requests))
    finally:
        server.shutdown()
        server.server_close()
        try:
            os.remove(target)
        except Exception:
            pass

    print()
    print("失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
