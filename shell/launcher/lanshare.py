# -*- coding: utf-8 -*-
"""lanshare.py — 「局域网共享」（LAN 模式）的外壳侧信息与配置写入。

**内核一个字节都不改**：本模块只做三件事——算本机内网地址、读当前 API Key
（只读 `<accounts>\\settings.json`）、把 `lan` 写回 `shell.json`（原子写，走
`config.save`）。真正的 LAN 行为全在内核里，这里只是把它讲清楚。

内核侧实现（2026-10-15 只读核实，供以后对照）：

* `--lan` → `--host 0.0.0.0`（`wb_proxy.py:_apply_cli_overrides`）；
* LAN 且没有显式 `--api-key` 时 → `wb_settings.ensure_launcher_key()`：
  生成 `wb-<token_urlsafe(24)>` 存进 `<accounts>\\settings.json` 的
  `launcher_key`，之后复用（**不落一个众所周知的默认 key**：网关花的是账号
  自己的额度）；
* **Key 的优先级**（`wb_proxy.py` 19240-19249）：
  `--api-key`（= 外壳 `shell.json` 的 `api_key`）> 看板里保存的 `api_key`
  （`api_key_set`）> 自动生成的 `launcher_key`；
* 启动 banner 打印本机所有内网 IP、每个 IP 的 API/看板地址、Key 与带 `?key=`
  的直链、防火墙提示——但**屏幕上不显示**（外壳抓了内核 stdout），只进
  `data\\core-<port>.log`，见交接文档 §6.14；
* **发现是纯手动的**：全仓库没有任何 UDP/组播/mDNS/SSDP。

所以外壳这一节的价值就一句话：**把只有日志里才有的那点信息搬到界面上**，
并且让"开/关"这件事不用再手编 JSON。
"""

import json
import os
import socket

# 连不上时多半是防火墙：内核目录里自带这个脚本（只读核实过它存在）。
FIREWALL_SCRIPT = "allow-firewall.bat"


def local_ipv4_addresses():
    """本机所有非回环 IPv4 地址（与内核 `local_ip_addresses()` 同一套办法）。

    两个来源：主机名解析，以及"UDP connect 一下问路由"的兜底（UDP connect
    不发包，只是让系统挑一个出口地址，所以不需要真的能上网）。
    """
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    if not found:
        probe = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            found.append(probe.getsockname()[0])
        except Exception:
            pass
        finally:
            try:
                if probe is not None:
                    probe.close()
            except Exception:
                pass
    return found


def accounts_settings_path(accounts_dir):
    return os.path.join(accounts_dir or "", "settings.json")


def read_accounts_settings(accounts_dir):
    """只读内核的 `accounts\\settings.json`。读不到就返回 {}（绝不抛）。"""
    path = accounts_settings_path(accounts_dir)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def panel_keys(data):
    """面板里配置的、**启用的** API Key（只读 `settings.json` 的 `api_keys`）。

    刻意不 import 内核的 `wb_settings`：它的 `api_keys()` 会把老的
    `api_key`/`api_key_set` **迁移**进列表并写盘——那是写内核目录，外壳不做。
    这里只按它最终的形状读，效果一样但不落一个字节。
    """
    out = []
    for entry in (data.get("api_keys") or []):
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled") or entry.get("deleted_at"):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        out.append({"key": key,
                    "label": str(entry.get("name") or entry.get("id") or "未命名"),
                    "realm": str(entry.get("realm") or ""),
                    "from": "panel"})
    return out


def keys_state(accounts_dir, cli_api_key=None):
    """别的设备**到底该填哪个 Key**——按内核真实的规矩算（不是按旧文档）。

    内核 `wb_proxy.identify_key()` 的规矩（2026-10-15 只读核实）：

        extra = () if configured_keys() else (API_KEY,)

    也就是说：**看板里只要有一个启用的 Key，那些 Key 就是唯一被接受的凭据**，
    `--api-key`（外壳的 `api_key`）与自动生成的 `launcher_key` 都会**被忽略**
    ——内核对这么做的解释是"别让 .bat 里留下的 key 在面板收紧之后还悄悄能用"。
    所以"该填哪个"必须按这个顺序算，否则界面会让人去填一个根本不认的 Key。

    返回 dict：auth_disabled / kind / accepted / others / source / detail /
    settings_file。
    """
    data = read_accounts_settings(accounts_dir)
    settings_file = accounts_settings_path(accounts_dir)
    cli_key = str(cli_api_key or "").strip()
    launcher_key = str(data.get("launcher_key") or "").strip()
    legacy_key = str(data.get("api_key") or "").strip()

    if data.get("auth_disabled") is True:
        return {"auth_disabled": True, "kind": "disabled", "accepted": [],
                "others": [],
                "source": "内核里关掉了 API Key 校验（auth_disabled）",
                "detail": "这种状态下 /v1 不需要 Key——局域网里谁都能调。"
                          "只在完全可信的网络里这么用。",
                "settings_file": settings_file}

    panel = panel_keys(data)
    if panel:
        others = []
        if cli_key:
            others.append("外壳设置里的 api_key（以 --api-key 传给内核）："
                          "**面板里已有 Key，它不再被接受**。")
        if launcher_key:
            others.append("内核自动生成的 launcher_key：同上，**不再被接受**"
                          "（内核是有意这样的，免得老 key 在面板收紧后还管用）。")
        return {"auth_disabled": False, "kind": "panel", "accepted": panel,
                "others": others,
                "source": "看板里配置的 API Key（%d 个可用）" % len(panel),
                "detail": "内核的规矩：**看板里一旦有启用的 Key，就只有这些 Key "
                          "被接受**（启动参数与自动生成的 launcher key 都会被忽略）。",
                "settings_file": settings_file}

    # 面板里没有启用的 Key：这时候才是 API_KEY 说了算
    # （--api-key > 看板保存过的单个 key > LAN 自动生成的 launcher_key）
    order = []
    if cli_key:
        order.append({"key": cli_key, "label": "启动参数 --api-key",
                      "realm": "", "from": "cli"})
    if data.get("api_key_set") and legacy_key:
        order.append({"key": legacy_key, "label": "看板保存过的单个 Key",
                      "realm": "", "from": "legacy_panel"})
    if launcher_key:
        order.append({"key": launcher_key, "label": "LAN 自动生成的 launcher_key",
                      "realm": "", "from": "launcher_key"})
    if order:
        first = order[0]
        return {"auth_disabled": False, "kind": first["from"],
                "accepted": order[:1], "others": [],
                "source": first["label"],
                "detail": {"cli": "外壳 shell.json 里的 api_key，由外壳以 --api-key "
                                  "传给内核（最高优先）。",
                           "legacy_panel": "看板里保存过的那个 Key（旧版单 Key 形态）。",
                           "launcher_key": "内核第一次以 LAN 模式启动时生成并保存的，"
                                           "之后一直复用。"}.get(first["from"], ""),
                "settings_file": settings_file}

    return {"auth_disabled": False, "kind": "none", "accepted": [], "others": [],
            "source": "还没有 Key",
            "detail": "内核**第一次以 LAN 模式启动**时才会生成一个（存进 "
                      "<accounts>\\settings.json 的 launcher_key）；"
                      "也可以先在内核看板里配一个 API Key。",
            "settings_file": settings_file}


def links(ips, port, dashboard_path="/", api_path="/v1"):
    """给别的设备用的地址表（带 key 的看板直链由调用方按需要拼）。"""
    out = []
    for ip in ips or []:
        out.append({
            "ip": ip,
            "api": "http://%s:%d%s" % (ip, port, api_path),
            "dashboard": "http://%s:%d%s" % (ip, port, dashboard_path),
        })
    return out


def dashboard_url_with_key(ip, port, key):
    if not key:
        return ""
    return "http://%s:%d/?key=%s" % (ip, port, key)


def reachable_ip(ips, port, timeout=0.8, probe=None):
    """逐个内网地址探一次 `/health`：返回**第一个真的答话**的地址。

    这是"局域网里别的设备到底连不连得上"的**端到端**证据——比只看配置里
    写着 lan=true 硬得多（防火墙会照样把包丢掉，而配置里看不出来）。
    全都探不通就返回空串。
    """
    if not ips:
        return ""
    if probe is None:
        try:
            import service as service_mod
            probe = service_mod.probe_health
        except Exception:
            return ""
    for ip in ips:
        try:
            info = probe(port, ip, timeout) or {}
        except Exception:
            continue
        if info.get("state") == "ours":
            return ip
    return ""


def firewall_hint(core_dir, port):
    """一句能照做的防火墙提示（脚本存在与否都说清楚）。"""
    script = os.path.join(core_dir or "", FIREWALL_SCRIPT)
    exists = os.path.isfile(script)
    if exists:
        return ("别的设备连不上，多半是 Windows 防火墙没放行：在内核目录里右键"
                "以管理员身份运行 %s（一次就够）；也可以手动给 Python 放行"
                "入站 TCP %d。" % (FIREWALL_SCRIPT, port))
    return ("别的设备连不上，多半是 Windows 防火墙没放行：给 Python 放行入站"
            " TCP %d 即可（内核目录里没有找到 %s，可能是这个版本的包里没带）。"
            % (port, FIREWALL_SCRIPT))
