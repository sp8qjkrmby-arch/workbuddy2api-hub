# -*- coding: utf-8 -*-
"""lan_share_test.py — 「局域网共享」这一节的**外壳侧**行为。

三件事，都是"错了就会误导用户"的那种：

1. **开关只写外壳自己的 shell.json**（原子写、保留未知键），并同步内存里的
   `service.lan`——否则"保存了却不生效"要再等一次外壳重启才发现；
2. 被 `--lan` / `bootstrap.json` 覆盖时**拒绝**改配置（内核那边 `--lan` 优先，
   改了也没用，不如当场说清楚）；
3. 「别的设备该填哪个 Key」按**内核真实的规矩**算：`wb_proxy.identify_key()`
   里是 `extra = () if configured_keys() else (API_KEY,)`——面板里一旦有启用的
   Key，那些 Key 就是唯一被接受的凭据，`--api-key` 与自动生成的 `launcher_key`
   都会**被忽略**。这一条是老文档（交接文档 §7.1 说"读 launcher_key"）没覆盖的，
   照它做会让人去填一个内核根本不认的 Key。

隔离：全部在 `%TEMP%\\wb-lan-test` 下的**临时外壳目录**里跑；每一节结束都拿
sha256 复核真 `data\\shell.json` 与真 `accounts\\settings.json` 没被动过。
**不启动内核**（service 是假的）。

用法：runtime\\python.exe tests\\lan_share_test.py
"""

import hashlib
import json
import os
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
               or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR") or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))

import config as config_mod      # noqa: E402
import lanshare                  # noqa: E402
import logging_setup             # noqa: E402
import service as service_mod    # noqa: E402
import ui_web                    # noqa: E402

SHELL = _SHELL_DIR
REAL_CORE = _KERNEL_DIR
REAL_SHELL_JSON = os.path.join(SHELL, "data", "shell.json")
REAL_ACCOUNTS_JSON = os.path.join(REAL_CORE, "accounts", "settings.json")
TEST_PORT = 18903                # 在 kill_test_processes 的测试白名单里
_failed = []


def check(label, cond, extra=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  " + extra) if extra else ""))
    if not cond:
        _failed.append(label)


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except Exception:
        return "(读不到)"


def read_log(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        return ""


class FakeService(object):
    """只实现 WebWindow 用到的那几个成员：status / restart / lan / core_dir。"""

    def __init__(self, port=TEST_PORT, state=service_mod.RUNNING, lan=False,
                 command=None, core_dir=REAL_CORE, log_handle=None):
        self.port = port
        self.state = state
        self.lan = lan
        self.core_dir = core_dir
        self.command = list(command if command is not None else [])
        self.restart_calls = []
        self.effective = {"config": {}}
        # 真 CoreService 的 status()["log_file"] 是**打开的文件对象**（不是路径），
        # 这里照原样模拟——就是为了钉住"别把它塞进 js_api 返回值"那条。
        self.log_handle = log_handle

    def _log_path(self):
        return os.path.join(SHELL, "data", "core-%d.log" % self.port)

    def status(self):
        labels = {service_mod.RUNNING: "运行中",
                  service_mod.EXTERNAL: "外部内核",
                  service_mod.STOPPED: "已停止"}
        return {"state": self.state,
                "state_label": labels.get(self.state, self.state),
                "port": self.port,
                "command": list(self.command),
                "log_file": self.log_handle or self._log_path()}

    def restart(self):
        self.restart_calls.append(time.time())
        return True, "测试用重启"


def make_window(root, service, config_lan=None, eff_lan=None, extra_shell=None):
    """在临时外壳目录里造一个 WebWindow（不建窗、不连内核）。

    `config_lan` 改的是 shell.json 里那个值，`eff_lan` 改的是**生效值**
    ——两者不一致就代表"被 --lan / bootstrap.json 顶掉了"（第三节要的正是这个）。
    """
    shell = os.path.join(root, "shell")
    data = os.path.join(shell, "data")
    accounts = os.path.join(root, "accounts")
    usage = os.path.join(root, "usage")
    for path in (data, accounts, usage):
        os.makedirs(path, exist_ok=True)
    payload = {"port": TEST_PORT, "lan": False}
    if extra_shell:
        payload.update(extra_shell)
    with open(os.path.join(data, "shell.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    loaded = config_mod.load(shell)
    cfg = loaded["config"]
    eff = {"config": dict(cfg), "core_dir": REAL_CORE, "core_source": "测试",
           "accounts_dir": accounts, "usage_dir": usage,
           "warnings": [], "fatal": ""}
    if config_lan is not None:
        cfg["lan"] = config_lan
    if eff_lan is not None:
        eff["config"]["lan"] = eff_lan
    service.effective = eff
    window = ui_web.WebWindow(shell, cfg, eff, service,
                              logger=lambda text: logging_setup.log("[web] " + text))
    return window, cfg, eff, shell, accounts


def read_shell_json(shell):
    try:
        with open(os.path.join(shell, "data", "shell.json"), "r",
                  encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def main():
    root = os.path.join(os.environ["TEMP"], "wb-lan-test")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    log_file = logging_setup.setup(root)

    real_shell_sha = sha256_file(REAL_SHELL_JSON)
    real_accounts_sha = sha256_file(REAL_ACCOUNTS_JSON)

    # ---------------- 1. 信息接口 ----------------
    section("1. api_lan_info：本机地址 / 链路 / Key 现状 / 连通性自检（只读）")
    svc = FakeService(command=[r"C:\...\python.exe", "wb_proxy.py", "--port",
                               str(TEST_PORT)])
    window, cfg, eff, shell, accounts = make_window(root, svc)
    info = window.api_lan_info()
    check("返回 ok", info.get("ok") is True)
    check("配置里是关的", info.get("config_lan") is False)
    check("运行中的内核：只监听本机（命令里没有 --lan）",
          info.get("live_lan") is False)
    check("端口正确", info.get("port") == TEST_PORT, str(info.get("port")))
    ips = info.get("ips") or []
    check("拿到了本机内网 IPv4（且没有回环地址）",
          bool(ips) and all(not ip.startswith("127.") for ip in ips), str(ips))
    links = info.get("links") or []
    check("每个内网地址都有一条 API/看板 链路",
          len(links) == len(ips)
          and all((":%d/v1" % TEST_PORT) in row["api"] for row in links))
    check("防火墙提示点名了 allow-firewall.bat（真内核目录里确实有）",
          "allow-firewall.bat" in (info.get("firewall") or "")
          and os.path.isfile(os.path.join(REAL_CORE, "allow-firewall.bat")))
    check("Key 现状：临时 accounts 是空的 → 还没有 Key",
          (info.get("key") or {}).get("kind") == "none",
          str((info.get("key") or {}).get("kind")))
    check("settings 路径指向临时目录（不是真内核）",
          str(info.get("accounts_settings") or "").startswith(root),
          str(info.get("accounts_settings")))
    check("核心不是 external 时不提外部内核",
          info.get("core_external") is False)

    # --lan 在命令里 → live_lan=True；external → 判断不了（None）
    svc2 = FakeService(command=["python.exe", "wb_proxy.py", "--lan"])
    w2, _c, _e, _s, _a = make_window(os.path.join(root, "lan-on"), svc2)
    check("命令里有 --lan 时：运行中的内核 = 监听 0.0.0.0",
          w2.api_lan_info().get("live_lan") is True)
    svc3 = FakeService(state=service_mod.EXTERNAL)
    w3, _c, _e, _s, _a = make_window(os.path.join(root, "external"), svc3)
    info3 = w3.api_lan_info()
    check("内核是外部启动的：live_lan=未知（不瞎猜）",
          info3.get("live_lan") is None and info3.get("core_external") is True)

    # ⚠️ 这条来自生产上的真实故障（2026-10-15）：真 CoreService 的
    # status()["log_file"] 是**打开的文件对象**，一旦混进 js_api 的返回值，
    # pywebview 的 json.dumps 就抛 "Object of type TextIOWrapper is not JSON
    # serializable"，整个接口失效——界面表现为"局域网那一节只剩一句报错、
    # 连开关都没有"（用户报的正是这个）。只有内核**在跑**时才会出现
    # （不跑时 log_file 是 None，所以之前的探针没抓到）。
    handle = open(os.path.join(root, "fake-core.log"), "a", encoding="utf-8")
    try:
        svc_live = FakeService(log_handle=handle)
        win_live, _c, _e, _s, _a = make_window(os.path.join(root, "live-log"),
                                               svc_live)
        live_info = win_live.api_lan_info()
        try:
            json.dumps(live_info)
            dumped = True
        except Exception as exc:
            dumped = False
            print("    JSON 化失败：%r" % (exc,))
        check("内核日志句柄存在时，api_lan_info 仍可 JSON 化（生产故障回归）",
              dumped is True)
        check("返回的是日志**路径**而不是文件对象",
              isinstance(live_info.get("core_log"), str)
              and live_info.get("core_log").endswith("core-%d.log" % TEST_PORT),
              repr(live_info.get("core_log"))[:80])
    finally:
        handle.close()

    # 连通性自检：用假探针，不起任何服务器
    probes = []
    def fake_probe(port, host, timeout):
        probes.append(host)
        return {"state": "ours" if host == "10.0.0.9" else "down"}

    check("reachable_ip：挑出真的答话的那个地址",
          lanshare.reachable_ip(["10.0.0.1", "10.0.0.9"], TEST_PORT,
                                probe=fake_probe) == "10.0.0.9")
    check("reachable_ip：都不答话就返回空串",
          lanshare.reachable_ip(["10.0.0.1"], TEST_PORT,
                                probe=fake_probe) == "")
    check("reachable_ip：没有地址时不探（空进空出）",
          lanshare.reachable_ip([], TEST_PORT, probe=fake_probe) == "")

    # ---------------- 2. 只保存 ----------------
    section("2. api_lan_set：只写外壳自己的 shell.json（原子 + 保留未知键）")
    svc4 = FakeService()
    window4, cfg4, eff4, shell4, accounts4 = make_window(
        os.path.join(root, "save"), svc4,
        extra_shell={"my_custom_key": 123,
                     "update_mirror": "https://example.invalid/"})
    res = window4.api_lan_set({"lan": True})
    check("返回 ok 且没有重启", res.get("ok") is True
          and res.get("restarting") is False, str(res.get("message")))
    saved = read_shell_json(shell4)
    check("shell.json 里 lan 变成 true", saved.get("lan") is True)
    check("未知键被原样保留（升级外壳不抹掉用户自己加的东西）",
          saved.get("my_custom_key") == 123
          and saved.get("update_mirror") == "https://example.invalid/",
          json.dumps(saved, ensure_ascii=False)[:120])
    check("内存里的 service.lan 同步了（否则要再等一次外壳重启）",
          svc4.lan is True)
    check("内存里的 effective 配置也同步了",
          bool(eff4["config"].get("lan")) is True)
    backups = os.path.join(shell4, "data", "config-backups")
    check("写盘前留了一份原件（config-backups）",
          os.path.isdir(backups) and os.listdir(backups) != [],
          str(os.listdir(backups) if os.path.isdir(backups) else "没有目录"))
    if os.path.isdir(backups) and os.listdir(backups):
        old = json.load(open(os.path.join(backups, os.listdir(backups)[0]),
                             encoding="utf-8"))
        check("原件里的 lan 还是旧值 false", old.get("lan") is False)
    res_off = window4.api_lan_set({"lan": False})
    check("关回去也走得通",
          res_off.get("ok") is True
          and read_shell_json(shell4).get("lan") is False)
    # 值没变就不该写盘：写完 mtime 会变，"我没动配置"这类复核就白做了
    shell_json4 = os.path.join(shell4, "data", "shell.json")
    sha_before = sha256_file(shell_json4)
    mtime_before = os.path.getmtime(shell_json4)
    time.sleep(0.05)
    res_same = window4.api_lan_set({"lan": False})
    check("值没变时不写盘（sha 与 mtime 都不动）",
          sha256_file(shell_json4) == sha_before
          and os.path.getmtime(shell_json4) == mtime_before)
    check("并且如实说「没有改动」",
          res_same.get("ok") is True
          and "没有改动" in (res_same.get("message") or ""),
          str(res_same.get("message"))[:60])

    # ---------------- 3. 被 --lan 覆盖时拒绝 ----------------
    section("3. 被 --lan / bootstrap 覆盖时：拒绝改配置（改了也不生效）")
    svc5 = FakeService()
    # shell.json 里是 false，但生效值是 true —— 这就是"启动参数带了 --lan"的形状
    window5, cfg5, eff5, shell5, _a5 = make_window(
        os.path.join(root, "override"), svc5, config_lan=False, eff_lan=True)
    before5 = read_shell_json(shell5)
    res5 = window5.api_lan_set({"lan": False})
    check("拒绝并说明原因", res5.get("ok") is False
          and "--lan" in (res5.get("message") or ""), str(res5.get("message"))[:90])
    check("配置没被改动", read_shell_json(shell5) == before5)
    check("开关也没被偷偷改（内存里还是被覆盖的那个值）",
          bool(cfg5.get("lan")) is False and bool(eff5["config"].get("lan")) is True)

    # ---------------- 4. 保存并重启内核 ----------------
    section("4. 保存并重启内核：后台重启、外部内核不动手")
    svc6 = FakeService()
    window6, cfg6, eff6, shell6, _a6 = make_window(
        os.path.join(root, "restart"), svc6)
    res6 = window6.api_lan_set({"lan": True, "restart": True})
    check("立即返回「正在重启」", res6.get("ok") is True
          and res6.get("restarting") is True, str(res6.get("message"))[:70])
    deadline = time.time() + 5
    while time.time() < deadline and not svc6.restart_calls:
        time.sleep(0.1)
    check("后台真的调了 service.restart()", len(svc6.restart_calls) == 1,
          "调用次数=%d" % len(svc6.restart_calls))
    check("配置已经写盘", read_shell_json(shell6).get("lan") is True)

    svc7 = FakeService(state=service_mod.EXTERNAL)
    window7, cfg7, eff7, shell7, _a7 = make_window(
        os.path.join(root, "external-restart"), svc7)
    res7 = window7.api_lan_set({"lan": True, "restart": True})
    time.sleep(0.6)
    check("外部内核：只保存，不替用户重启",
          res7.get("ok") is True and res7.get("restarting") is False
          and svc7.restart_calls == [], str(res7.get("message"))[:80])
    check("外部内核：配置照样写进去了",
          read_shell_json(shell7).get("lan") is True)

    # ---------------- 5. Key：按内核真实规矩算 ----------------
    section("5. 「该填哪个 Key」：面板 Key 优先，启动参数/launcher key 会作废")

    def accounts_dir_with(name, data):
        path = os.path.join(root, "acc-" + name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "settings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return path

    panel = accounts_dir_with("panel", {
        "api_keys": [{"id": "k1", "name": "手机", "key": "wb-panel-A",
                      "enabled": True, "realm": "cn"},
                     {"id": "k2", "name": "平板", "key": "wb-panel-B",
                      "enabled": True},
                     {"id": "k3", "name": "停用的", "key": "wb-off",
                      "enabled": False},
                     {"id": "k4", "name": "删掉的", "key": "wb-gone",
                      "enabled": True, "deleted_at": "2026-01-01"}],
        "launcher_key": "wb-launcher"})
    st = lanshare.keys_state(panel, None)
    check("面板里有启用 Key → 只接受那两个（禁用的、删掉的都不算）",
          [a["key"] for a in st["accepted"]] == ["wb-panel-A", "wb-panel-B"],
          str([a["key"] for a in st["accepted"]]))
    check("面板 Key 的名字带出来（界面上分得清哪个是哪个）",
          st["accepted"][0]["label"] == "手机"
          and st["accepted"][0]["realm"] == "cn")
    st_cli = lanshare.keys_state(panel, "wb-from-shell")
    check("就算外壳传了 --api-key，面板 Key 仍然优先（内核就是这么定的）",
          [a["key"] for a in st_cli["accepted"]] == ["wb-panel-A", "wb-panel-B"])
    check("并且**明说**那个 api-key 与 launcher key 不被接受",
          st_cli["others"] and all("不接受" in t or "不再被接受" in t
                                   for t in st_cli["others"]),
          " ｜ ".join(st_cli["others"])[:100])

    st2 = lanshare.keys_state(accounts_dir_with("launcher", {
        "launcher_key": "wb-launcher-only"}), None)
    check("没有面板 Key 时：用自动生成的 launcher_key",
          [a["key"] for a in st2["accepted"]] == ["wb-launcher-only"]
          and st2["kind"] == "launcher_key")
    st3 = lanshare.keys_state(accounts_dir_with("launcher", {
        "launcher_key": "wb-launcher-only"}), "wb-from-shell")
    check("有 --api-key 时它优先于 launcher_key",
          [a["key"] for a in st3["accepted"]] == ["wb-from-shell"]
          and st3["kind"] == "cli")

    st4 = lanshare.keys_state(accounts_dir_with("legacy", {
        "api_key": "wb-legacy", "api_key_set": True}), None)
    check("旧版单 Key（api_key + api_key_set）也认",
          [a["key"] for a in st4["accepted"]] == ["wb-legacy"])

    st5 = lanshare.keys_state(accounts_dir_with("disabled", {
        "auth_disabled": True, "launcher_key": "wb-x"}), None)
    check("内核关了 Key 校验时如实说（不假装有个 Key 要填）",
          st5["auth_disabled"] is True and st5["accepted"] == [])

    st6 = lanshare.keys_state(accounts_dir_with("empty", {}), None)
    check("什么都没有时：kind=none，并说明内核第一次 LAN 启动才生成",
          st6["kind"] == "none" and st6["accepted"] == []
          and "LAN" in st6["detail"])

    # ---------------- 6. 复制到剪贴板 ----------------
    section("6. 复制：外壳直接写剪贴板（浏览器那两条会丢用户手势）")
    # 用户报障"无法复制"的根因：`navigator.clipboard.writeText` 被 WebView2 拦下后，
    # 异步退回 `document.execCommand('copy')` 已经**没有用户手势**了 → 两条一起失败。
    # 所以外壳自己写剪贴板（ctypes），这里验它真的写进去了。
    #
    # 但**绝不毁用户的东西**：先看剪贴板里是什么——是文本就存一份、用完还回去；
    # 是空的就写、用完好清掉；是**图片/文件**那类没法还原的内容，就整段跳过。
    import clipboard as clipboard_mod
    had_text = clipboard_mod.has_text()
    was_empty = clipboard_mod.is_empty()
    before_text = clipboard_mod.get_text()[1] if had_text else ""
    savable = had_text or was_empty

    res_empty = window.api_copy({})
    check("空内容被拒绝（不会去动剪贴板）",
          res_empty.get("ok") is False, str(res_empty.get("message")))

    if not savable:
        print("    （剪贴板里是非文本内容，跳过「写了再还原」这一步"
              "——不毁用户的东西）")
        check("非文本剪贴板：确实没动它",
              clipboard_mod.has_text() is False
              and clipboard_mod.is_empty() is False)
    else:
        probe_text = "wb-shell-copy-test-%d" % os.getpid()
        res_copy = window.api_copy({"text": probe_text})
        got_ok, got_text = clipboard_mod.get_text()
        check("api_copy 报告成功", res_copy.get("ok") is True,
              str(res_copy.get("message")))
        check("剪贴板里**真的**是那段文字（不是嘴上说成功）",
              got_ok and got_text == probe_text,
              repr(got_text)[:40])
        check("日志里不写正文（可能是 API Key），只记长度",
              probe_text not in read_log(log_file))
        # 还原：原来是文本就写回原文，原来是空的就清干净
        if had_text:
            clipboard_mod.set_text(before_text)
            after_ok, after_text = clipboard_mod.get_text()
            check("测试不留痕迹：原内容已还回去",
                  after_ok and after_text == before_text)
        else:
            clipboard_mod.clear()
            check("测试不留痕迹：剪贴板已清回空",
                  clipboard_mod.get_text()[0] is False)

    # ---------------- 7. 只读保证 ----------------
    section("7. 只读保证：真配置 / 真内核数据一个字节都没动")
    check("真 shell.json 未变", sha256_file(REAL_SHELL_JSON) == real_shell_sha)
    check("真 accounts\\settings.json 未变（面板 Key 是只读来的）",
          sha256_file(REAL_ACCOUNTS_JSON) == real_accounts_sha)
    check("临时目录下确实产生了配置文件（证明上面确实在写盘）",
          os.path.isfile(os.path.join(shell4, "data", "shell.json")))

    # 日志里应能看到「已写入 shell.json」
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
            log_text = fh.read()
    except Exception:
        log_text = ""
    check("外壳日志里记下了这次改动（可事后追）",
          "局域网共享：开启（已写入 shell.json" in log_text)
    check("重启成功的日志也在",
          "局域网共享：内核重启成功" in log_text)

    print()
    print("  隔离根：%s" % root)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
