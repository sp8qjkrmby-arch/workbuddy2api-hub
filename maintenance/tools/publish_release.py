"""发布 GitHub Release（自维护仓库），凭证来自 git credential（GCM），不落盘、不回显。

用法:
    python publish_release.py <tag> <zip_path> <sums_path> <body_file>
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

OWNER = "sp8qjkrmby-arch"
REPO = "workbuddy2api-hub"
REPO_ROOT = r"C:\Desktop\2api维护\repo"


def get_token():
    """从 git credential（GCM → Windows 凭据管理器）取 token。"""
    payload = "protocol=https\nhost=github.com\n\n"
    out = subprocess.run(
        ["git", "credential", "fill"],
        cwd=REPO_ROOT, input=payload, capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise SystemExit("取凭据失败: %s" % (out.stderr or "").strip()[:200])
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("凭据里没有 password 字段")


def api(token, method, path, data=None, base="https://api.github.com", ctype="application/json"):
    url = base + path
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", ctype)
    req.add_header("User-Agent", "wb-fork-release-script")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return exc.code, {"error": detail}


def main():
    tag, zip_path, sums_path, body_file = sys.argv[1:5]
    token = get_token()
    with open(body_file, encoding="utf-8") as fh:
        body = fh.read()

    # 1) 创建 release（已存在则复用）
    status, rel = api(token, "POST", "/repos/%s/%s/releases" % (OWNER, REPO), {
        "tag_name": tag,
        "target_commitish": "main",
        "name": "v%s · 自维护分支首版" % tag.lstrip("v"),
        "body": body,
        "draft": False,
        "prerelease": False,
    })
    if status == 422:
        print("release 已存在，取回现有记录")
        status, rel = api(token, "GET", "/repos/%s/%s/releases/tags/%s" % (OWNER, REPO, tag))
    if status not in (200, 201):
        print("创建 release 失败 %s: %s" % (status, rel))
        return 1
    print("release: %s (id=%s)" % (rel["html_url"], rel["id"]))

    existing = {a["name"]: a for a in (rel.get("assets") or [])}
    for path in (zip_path, sums_path):
        name = os.path.basename(path)
        if name in existing:
            print("asset 已存在，跳过: %s" % name)
            continue
        with open(path, "rb") as fh:
            payload = fh.read()
        q = urllib.parse.urlencode({"name": name})
        status, asset = api(
            token, "POST",
            "/repos/%s/%s/releases/%s/assets?%s" % (OWNER, REPO, rel["id"], q),
            payload, base="https://uploads.github.com",
            ctype="application/octet-stream",
        )
        if status not in (200, 201):
            print("上传失败 %s: %s" % (name, asset))
            return 1
        print("asset: %-38s %6.2f MB  %s" % (name, asset["size"] / 1048576, asset["browser_download_url"]))

    status, rel = api(token, "GET", "/repos/%s/%s/releases/tags/%s" % (OWNER, REPO, tag))
    print("\n最终状态: %s  资产 %d 个" % (rel.get("html_url"), len(rel.get("assets") or [])))
    for a in rel.get("assets") or []:
        print("  - %-38s %6.2f MB" % (a["name"], a["size"] / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
