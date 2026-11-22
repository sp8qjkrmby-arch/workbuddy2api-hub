# -*- coding: utf-8 -*-
"""config.py — 外壳设置：默认值、读写、校验、生效值合并。

设置住 `<shell>\\data\\shell.json`。设计上有三条自我约束：

1. **不认识的值一律保留**。读到旧版本写的、或用户手加的键，原样留在内存里，
   保存时写回去——升级外壳不该抹掉用户自己加的东西。
2. **坏值降级而不是报错**。端口写成 "abc" 就用默认端口并记一条警告；
   只有「内核目录不存在」这种致命项才拦启动。
3. **写盘是原子的**。临时文件 + os.replace，断电不会留下半个 JSON。

纯标准库，Python 3.9+。
"""

import json
import os
import shutil
import time

import paths

# --- 默认值 -----------------------------------------------------------------

DEFAULTS = {
    # 内核
    "core_dir": "",
    "port": 8788,
    "host": "127.0.0.1",
    "lan": False,
    "accounts_dir": None,
    "usage_dir": None,
    "api_key": None,
    "panel_password": None,
    "panel_password_remember": False,
    "auto_start_core": True,
    # 界面
    "ui": "auto",                 # auto | web | native
    "tray_enabled": True,
    "close_to_tray": True,
    "start_minimized": False,
    "autostart": False,
    "window_geometry": "",
    # 内核守护：内核意外退出时自动拉起（详见 service.WATCHDOG_BACKOFF）
    "core_watchdog": True,
    "core_watchdog_retries": 3,
    # 更新
    # D：启动时静默查一次（有新版本才提示）。2026-10-18 起默认开启——
    # 以前默认 False 且没有任何周期检查，等于"永远不知道有新版本"。
    "check_update_on_launch": True,
    # A：周期后台检查的间隔（小时）。0 = 关闭周期检查。
    # 真正联网的频率还受 updater.CHECK_MIN_INTERVAL 节流保护（连点也不会打爆限流）。
    "update_check_hours": 6,
    "repo": "sp8qjkrmby-arch/workbuddy2api-hub",
    "update_mirror": "",
    "keep_backups": 3,
    # 运行时
    "runtime_dir": "",
    # 外壳记账
    "config_version": 1,
}

UI_CHOICES = ("auto", "web", "native")
PORT_RANGE = (1, 65535)

# 面板密码与 API Key 绝不出现在日志 / 异常消息里。
SECRET_KEYS = ("panel_password", "api_key")


def default_config():
    return dict(DEFAULTS)


def config_file(shell_path=None):
    return os.path.join(paths.data_dir() if shell_path is None
                        else os.path.join(shell_path, paths.DATA_DIR_NAME),
                        "shell.json")


# --- 读 ---------------------------------------------------------------------

def _as_bool(value, fallback):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return fallback


def _as_int(value, fallback):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _as_opt_text(value):
    """可空字符串：空串与 null 都归一成 None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load(shell_path=None):
    """读设置。

    返回 dict：config / warnings / path / existed。
    `config` 里一定包含全部默认键；未知键原样保留在 `config["_extra"]`。
    """
    shell_path = shell_path or paths.shell_dir()
    path = config_file(shell_path)
    warnings = []
    existed = os.path.isfile(path)
    raw = {}
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                raw = loaded
            else:
                warnings.append("shell.json 不是对象，已按默认值处理")
        except Exception as exc:
            warnings.append("shell.json 读取失败（%s），已按默认值处理"
                            % type(exc).__name__)

    config = default_config()
    extra = {}
    for key, value in raw.items():
        if key in config:
            config[key] = value
        else:
            extra[key] = value
    config["_extra"] = extra

    _normalise(config, warnings)
    return {"config": config, "warnings": warnings, "path": path, "existed": existed}


def _normalise(config, warnings):
    """就地校验。坏值改回默认并记警告，绝不抛异常。"""
    if str(config.get("ui") or "").lower() not in UI_CHOICES:
        warnings.append("ui=%r 不是 %s 之一，已改用 auto"
                        % (config.get("ui"), "/".join(UI_CHOICES)))
        config["ui"] = "auto"
    else:
        config["ui"] = str(config["ui"]).lower()

    port = _as_int(config.get("port"), DEFAULTS["port"])
    if not (PORT_RANGE[0] <= port <= PORT_RANGE[1]):
        warnings.append("port=%r 超出范围，已改用 %d" % (config.get("port"),
                                                        DEFAULTS["port"]))
        port = DEFAULTS["port"]
    config["port"] = port

    host = str(config.get("host") or "").strip() or DEFAULTS["host"]
    config["host"] = host

    for key in ("lan", "panel_password_remember", "auto_start_core",
                "tray_enabled", "close_to_tray", "start_minimized",
                "autostart", "check_update_on_launch", "core_watchdog"):
        config[key] = _as_bool(config.get(key), DEFAULTS[key])

    retries = _as_int(config.get("core_watchdog_retries"),
                      DEFAULTS["core_watchdog_retries"])
    config["core_watchdog_retries"] = max(0, min(10, retries))

    # 周期检查间隔：0 = 关闭；上限一周（再大就等于没开）
    hours = _as_int(config.get("update_check_hours"),
                    DEFAULTS["update_check_hours"])
    config["update_check_hours"] = max(0, min(24 * 7, hours))

    keep = _as_int(config.get("keep_backups"), DEFAULTS["keep_backups"])
    config["keep_backups"] = max(1, min(20, keep))

    for key in ("accounts_dir", "usage_dir", "api_key", "panel_password",
                "core_dir", "runtime_dir", "update_mirror", "window_geometry"):
        config[key] = _as_opt_text(config.get(key))
    for key in ("core_dir", "update_mirror", "runtime_dir", "window_geometry"):
        if config[key] is None:
            config[key] = ""
    # 说明：**不要**在这里加 `last_core_version` / `last_updated_at`。
    # 「上次更新到哪个版本、什么时候更新的」由更新链路自己记在
    # `data\update-state.json`（字段 from_version / to_version / installed_at /
    # last_check），界面和托盘都读那一份。曾经 shell.json 里也有同名的两个键，
    # 但从来没有任何代码写它们（死键），2026-10-18 已删除——两处记账必然对不上。

    repo = str(config.get("repo") or "").strip() or DEFAULTS["repo"]
    if repo.count("/") != 1:
        warnings.append("repo=%r 不是 owner/name 形状，已改用默认仓库" % repo)
        repo = DEFAULTS["repo"]
    config["repo"] = repo


def merged_effective(config, shell_path=None, cli=None):
    """把命令行覆盖、bootstrap.json、内核目录解析合并成「生效配置」。

    返回 dict：config（已合并）/ core_dir / core_source / accounts_dir /
    usage_dir / warnings / fatal（致命问题的一句话，空串表示没问题）。
    """
    shell_path = shell_path or paths.shell_dir()
    cli = cli or {}
    warnings = list(config.get("_extra_warnings") or [])
    effective = {key: value for key, value in config.items() if not key.startswith("_")}

    boot = paths.read_bootstrap()
    for key in paths.BOOTSTRAP_KEYS:
        if key in boot and key not in cli:
            effective[key] = boot[key]
            warnings.append("bootstrap.json 提供了 %s" % key)
    # 命令行覆盖必须先进 effective，再交给发现逻辑——否则 --core-dir
    # 会在"外壳同级自动发现"这一步被顶掉，而它存在的意义正是发现猜错时的补救。
    for key in ("core_dir", "port", "host", "ui", "tray_enabled", "runtime_dir",
                "accounts_dir", "usage_dir", "lan"):
        if cli.get(key) is not None:
            effective[key] = cli[key]
    _normalise(effective, warnings)

    core_dir, core_source = paths.resolve_core_dir(effective.get("core_dir") or "",
                                                   shell_path)
    effective["core_dir"] = core_dir

    fatal = ""
    if not core_dir:
        fatal = ("没找到内核目录。请把内核（含 wb_proxy.py 的目录）放在外壳旁边，"
                 "或用 --core-dir 指定。")
    else:
        problem = paths.describe_core_problem(core_dir)
        if problem:
            fatal = problem

    accounts_dir = effective.get("accounts_dir") or (
        os.path.join(core_dir, "accounts") if core_dir else "")
    usage_dir = effective.get("usage_dir") or (
        os.path.join(core_dir, "usage") if core_dir else "")

    # 显式给了内核目录本身当数据目录，一定是填错了——那等于让更新去覆盖数据。
    for label, value in (("accounts_dir", accounts_dir), ("usage_dir", usage_dir)):
        if value and core_dir and os.path.abspath(value) == os.path.abspath(core_dir):
            fatal = ("%s 不能等于内核目录本身（%s）：更新会覆盖数据。"
                     % (label, core_dir))

    return {
        "config": effective,
        "core_dir": core_dir,
        "core_source": core_source,
        "accounts_dir": accounts_dir,
        "usage_dir": usage_dir,
        "warnings": warnings,
        "fatal": fatal,
    }


# --- 写 ---------------------------------------------------------------------

def save(config, shell_path=None):
    """原子写盘。返回 (ok, message)。"""
    shell_path = shell_path or paths.shell_dir()
    path = config_file(shell_path)
    payload = {key: value for key, value in config.items()
               if not key.startswith("_")}
    payload.pop("_extra", None)
    payload.update(config.get("_extra") or {})
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        temp = path + ".tmp-%d" % os.getpid()
        with open(temp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    except Exception as exc:
        return False, "保存失败：%s" % exc
    return True, path


def backup_before_overwrite(shell_path=None):
    """首次写配置前留一份原件（只在文件已存在、且当天还没备份过时做一次）。"""
    shell_path = shell_path or paths.shell_dir()
    path = config_file(shell_path)
    if not os.path.isfile(path):
        return ""
    folder = os.path.join(paths.data_dir() if shell_path is None
                          else os.path.join(shell_path, paths.DATA_DIR_NAME),
                          "config-backups")
    stamp = time.strftime("%Y%m%d")
    target = os.path.join(folder, "shell-%s.json" % stamp)
    if os.path.isfile(target):
        return target
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(path, target)
        return target
    except Exception:
        return ""


# --- 密钥安全 ---------------------------------------------------------------

def redact(mapping):
    """给日志用：把密钥类字段换成占位符。"""
    safe = {}
    for key, value in dict(mapping or {}).items():
        if key in SECRET_KEYS and value:
            safe[key] = "***"
        else:
            safe[key] = value
    return safe


def secret_fingerprint(value):
    """不泄露原文、又能判断「是不是同一个密钥」的短指纹。"""
    if not value:
        return "(未设置)"
    import hashlib
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return "sha256:%s…(%d 字符)" % (digest[:8], len(str(value)))
