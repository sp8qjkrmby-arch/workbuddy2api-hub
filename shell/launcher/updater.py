# -*- coding: utf-8 -*-
"""updater.py — 内核的更新：发现 → 下载 → 校验 → 备份 → 覆盖 → 自检 → 回滚。

这是本方案里唯一会**改动内核目录**的模块，所以规矩写得死一些：

1. **只认官方的发布资产**（`workbuddy2api-hub-vX.Y.Z.zip`）+ 官方 `SHA256SUMS`，
   校验不通过就整包丢弃，绝不"先覆盖后校验"。
2. **备份是硬门槛**。备份不成，后面一步都不做。
3. **保护名单**优先于覆盖：`accounts/`、`usage/`、`.git/`、`__pycache__/`、
   `data/`、`*.log` 一律不写——这是"原地保留数据"的技术保证，而不是靠约定。
4. **自检不过就回滚**，回滚也失败就把备份路径和手动命令交出来，绝不静默半覆盖。
5. 旧版已删除的文件**不自动删**：磁盘上多出来的东西无法与用户手改区分，
   只列出来让人自己决定。

版本号只在日志里出现；判断"新不新"只用 `parse_version` + 元组比较，
不用字符串比较（"1.10.0" < "1.9.0" 那种坑）。
"""

import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile

import logging_setup
import service as service_mod

API_BASE = "https://api.github.com"
RELEASES_PATH = "/repos/%s/releases"
LATEST_PAGE = "https://github.com/%s/releases/latest"
DOWNLOAD_BASE = "https://github.com/%s/releases/download/%s/%s"

ASSET_NAME = "workbuddy2api-hub-v%s.zip"
SUMS_NAME = "SHA256SUMS"
CHECKSUMS_NAME = "checksums.txt"
ARCHIVE_ROOT = "wb-proxy"
USER_AGENT = "wb-shell-updater"
REQUEST_TIMEOUT = 20
# 两次"联网检查"之间至少隔这么久。GitHub 对未认证请求限 60 次/小时，
# 而用户可能连点「检查更新」、也可能频繁重启外壳；周期检查也走这个节流。
# 15 分钟足够"刚查过就别再查"，又不至于让手动检查被拦得太死。
CHECK_MIN_INTERVAL = 15 * 60

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# 保护名单：匹配到的相对路径绝不覆盖。
# `python/` 与 `tests/` 也在内：官方发布包不含它们（前者是便携运行时，
# 后者是开发期资产），而用户本地往往留着——更新时把运行时连根换掉风险太高，
# 而 `python/` 被当成"新版已删除的旧文件"报出来只会制造噪音。
#
# `catalog/` 是**内核自己会写的运行时存档**，不是随包静态资产——内核源码里
# 写得很明确（`wb_proxy.CATALOG_CACHE_DIR`）：
#   "目录落盘存档：每次成功从上游 /v3/config 拉到清单就写一份响应体副本，
#    纯留档（网关自己不读它）。与 usage 一样运行时产生……不参与提交与更新"
# 所以它和 `usage/` 同类：更新时不该覆盖它。附带好处是**只读基准不会再被它污染**
# ——2026-10-15 实测：录完基准 20 分钟后，内核自己刷新了
# `catalog/product-config-cn.json`，基准于是报出 1 处"不一致"，看着像外壳改了内核。
PROTECTED_DIRS = ("accounts", "usage", "catalog", ".git", "__pycache__", "data",
                  "tests", "python")
PROTECTED_SUFFIXES = (".log", ".pyc", ".pyo")
# 覆盖前必须先确认存在的核心文件（防止把半个包铺上去）。
REQUIRED_FILES = ("wb_proxy.py", "dashboard.html")


class UpdateError(RuntimeError):
    """更新过程中的可展示错误。消息面向用户，不含堆栈。"""


# --- 版本 -------------------------------------------------------------------

def parse_version(text):
    match = VERSION_RE.match(str(text or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_newer(candidate, current):
    left, right = parse_version(candidate), parse_version(current)
    return bool(left and right and left > right)


def version_text(triple):
    return "%d.%d.%d" % triple


# --- 网络 -------------------------------------------------------------------

def _opener():
    """带固定 UA 的 opener。更新源是公网，允许走系统代理。"""
    return urllib.request.build_opener()


def _request(url, headers=None, timeout=REQUEST_TIMEOUT):
    merged = {"User-Agent": USER_AGENT}
    merged.update(headers or {})
    token = str(os.environ.get("GITHUB_TOKEN") or "").strip()
    if token and "api.github.com" in url:
        merged["Authorization"] = "Bearer " + token
    return urllib.request.Request(url, headers=merged)


def fetch_releases(repo, timeout=REQUEST_TIMEOUT):
    """拉 releases 列表。失败抛 UpdateError（消息短、可展示）。"""
    url = API_BASE + RELEASES_PATH % repo
    try:
        with _opener().open(_request(url, {"Accept": "application/vnd.github+json"}),
                            timeout=timeout) as response:
            payload = response.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise UpdateError("查询发布列表失败：HTTP %d" % exc.code)
    except Exception as exc:
        raise UpdateError("查询发布列表失败：%s" % type(exc).__name__)
    try:
        document = json.loads(payload)
    except Exception:
        raise UpdateError("发布列表不是合法 JSON")
    if not isinstance(document, list):
        raise UpdateError("发布列表格式异常")
    return document


def fetch_latest_by_redirect(repo, timeout=REQUEST_TIMEOUT):
    """API 不可用时的退路：/releases/latest 的 302 Location 末尾就是 tag。"""
    url = LATEST_PAGE % repo
    try:
        with _opener().open(_request(url), timeout=timeout) as response:
            final = response.geturl() or ""
    except Exception as exc:
        raise UpdateError("回退查询也失败：%s" % type(exc).__name__)
    match = re.search(r"/releases/tag/([^/?#]+)", final)
    if not match:
        raise UpdateError("回退查询拿不到 tag")
    return {"tag_name": match.group(1), "draft": False, "prerelease": False,
            "html_url": final, "published_at": "", "body": "", "assets": []}


def pick_latest(document):
    """从 releases 里挑版本号最大的稳定版。返回 (version_text, entry) 或 (None, None)。"""
    best = None
    for entry in document if isinstance(document, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("draft") or entry.get("prerelease"):
            continue
        triple = parse_version(entry.get("tag_name"))
        if triple is None:
            continue
        if best is None or triple > best[0]:
            best = (triple, entry)
    if best is None:
        return None, None
    return version_text(best[0]), best[1]


def asset_url(entry, tag, name, mirror=""):
    base = DOWNLOAD_BASE % (entry.get("_repo", ""), tag, name)
    if mirror:
        return mirror.rstrip("/") + "/" + base
    return base


def find_asset(entry, name):
    for asset in entry.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name:
            return asset
    return None


def sha_from_api(entry, asset_name):
    """发布元数据里自带的 `digest`（形如 sha256:<hex>）。

    GitHub 的 releases 接口会给每个资产一个内容摘要，它与同版本 `SHA256SUMS`
    里那一行**逐字符相同**（已核对）。所以当 release 资产的直链拉不动时——国内
    网络下 github.com 抖动是常态，而校验文件只有几百字节、最容易先被 RST——
    这条同样来自官方的来源可以顶上，不必降级成"没有校验值就拒绝安装"。
    """
    asset = find_asset(entry, asset_name)
    if not asset:
        return ""
    digest = str(asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


# --- 下载与校验 -------------------------------------------------------------

def _download(url, target, size_hint=0, progress=None, timeout=60, attempts=4,
              backoff=2.0):
    """下载到 target（调用方保证 target 是 .part 之类的中转名）。

    两条硬要求，都来自真实故障（2026-10-14：连续两次更新都报"校验失败"，
    实际是直链把 9.9 MB 的包分别在 4.7 MB / 2.8 MB 处切断了，而这里把半包
    当成了下载成功，于是错误只能拖到 SHA256 那一步、变成一句指不到原因的
    "校验失败"）：

    1. **核对字节数**：拿到的比期望少就**不算成功**——截断必须在这一步就报出来；
    2. **断点续传**：中断后带 `Range` 从断点接着要（GitHub 直链实测返回 206），
       一次抖动只损失没传到的那一小段，而不是整包重来。
    """
    headers = {"Accept": "application/octet-stream"}
    total = int(size_hint or 0)
    done = 0
    last = ""
    rounds = max(1, int(attempts))
    for attempt in range(1, rounds + 1):
        request_headers = dict(headers)
        if done:
            request_headers["Range"] = "bytes=%d-" % done
        try:
            with _opener().open(_request(url, request_headers),
                                timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                if done and status != 206:
                    # 服务器不认续传（返回整包）：从头写，别把两段拼起来。
                    logging_setup.log("下载续传不被支持（HTTP %s），改为整包重下"
                                      % status, "WARN")
                    done = 0
                length = int(response.headers.get("Content-Length") or 0)
                if length:
                    total = (done + length) if done else max(total, length)
                with open(target, "ab" if done else "wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            try:
                                progress(done, total)
                            except Exception:
                                pass
        except urllib.error.HTTPError as exc:
            last = "HTTP %d" % exc.code
        except Exception as exc:
            last = "%s: %s" % (type(exc).__name__, exc)

        if total and done >= total:
            return target
        if not total and done:
            # 体积未知（既没给 size_hint 也没有 Content-Length）：只能交给
            # 调用方的 SHA256 去判——那种情况下"读完了"就是唯一依据。
            return target
        if attempt < rounds:
            wait = min(backoff * attempt, 6.0)
            logging_setup.log(
                "下载中断（已拿到 %d/%s 字节，%s），%.1f 秒后从断点续传（第 %d/%d 次）"
                % (done, total or "?", last or "连接提前结束", wait,
                   attempt + 1, rounds), "WARN")
            time.sleep(wait)

    if total:
        raise UpdateError(
            "下载不完整：只拿到 %.1f MB / %.1f MB（含断点续传共尝试 %d 次）。"
            "多半是网络到 GitHub 的直链被中途切断——稍后重试，"
            "或在设置里填 update_mirror 换一条通路。%s"
            % (done / 1048576.0, total / 1048576.0, rounds,
               ("最后错误：" + last) if last else ""))
    raise UpdateError("下载失败：%s（%s）" % (last or "连接提前结束", url))


def sha256_file(path, progress=None):
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            if progress:
                try:
                    progress(done)
                except Exception:
                    pass
    return digest.hexdigest()


def parse_sums(text):
    """解析 SHA256SUMS / checksums.txt：`<hex>  <name>`，忽略注释与坏行。"""
    table = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("*", " ").split()
        if len(parts) < 2:
            continue
        digest, name = parts[0].lower(), parts[-1].lstrip("./")
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            table[name] = digest
    return table


def fetch_expected_sha(repo, tag, asset_name, mirror="", timeout=REQUEST_TIMEOUT):
    """取官方校验值。两个来源都试，任一命中即可；都不行返回 (None, 原因)。"""
    reasons = []
    for name in (SUMS_NAME, CHECKSUMS_NAME):
        url = DOWNLOAD_BASE % (repo, tag, name)
        if mirror:
            url = mirror.rstrip("/") + "/" + url
        try:
            with _opener().open(_request(url), timeout=timeout) as response:
                table = parse_sums(response.read(256 * 1024).decode("utf-8", "replace"))
        except Exception as exc:
            reasons.append("%s: %s" % (name, type(exc).__name__))
            continue
        if asset_name in table:
            return table[asset_name], ""
        reasons.append("%s 里没有 %s 的条目" % (name, asset_name))
    return None, "；".join(reasons)


# --- 安全解压 ---------------------------------------------------------------

def safe_extract_entries(zip_path):
    """列出 zip 里可安全解出的条目：相对路径、无 ..、无绝对路径、无符号链接。

    返回 (entries, skipped)：entries 是 [(ZipInfo, 目标相对路径)]，路径已剥掉
    顶层的 `wb-proxy/`。
    """
    entries = []
    skipped = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                skipped.append("%s（绝对路径）" % name)
                continue
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if any(part == ".." for part in parts):
                skipped.append("%s（含 ..）" % name)
                continue
            # 符号链接：外部属性高 16 位是 Unix 模式，0xA000 表示链接。
            mode = (info.external_attr >> 16) & 0xF000
            if mode == 0xA000:
                skipped.append("%s（符号链接）" % name)
                continue
            if not parts:
                continue
            if parts[0] == ARCHIVE_ROOT:
                parts = parts[1:]
            if not parts:
                continue
            entries.append((info, "/".join(parts)))
    return entries, skipped


def extract_to(zip_path, target_dir):
    """把 zip 解到 target_dir（自动剥掉顶层 wb-proxy/）。返回 (文件数, 跳过清单)。"""
    entries, skipped = safe_extract_entries(zip_path)
    if not entries:
        raise UpdateError("压缩包里没有可用文件（顶层应为 %s/）" % ARCHIVE_ROOT)
    root = os.path.abspath(target_dir)
    written = 0
    with zipfile.ZipFile(zip_path) as archive:
        for info, relative in entries:
            if info.is_dir():
                continue
            destination = os.path.abspath(os.path.join(root, relative))
            if not destination.startswith(root + os.sep):
                skipped.append("%s（解出后越界）" % relative)
                continue
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as handle:
                shutil.copyfileobj(source, handle)
            written += 1
    return written, skipped


# --- 保护名单 ---------------------------------------------------------------

def is_protected(relative):
    """这个相对路径能不能被覆盖。True = 不能。"""
    parts = relative.replace("\\", "/").split("/")
    if not parts:
        return True
    if parts[0] in PROTECTED_DIRS:
        return True
    if relative.lower().endswith(PROTECTED_SUFFIXES):
        return True
    return False


def core_manifest(core_dir, skip_protected=True):
    """内核目录的 {相对路径: sha256} 清单，用于备份后校验与差异报告。"""
    manifest = {}
    for root, dirs, files in os.walk(core_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            full = os.path.join(root, name)
            relative = os.path.relpath(full, core_dir).replace("\\", "/")
            if skip_protected and is_protected(relative):
                continue
            try:
                manifest[relative] = sha256_file(full)
            except Exception:
                manifest[relative] = ""
    return manifest


# --- 更新器 -----------------------------------------------------------------

def write_baseline(core_dir, version, data_dir, note="", logger=None):
    """重写内核只读基准 `data\\baseline-core-sha256.txt`。

    与 `tests/snapshot_core.py` 是同一件事——区别只是那次靠人手工跑。升级成功后由
    `install()` 自动调用：**内核被合法换过，基准就必须跟着换**，否则下一次体检会把
    "升级"报成"外壳改了内核"。2026-10-15 一天之内真的发生了两次（1.7.5 报 5 处、
    1.7.6 报 3 处），而那份基准的用途恰恰是"证明外壳没碰过内核"——它一过期，
    每次比对都在误报（交接文档自己写着"留着过期基准比没有基准更危险"）。

    旧基准归档到 `data\\config-backups\\baseline-core-sha256.<时间戳>.txt`。
    返回 (ok, message)：任何失败都只意味着"基准没重录成功"，
    **绝不影响这次更新本身**（调用方只记一行）。
    """
    try:
        manifest = core_manifest(core_dir, skip_protected=True)
    except Exception as exc:
        return False, "重录基准失败（读不到内核清单）：%s" % exc
    if not manifest:
        return False, "重录基准失败（清单是空的，内核目录不对？）"

    path = os.path.join(data_dir, "baseline-core-sha256.txt")
    if os.path.isfile(path):
        archive = os.path.join(data_dir, "config-backups",
                               "baseline-core-sha256.%s.txt"
                               % time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.makedirs(os.path.dirname(archive), exist_ok=True)
            shutil.copy2(path, archive)
        except Exception as exc:
            if logger:
                logger("旧基准归档失败（继续重录）：%r" % (exc,), "WARN")

    lines = ["# core_dir: %s" % core_dir,
             "# version : %s" % (version or "unknown"),
             "# created : %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "# note    : %s" % (note or "外壳只读基准；用于证明外壳未修改内核")]
    for relative in sorted(manifest):
        lines.append("%s  %s" % (manifest[relative], relative))
    temp = path + ".tmp"
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception as exc:
        try:
            if os.path.isfile(temp):
                os.remove(temp)
        except Exception:
            pass
        return False, "重录基准失败：%s" % exc
    return True, "基准已按 %s 重录（%d 个文件）" % (version or "新版", len(manifest))


def install_blocked_reason(plan, service=None):
    """这次为什么不能装。能装就返回空串。

    给界面用：**在用户点按钮之前**就说清，而不是让他下完几十 MB 再被拒绝。
    这里的判断必须与 `Updater.install()` 的前置检查一致（同一个说法、同一处真相），
    `tests/update_isolated_test.py` 里对两侧都做了断言。
    """
    if service is not None:
        try:
            state = service.status()["state"]
        except Exception:
            state = ""
        if state == service_mod.EXTERNAL:
            return ("内核是外部启动的（不是本外壳拉起的），外壳无法安全停止它，"
                    "因此不能覆盖文件。请先从启动它的那个窗口停止内核。")
        # 这两种状态下外壳**没有可停的内核**，install() 会直接要求"端口已释放"
        # （`wait_port_released`）。那就把端口检查提到前面来：否则界面会给出
        # 一个按下去必定失败的按钮，用户只能从那句"停止后端口仍被占用"里反推原因。
        if state in (service_mod.STOPPED, service_mod.FAILED):
            try:
                health = service_mod.probe_health(service.port, service.host,
                                                  timeout=2.0)
            except Exception:
                health = {}
            kind = health.get("state")
            if kind == "ours":
                return ("端口 %s 上还有一个内核在运行，但它不是本外壳启动的，"
                        "外壳无法安全停止它。请先在那个窗口停止内核，再更新。"
                        % service.port)
            if kind == "foreign":
                return ("端口 %s 被别的程序占用了（%s）；覆盖内核文件前必须先释放它。"
                        % (service.port, health.get("message") or "非本内核的服务"))
    if not plan.asset_name:
        return "该版本没有可安装的资产包，无法自动安装"
    if not plan.expected_sha:
        return ("取不到官方 SHA256 校验值，外壳拒绝安装"
                "（可稍后重试；国内网络下直链会抖动）")
    return ""


class UpdatePlan(object):
    """一次更新的全部上下文。UI 只需要读它。"""

    def __init__(self):
        self.current_version = ""
        self.latest_version = ""
        self.tag = ""
        self.release_url = ""
        self.published_at = ""
        self.changelog = ""
        self.asset_name = ""
        self.asset_size = 0
        self.expected_sha = ""
        self.sha_source = ""
        self.mirror = ""
        self.notes = []

    @property
    def available(self):
        return bool(self.latest_version) and is_newer(self.latest_version,
                                                      self.current_version)

    def as_dict(self):
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "available": self.available,
            "tag": self.tag,
            "release_url": self.release_url,
            "published_at": self.published_at,
            "asset_name": self.asset_name,
            "asset_size": self.asset_size,
            "expected_sha": self.expected_sha,
            "sha_source": self.sha_source,
            "changelog": self.changelog,
            "notes": list(self.notes),
        }


class Updater(object):
    """发现 + 安装。所有会动内核目录的动作都能被 UI 明确触发并观察。"""

    def __init__(self, effective, service=None, shell_path=None):
        config = effective["config"]
        self.config = config
        self.service = service
        self.core_dir = effective["core_dir"]
        self.repo = config.get("repo") or "ardeyouxipianyi/workbuddy2api-hub"
        self.mirror = (config.get("update_mirror") or "").strip()
        self.keep_backups = int(config.get("keep_backups") or 3)
        from paths import data_dir, DATA_DIR_NAME
        # 账本目录：**优先按调用方给的外壳路径算**，只有没给时才退回
        # `paths.data_dir()`（按模块位置推导）。以前一律用后者，于是
        # "传了 shell_path 也不生效"——测试想隔离账本就必须再手工改三个属性，
        # 忘一个就会写到真实外壳里（2026-10-18 踩过：把工作区账本写脏了）。
        base = (os.path.join(os.path.abspath(shell_path), DATA_DIR_NAME)
                if shell_path else data_dir())
        self.data_dir = base
        self.state_path = os.path.join(self.data_dir, "update-state.json")
        self.staging = os.path.join(self.data_dir, "staging")
        self.backups = os.path.join(self.data_dir, "backups")

    # ---- 状态落盘 ---------------------------------------------------------

    def read_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    # ---- 检查节流（2026-10-18 起）----------------------------------------
    #
    # 以前「检查更新」没有任何节流：连点 10 次就联网 10 次。GitHub 对未认证
    # 请求限 60 次/小时，而周期检查（见 __main__.schedule_periodic_update_check）
    # 也需要一个"最近刚查过就别再查"的判据。这里把"多久查一次算合理"集中成
    # 一个方法，界面、托盘、周期任务都走它。

    def seconds_since_last_check(self):
        """距上次成功检查过了多少秒；没有记录或读不出来时返回 None。"""
        state = self.read_state()
        stamp = str(state.get("last_check") or "").strip()
        if not stamp:
            return None
        try:
            parsed = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            return None
        return max(0.0, time.time() - parsed)

    def check_is_fresh(self, min_interval):
        """上次检查是否足够新（新到不必再联网）。"""
        if min_interval <= 0:
            return False
        age = self.seconds_since_last_check()
        return age is not None and age < min_interval

    def cached_plan(self):
        """不上网，用上次检查存下的状态拼一个最小 plan（给周期任务/重启用）。

        只在"状态文件里说 latest_version 比当前新"时返回它；否则返回 None。
        这样"记住有新版本"这件事不需要额外存储，直接复用 update-state.json。
        """
        state = self.read_state()
        latest = str(state.get("latest_version") or "").strip()
        current = self.current_version()
        if not latest or not is_newer(latest, current):
            return None
        plan = UpdatePlan()
        plan.current_version = current
        plan.latest_version = latest
        plan.tag = str(state.get("tag") or ("v" + latest)).strip()
        plan.release_url = str(state.get("release_url") or "")
        plan.asset_name = str(state.get("asset") or "")
        plan.expected_sha = str(state.get("asset_sha256") or "")
        try:
            plan.asset_size = int(state.get("asset_size") or 0)
        except Exception:
            plan.asset_size = 0
        plan.mirror = self.mirror
        plan.sha_source = "上次检查的记录（未重新联网）"
        plan.notes.append("这是缓存的检查结果")
        return plan

    def mark_notified(self, version):
        """记下"这个版本已经提示过用户了"，避免周期任务反复打扰。"""
        try:
            self._write_state({"notified_version": str(version or "")})
        except Exception:
            pass

    def was_notified(self, version):
        """这个版本是否已经提示过。"""
        state = self.read_state()
        return str(state.get("notified_version") or "") == str(version or "")

    def _write_state(self, patch):
        state = self.read_state()
        state.update(patch)
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            temp = self.state_path + ".tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.state_path)
        except Exception as exc:
            logging_setup.log("更新状态写盘失败：%r" % (exc,), "WARN")

    # ---- 发现 -------------------------------------------------------------

    def current_version(self):
        """运行中优先问 /health；否则读内核源码里的版本串。"""
        if self.service is not None:
            status = self.service.status()
            version = status.get("core_version") or ""
            if version:
                return version
        return service_mod.read_core_version(self.core_dir)

    def check(self, timeout=REQUEST_TIMEOUT):
        """查最新版。网络失败抛 UpdateError；返回 UpdatePlan。"""
        plan = UpdatePlan()
        plan.current_version = self.current_version()
        plan.mirror = self.mirror

        document = None
        first_error = None
        try:
            document = fetch_releases(self.repo, timeout=timeout)
        except UpdateError as exc:
            first_error = exc
            logging_setup.log("发布列表查询失败：%s，尝试回退端点" % exc, "WARN")
        if document is None:
            try:
                document = [fetch_latest_by_redirect(self.repo, timeout=timeout)]
                plan.notes.append("GitHub API 不可用，已用 releases/latest 回退查询")
            except UpdateError as exc:
                raise UpdateError("检查更新失败：%s / %s" % (first_error, exc))

        for entry in document:
            if isinstance(entry, dict):
                entry.setdefault("_repo", self.repo)
        latest, entry = pick_latest(document)
        if not latest or entry is None:
            plan.notes.append("没有找到规范的稳定版本号")
            return plan

        plan.latest_version = latest
        plan.tag = str(entry.get("tag_name") or "").strip()
        plan.release_url = str(entry.get("html_url") or "")
        plan.published_at = str(entry.get("published_at") or "")
        body = str(entry.get("body") or "")
        plan.changelog = body[:4000]
        plan.asset_name = ASSET_NAME % latest

        asset = find_asset(entry, plan.asset_name)
        if asset:
            plan.asset_size = int(asset.get("size") or 0)
        else:
            plan.notes.append("该版本没有 %s 资产，不能自动安装" % plan.asset_name)

        if plan.available:
            api_sha = sha_from_api(entry, plan.asset_name)
            if self.mirror and api_sha:
                # 配了镜像时**校验值必须来自官方 API**：镜像同样能改 SHA256SUMS，
                # 若连校验值也从镜像取，"官方 SHA256 校验"就名存实亡了。
                # releases 的 digest 字段来自 api.github.com（不走镜像）。
                plan.expected_sha = api_sha
                plan.sha_source = "官方 releases 元数据 digest（镜像只用于下载包）"
                plan.notes.append("已配置镜像：校验值取自官方 API，不取镜像提供的 %s"
                                  % SUMS_NAME)
            else:
                expected, reason = fetch_expected_sha(self.repo, plan.tag,
                                                      plan.asset_name, self.mirror,
                                                      timeout=timeout)
                if expected:
                    plan.expected_sha = expected
                    if self.mirror:
                        plan.sha_source = "镜像提供的 %s" % SUMS_NAME
                        plan.notes.append("校验值也来自镜像（官方 API 摘要取不到）——"
                                          "镜像能同时改包和校验值，请谨慎")
                    else:
                        plan.sha_source = "官方 %s" % SUMS_NAME
                elif api_sha:
                    # 直链抖动时用发布元数据里的官方 digest 顶上。
                    plan.expected_sha = api_sha
                    plan.sha_source = "官方 releases 元数据 digest（%s 取不到）" % SUMS_NAME
                    plan.notes.append("校验文件取不到，已改用 API 元数据里的官方摘要")
                else:
                    plan.notes.append("取不到官方校验值（%s）——安装时会被拒绝" % reason)
        self._write_state({"last_check": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "latest_version": plan.latest_version,
                           "current_version": plan.current_version,
                           # 多存几个字段，是为了让 cached_plan() 能在**不联网**时
                           # 拼出一份够用的 plan（周期提醒、重启后恢复"有新版"标记）。
                           "tag": plan.tag,
                           "release_url": plan.release_url,
                           "asset": plan.asset_name,
                           "asset_sha256": plan.expected_sha,
                           "asset_size": plan.asset_size})
        return plan

    # ---- 备份 -------------------------------------------------------------

    def make_backup(self, version, progress=None):
        """把内核目录（排除保护名单）压成备份 zip + manifest。返回 (zip 路径, manifest)。"""
        tag = re.sub(r"[^0-9A-Za-z._-]", "_", version or "unknown")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.makedirs(self.backups, exist_ok=True)
        archive_path = os.path.join(self.backups, "core-%s-%s.zip" % (tag, stamp))
        manifest = core_manifest(self.core_dir)
        try:
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for index, relative in enumerate(sorted(manifest)):
                    full = os.path.join(self.core_dir, relative.replace("/", os.sep))
                    archive.write(full, relative)
                    if progress:
                        try:
                            progress(index + 1, len(manifest))
                        except Exception:
                            pass
                archive.writestr("__manifest__.json", json.dumps(
                    {"version": version, "created": stamp, "files": manifest},
                    ensure_ascii=False, indent=1))
        except Exception as exc:
            raise UpdateError("备份失败：%s" % exc)
        if not os.path.isfile(archive_path) or os.path.getsize(archive_path) == 0:
            raise UpdateError("备份文件为空，已中止更新")
        self._prune_backups()
        return archive_path, manifest

    def _prune_backups(self):
        try:
            items = []
            for name in os.listdir(self.backups):
                if name.startswith("core-") and name.endswith(".zip"):
                    full = os.path.join(self.backups, name)
                    items.append((os.path.getmtime(full), full))
            items.sort(reverse=True)
            for _, full in items[self.keep_backups:]:
                try:
                    os.remove(full)
                except Exception:
                    pass
        except Exception:
            pass

    def list_backups(self):
        items = []
        try:
            for name in os.listdir(self.backups):
                if name.startswith("core-") and name.endswith(".zip"):
                    full = os.path.join(self.backups, name)
                    items.append({"name": name, "path": full,
                                  "size": os.path.getsize(full),
                                  "mtime": time.strftime(
                                      "%Y-%m-%d %H:%M:%S",
                                      time.localtime(os.path.getmtime(full)))})
        except Exception:
            pass
        items.sort(key=lambda item: item["mtime"], reverse=True)
        return items

    # ---- 安装 -------------------------------------------------------------

    def install(self, plan, progress=None, stop_core=True, start_core=True):
        """执行一次完整更新。返回 dict：ok / message / steps / rolled_back。

        `progress` 形如 fn(stage, done, total, text)。
        """
        steps = []

        def note(text, level="INFO"):
            steps.append(text)
            logging_setup.log("[更新] " + text, level)

        def emit(stage, done=0, total=0, text=""):
            if progress:
                try:
                    progress(stage, done, total, text)
                except Exception:
                    pass

        if not plan.asset_name:
            return {"ok": False, "message": "没有可安装的资产", "steps": steps,
                    "rolled_back": False}
        if not plan.expected_sha:
            return {"ok": False,
                    "message": "没有官方 SHA256 校验值，拒绝安装（可先点「检查更新」重试）",
                    "steps": steps, "rolled_back": False}
        problem = self._verify_core_dir()
        if problem:
            return {"ok": False, "message": problem, "steps": steps,
                    "rolled_back": False}

        os.makedirs(self.staging, exist_ok=True)
        archive_path = os.path.join(self.staging, "%s.zip" % plan.tag)
        part_path = archive_path + ".part"

        # 0) 先做**不需要停内核**的检查：外部启动的内核没法安全停，那就不该
        #    下完几十 MB 才被拒。
        if stop_core and self.service is not None:
            if self.service.status()["state"] == service_mod.EXTERNAL:
                return {"ok": False, "rolled_back": False, "steps": steps,
                        "message": "内核是外部启动的，外壳无法安全停止它，更新已中止。"
                                   "请先从启动它的那个窗口停止内核，再重试。"}

        # 1) 下载 + 校验。**这两步在停内核之前做**：它们只碰 data\staging，
        #    不需要内核让路。于是——下载失败时内核压根没停（2026-10-14 那两次
        #    失败各白停了内核一次），成功时内核也只需在备份/覆盖那几秒下线，
        #    而不是整段下载时间（9.9 MB 实测要 60~90 秒）。
        stopped = False
        emit("download", 0, plan.asset_size, "正在下载 %s" % plan.asset_name)
        url = asset_url({"_repo": self.repo}, plan.tag, plan.asset_name, self.mirror)
        try:
            _download(url, part_path, plan.asset_size,
                      progress=lambda done, total: emit("download", done, total,
                                                        "正在下载"))
        except UpdateError as exc:
            self._cleanup(part_path)
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": str(exc)}
        os.replace(part_path, archive_path)
        size = os.path.getsize(archive_path)
        note("已下载 %s（%.1f MB）" % (plan.asset_name, size / 1048576.0))

        emit("verify", 0, size, "正在校验 SHA256")
        actual = sha256_file(archive_path)
        note("SHA256：%s" % actual)
        if actual.lower() != plan.expected_sha.lower():
            # 把体积一起报出来：**体积对不上 = 下载被切断**（那种情况现在会在
            # _download 里就被拦住，走不到这里）；体积对得上但哈希不对才更像
            # "内容不是官方那一份"。这两种问题的处置完全不同，所以要说清楚。
            hint = ""
            if plan.asset_size and size == int(plan.asset_size):
                hint = ("\n体积与官方一致但内容不同——可能是上游替换了包，"
                        "或下载通路改写了内容。")
            elif plan.asset_size:
                hint = ("\n体积也不对（期望 %.1f MB）——多半是下载不完整。"
                        % (int(plan.asset_size) / 1048576.0))
            self._cleanup(archive_path)
            self._restart_if_needed(stopped, start_core, note)
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": "SHA256 校验不通过，已丢弃下载包并保持现状。\n"
                               "包体积 %.1f MB\n期望 %s\n实际 %s%s"
                               % (size / 1048576.0, plan.expected_sha, actual,
                                  hint)}
        note("SHA256 校验通过（来源：%s）" % (plan.sha_source or "官方"))

        # 2) 解压到暂存区（同样只碰 staging，内核仍在服务）
        emit("extract", 0, 0, "正在解压")
        extract_dir = os.path.join(self.staging, plan.tag)
        try:
            if os.path.isdir(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            written, skipped = extract_to(archive_path, extract_dir)
        except UpdateError as exc:
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": str(exc)}
        note("解压 %d 个文件（跳过 %d 条不安全条目）" % (written, len(skipped)))
        for item in skipped[:10]:
            note("  跳过：%s" % item, "WARN")
        missing = [name for name in REQUIRED_FILES
                   if not os.path.isfile(os.path.join(extract_dir, name))]
        if missing:
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": "新包缺少必需文件：%s，已中止" % "、".join(missing)}

        # 3) 停内核：**到这里才需要内核让路**（Windows 上换 .py 必须先释放文件
        #    占用）。前面下载/校验/解压都只碰 data\staging，所以内核直到现在
        #    才下线——实测下载要 60~90 秒，这一段本来完全不必停内核。
        if stop_core and self.service is not None:
            state = self.service.status()["state"]
            if state not in (service_mod.STOPPED,):
                note("停止内核以便安全覆盖文件…")
                ok, message = self.service.stop()
                note("停止内核：%s（%s）" % ("成功" if ok else "失败", message),
                     "INFO" if ok else "ERROR")
                if not ok:
                    return {"ok": False, "rolled_back": False, "steps": steps,
                            "message": "无法停止内核，更新中止：%s" % message}
                stopped = True
            released, detail = self.service.wait_port_released(timeout=10)
            if not released:
                return {"ok": False, "rolled_back": False, "steps": steps,
                        "message": "停止后端口仍被占用（%s），更新中止" % detail}
            if stopped:
                # 端口关闭 ≠ 文件句柄全放。留一拍让 Windows 回收句柄、
                # 让杀软收工，避免覆盖阶段撞 WinError 32。
                time.sleep(0.6)

        # 4) 备份（硬门槛）
        emit("backup", 0, 0, "正在备份当前内核")
        try:
            backup_path, manifest = self.make_backup(plan.current_version)
        except UpdateError as exc:
            self._restart_if_needed(stopped, start_core, note)
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": str(exc)}
        note("已备份 %d 个文件到 %s" % (len(manifest), os.path.basename(backup_path)))

        # 5) 覆盖
        emit("overlay", 0, 0, "正在覆盖内核文件")
        try:
            overwritten, added, protected_hits = self._overlay(extract_dir)
        except UpdateError as exc:
            note("覆盖失败：%s" % exc, "ERROR")
            emit("rollback", 0, 0, "覆盖失败，正在回滚")
            rollback_ok, rollback_message = self._restore_backup(backup_path, manifest)
            note("回滚：%s" % ("成功" if rollback_ok else "失败——" + rollback_message),
                 "INFO" if rollback_ok else "ERROR")
            if rollback_ok:
                self._restart_if_needed(stopped, start_core, note)
            self._write_state({"result": "rolled_back" if rollback_ok else "rollback_failed",
                               "from_version": plan.current_version,
                               "to_version": plan.latest_version,
                               "backup": backup_path,
                               "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            if rollback_ok:
                return {"ok": False, "rolled_back": True, "steps": steps,
                        "message": "覆盖内核文件时失败，已自动回滚到 %s。\n%s"
                                   % (plan.current_version, exc), "backup": backup_path}
            return {"ok": False, "rolled_back": False, "steps": steps,
                    "message": "覆盖内核文件失败，且自动回滚失败：%s\n"
                               "备份文件：%s\n手动恢复：把该 zip 解压覆盖回 %s。"
                               % (rollback_message, backup_path, self.core_dir),
                    "backup": backup_path}
        note("覆盖 %d 个文件，新增 %d 个" % (len(overwritten), len(added)))
        stale = sorted(name for name in manifest
                       if name not in overwritten and not is_protected(name))
        if stale:
            note("新版已不含以下旧文件（未自动删除，请自行确认）：%s"
                 % "、".join(stale[:20]), "WARN")
        if protected_hits:
            note("按保护名单跳过 %d 条（数据/缓存）：%s"
                 % (len(protected_hits), "、".join(protected_hits[:10])))
        runtime_note = self._runtime_note(extract_dir)
        if runtime_note:
            note(runtime_note, "WARN")

        # 6) 自检
        emit("verify_core", 0, 0, "正在启动新内核自检")
        ok, message = self._verify_new_core(stopped, start_core, note)
        if ok:
            self._write_state({
                "from_version": plan.current_version,
                "to_version": plan.latest_version,
                "tag": plan.tag,
                "asset": plan.asset_name,
                "asset_sha256": actual,
                "backup": backup_path,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": "ok",
            })
            self._cleanup(archive_path)
            shutil.rmtree(extract_dir, ignore_errors=True)
            note("更新完成：%s → %s" % (plan.current_version, plan.latest_version))
            # 基准跟着新版重录：内核被合法换过，基准不跟着换就会在下次体检里
            # 报成"外壳改了内核"（见 write_baseline 的注释）。
            # 注意用 self.data_dir（而不是 paths.data_dir()）：隔离测试会把
            # self.data_dir 指到临时目录，这样它就绝不会写坏真基准。
            base_ok, base_msg = write_baseline(
                self.core_dir, plan.latest_version, self.data_dir,
                note=("%s 安装后由更新链路自动重录（此前基准是 %s，已被合法升级作废）"
                      % (plan.latest_version, plan.current_version)),
                logger=note)
            note("只读基准：%s" % base_msg, "INFO" if base_ok else "WARN")
            steps.append("重录只读基准：%s" % ("成功" if base_ok else base_msg))
            return {"ok": True, "message": "已更新到 %s" % plan.latest_version,
                    "to_version": plan.latest_version,
                    "from_version": plan.current_version,
                    "steps": steps, "rolled_back": False, "backup": backup_path,
                    "baseline": base_ok}

        # 7) 回滚
        note("新内核自检未通过（%s），开始回滚" % message, "ERROR")
        emit("rollback", 0, 0, "正在回滚")
        rollback_ok, rollback_message = self._restore_backup(backup_path, manifest)
        note("回滚：%s" % ("成功" if rollback_ok else "失败——" + rollback_message),
             "INFO" if rollback_ok else "ERROR")
        if rollback_ok:
            restart_ok, restart_message = self._verify_new_core(False, start_core, note)
            note("已回到 %s 并重新自检：%s（%s）"
                 % (plan.current_version, "通过" if restart_ok else "未通过",
                    restart_message))
        self._write_state({
            "from_version": plan.current_version,
            "to_version": plan.latest_version,
            "tag": plan.tag,
            "result": "rolled_back" if rollback_ok else "rollback_failed",
            "backup": backup_path,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if rollback_ok:
            return {"ok": False, "rolled_back": True, "steps": steps,
                    "message": "新内核自检未通过（%s），已自动回滚到 %s。"
                               % (message, plan.current_version),
                    "backup": backup_path}
        return {"ok": False, "rolled_back": False, "steps": steps,
                "message": "新内核自检未通过，且自动回滚失败：%s\n"
                           "内核当前处于已停止状态。备份文件：%s\n"
                           "手动恢复：把该 zip 解压覆盖回 %s 后重新启动。"
                           % (rollback_message, backup_path, self.core_dir),
                "backup": backup_path}

    # ---- 安装的内部步骤 ---------------------------------------------------

    def _verify_core_dir(self):
        if not os.path.isdir(self.core_dir):
            return "内核目录不存在：%s" % self.core_dir
        for name in REQUIRED_FILES:
            if not os.path.isfile(os.path.join(self.core_dir, name)):
                return "内核目录缺少 %s，不敢更新：%s" % (name, self.core_dir)
        try:
            probe = os.path.join(self.core_dir, ".wb-shell-write-probe")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe)
        except Exception as exc:
            return "内核目录不可写（%s）：%s" % (self.core_dir, exc)
        return ""

    def _overlay(self, source_dir):
        """把 source_dir 铺到内核目录。

        返回 (覆盖的路径集合, 新增的路径集合, 保护命中清单)。

        页文件在 Windows 上不是"进程一退就立刻松手"：终止信号送达后，句柄回收、
        杀软的实时扫描、搜索索引都可能再占用它几十到几百毫秒。所以每一次写入都带
        退避重试；真的一直被占住才报错（由调用方回滚），而不是留个半覆盖的内核。
        """
        overwritten = set()
        added = set()
        protected_hits = []
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                source = os.path.join(root, name)
                relative = os.path.relpath(source, source_dir).replace("\\", "/")
                if is_protected(relative):
                    protected_hits.append(relative)
                    continue
                destination = os.path.join(self.core_dir,
                                           relative.replace("/", os.sep))
                existed = os.path.isfile(destination)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                last_error = None
                for attempt in range(6):
                    try:
                        shutil.copy2(source, destination)
                        last_error = None
                        break
                    except OSError as exc:
                        last_error = exc
                        if not isinstance(exc, PermissionError) and \
                                getattr(exc, "winerror", None) not in (5, 32, 33):
                            break
                        time.sleep(0.35 * (attempt + 1))
                if last_error is not None:
                    raise UpdateError(
                        "写入 %s 失败：%s\n（文件被占用，通常是内核尚未完全退出或"
                        "安全软件正在扫描；请稍后重试更新）"
                        % (relative, last_error))
                (overwritten if existed else added).add(relative)
        return overwritten, added, protected_hits

    def _runtime_note(self, extract_dir):
        """新包声明的运行时版本与本机 `python/` 不一致时给一句提示。

        `python/` 在保护名单里——我们不替换便携运行时。但官方发布包会把它钉死并
        校验（`release-manifest.json` 的 `runtime.python`），所以"新内核要求 3.12.15
        而我这里是 3.12.14"这种事必须说出来，而不是悄悄跳过。纯提示，不阻断更新。
        """
        manifest_path = os.path.join(extract_dir, "release-manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                declared = (json.load(handle).get("runtime") or {}).get("python") or ""
        except Exception:
            return ""
        if not declared:
            return ""
        local = ""
        version_file = os.path.join(self.core_dir, "python", "version.txt")
        try:
            with open(version_file, "r", encoding="utf-8") as handle:
                local = handle.read().strip()
        except Exception:
            pass
        if not local:
            exe = os.path.join(self.core_dir, "python", "python.exe")
            if os.path.isfile(exe):
                local = "（无法读取版本，但 python\\python.exe 存在）"
        return ("新包声明运行时 Python %s，本机 python\\ 为 %s —— 便携运行时按设计"
                "**未被替换**（换运行时风险高于收益）。若新内核需要更高版本，"
                "请手工解压官方包里的 python\\ 目录覆盖。" % (declared, local or "未知"))

    def _restore_backup(self, backup_path, manifest):
        """从备份 zip 逐文件还原：先删本次新增，再覆盖回原件。"""
        if not os.path.isfile(backup_path):
            return False, "备份文件不见了：%s" % backup_path
        try:
            entries, skipped = safe_extract_entries(backup_path)
            entries = [(info, relative) for info, relative in entries
                       if relative != "__manifest__.json"]
            # 先删掉当前目录里备份清单之外的文件（本次更新新增的）。
            current = core_manifest(self.core_dir, skip_protected=True)
            for relative in current:
                if relative not in manifest and not is_protected(relative):
                    try:
                        os.remove(os.path.join(self.core_dir,
                                               relative.replace("/", os.sep)))
                    except Exception:
                        pass
            with zipfile.ZipFile(backup_path) as archive:
                for info, relative in entries:
                    if info.is_dir() or is_protected(relative):
                        continue
                    destination = os.path.join(self.core_dir,
                                               relative.replace("/", os.sep))
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with archive.open(info) as source, open(destination, "wb") as handle:
                        shutil.copyfileobj(source, handle)
            # 逐文件校验还原结果
            bad = []
            for relative, digest in manifest.items():
                full = os.path.join(self.core_dir, relative.replace("/", os.sep))
                if not os.path.isfile(full):
                    bad.append("%s 缺失" % relative)
                    continue
                if digest and sha256_file(full) != digest:
                    bad.append("%s 内容不符" % relative)
                if len(bad) >= 5:
                    break
            if bad:
                return False, "还原后校验不通过：%s" % "、".join(bad)
            return True, ""
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)

    def _verify_new_core(self, was_stopped, start_core, note):
        """启动内核并探通 /health。返回 (ok, message)。"""
        if self.service is None or not start_core:
            return True, "（未配置内核服务，跳过自检）"
        ok, message = self.service.start()
        note("启动内核：%s（%s）" % ("成功" if ok else "失败", message))
        return ok, message

    def _restart_if_needed(self, stopped, start_core, note):
        """更新在自检之前失败：把停下来的内核放回去，别让用户的服务停在半空。"""
        if stopped and start_core and self.service is not None:
            ok, message = self.service.start()
            note("更新中止，已把原内核重新启动：%s（%s）"
                 % ("成功" if ok else "失败", message))

    def _cleanup(self, path):
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

    # ---- 独立回滚入口 -----------------------------------------------------

    def rollback(self, backup_name=None, progress=None):
        """手动回滚到某个备份。返回 dict：ok / message / steps。"""
        steps = []
        backups = self.list_backups()
        if not backups:
            return {"ok": False, "message": "没有可用的备份", "steps": steps}
        target = None
        if backup_name:
            for item in backups:
                if item["name"] == backup_name:
                    target = item
                    break
        else:
            target = backups[0]
        if target is None:
            return {"ok": False, "message": "找不到备份 %s" % backup_name,
                    "steps": steps}

        if self.service is not None:
            state = self.service.status()["state"]
            if state == service_mod.EXTERNAL:
                return {"ok": False, "steps": steps,
                        "message": "内核是外部启动的，无法安全回滚"}
            if state not in (service_mod.STOPPED,):
                ok, message = self.service.stop()
                steps.append("停止内核：%s（%s）" % ("成功" if ok else "失败", message))
                if not ok:
                    return {"ok": False, "message": "无法停止内核：%s" % message,
                            "steps": steps}

        try:
            with zipfile.ZipFile(target["path"]) as archive:
                manifest = json.loads(archive.read("__manifest__.json")
                                      .decode("utf-8")).get("files") or {}
        except Exception as exc:
            return {"ok": False, "message": "备份清单读取失败：%s" % exc,
                    "steps": steps}

        ok, message = self._restore_backup(target["path"], manifest)
        steps.append("还原 %s：%s" % (target["name"], "成功" if ok else message))
        if not ok:
            return {"ok": False, "message": message, "steps": steps}
        if self.service is not None:
            started, start_message = self.service.start()
            steps.append("重新启动内核：%s（%s）"
                         % ("成功" if started else "失败", start_message))
            ok = ok and started
        self._write_state({"result": "manual_rollback", "backup": target["path"],
                           "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        # 手工回滚也是"内核文件被合法换过"：基准同样要跟着换，否则立刻又会出现
        # "基准说 1.7.6、磁盘是 1.7.5"的假不一致（那正是这次要消灭的东西）。
        if ok:
            back_version = service_mod.read_core_version(self.core_dir) or ""
            base_ok, base_msg = write_baseline(
                self.core_dir, back_version, self.data_dir,
                note=("%s 手工回滚后由更新链路自动重录（回滚到 %s）"
                      % (back_version or "回滚版", target["name"])),
                logger=lambda text, level="INFO": steps.append(text))
            steps.append("重录只读基准：%s" % ("成功" if base_ok else base_msg))
        return {"ok": ok, "steps": steps,
                "message": "已回滚到 %s" % target["name"] if ok
                           else "回滚完成但内核未能启动"}
