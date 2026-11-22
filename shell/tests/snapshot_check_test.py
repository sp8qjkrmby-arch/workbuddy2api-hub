# -*- coding: utf-8 -*-
"""snapshot_check_test.py — 验证 snapshot_core.py --check（只读校验基准）。

覆盖：
1. 一致时 ok=True、退出码 0、各分类都为空；
2. 内容被改 → 落在 changed 里，**且列出具体文件名**；
3. 文件被删 → 落在 missing 里；
4. 多出文件 → 落在 extra 里；
5. **--check 全程不写盘**：基准 / config-backups / shell.log 的 mtime 与 sha 不变；
6. 基准缺失时报错清楚（返回 2 而不是假装通过）；
7. 版本不一致时给出"多半是升级过、需要重录"的提示。

全部在临时目录里跑，不碰真基准。
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
              or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配（repo/shell/tests → 内核在 repo 根；生产布局在 <shell>/wb-proxy）
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))
sys.path.insert(0, os.path.join(_SHELL_DIR, "tests"))

import snapshot_core as snap     # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "wb-snapcheck")
_failed = []


def check(label, condition, extra=""):
    mark = "PASS" if condition else "FAIL"
    print("  [%s] %s%s" % (mark, label, ("  " + extra) if extra else ""))
    if not condition:
        _failed.append(label)
    return condition


def section(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def make_env(name):
    """造一个假外壳 + 假内核（只有一个文件 + 一份匹配的基准）。"""
    root = os.path.join(ROOT, name)
    shell = os.path.join(root, "shell")
    core = os.path.join(root, "core")
    os.makedirs(os.path.join(shell, "data", "config-backups"), exist_ok=True)
    os.makedirs(os.path.join(core, "accounts"), exist_ok=True)
    with io.open(os.path.join(core, "wb_proxy.py"), "w", encoding="utf-8") as fh:
        fh.write('VERSION = {"version": "1.7.15"}\n')
    with io.open(os.path.join(core, "dashboard.html"), "w", encoding="utf-8") as fh:
        fh.write("<html>ok</html>\n")
    return root, shell, core


def write_baseline(shell, core, version="1.7.15"):
    """用 updater 的清单录一份基准（等价于 --check 之前那次"录"）。"""
    import updater as updater_mod
    manifest = updater_mod.core_manifest(core, skip_protected=True)
    path = os.path.join(shell, "data", "baseline-core-sha256.txt")
    lines = ["# core_dir: %s" % core,
             "# version : %s" % version,
             "# created : 2026-10-18 22:00:00",
             "# note    : 测试用"]
    for relative in sorted(manifest):
        lines.append("%s  %s" % (manifest[relative], relative))
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path, manifest


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)

    # ---------------------------------------------------------------- 1
    section("1. 一致：ok=True、三类差异都为空")
    root, shell, core = make_env("ok")
    write_baseline(shell, core)
    report = snap.check_baseline(core, shell)
    check("ok 为真", report["ok"] is True)
    check("changed/missing/extra 都为空",
          not report["changed"] and not report["missing"] and not report["extra"])
    check("读出了基准版本", report["baseline_version"] == "1.7.15",
          report["baseline_version"])
    check("读出了磁盘版本", report["disk_version"] == "1.7.15",
          report["disk_version"])
    check("计数是 2 个文件", report["counts"]["baseline"] == 2,
          str(report["counts"]))

    # ---------------------------------------------------------------- 2
    section("2. 内容被改 → 落在 changed 里且**列出文件名**")
    with io.open(os.path.join(core, "wb_proxy.py"), "w", encoding="utf-8") as fh:
        fh.write('VERSION = {"version": "1.7.15"}\n# 被改过\n')
    report = snap.check_baseline(core, shell)
    check("ok 为假", report["ok"] is False)
    check("列出的是 wb_proxy.py", report["changed"] == ["wb_proxy.py"],
          repr(report["changed"]))
    check("只报 1 处", report["counts"]["changed"] == 1, str(report["counts"]))
    check("没把别的文件算进来",
          not report["missing"] and not report["extra"])

    # ---------------------------------------------------------------- 3
    section("3. 文件被删 → 落在 missing 里")
    os.remove(os.path.join(core, "dashboard.html"))
    report = snap.check_baseline(core, shell)
    check("ok 为假", report["ok"] is False)
    check("列出缺失的 dashboard.html",
          report["missing"] == ["dashboard.html"], repr(report["missing"]))

    # ---------------------------------------------------------------- 4
    section("4. 多出文件 → 落在 extra 里")
    with io.open(os.path.join(core, "wb_new_tool.py"), "w", encoding="utf-8") as fh:
        fh.write("# 新版才有的文件\n")
    report = snap.check_baseline(core, shell)
    check("列出多出的 wb_new_tool.py",
          "wb_new_tool.py" in report["extra"], repr(report["extra"]))
    check("三类差异可以同时出现",
          bool(report["changed"]) and bool(report["missing"])
          and bool(report["extra"]))

    # ---------------------------------------------------------------- 5
    section("5. --check 全程不写盘（基准 / 归档 / shell.log 都不动）")
    root5, shell5, core5 = make_env("readonly")
    baseline5, _m = write_baseline(shell5, core5)
    # 造一个"旧的归档 + 已有的 shell.log"，确认它们也不被碰
    arch = os.path.join(shell5, "data", "config-backups",
                        "baseline-core-sha256.20260101-000000.txt")
    with io.open(arch, "w", encoding="utf-8") as fh:
        fh.write("# 旧档\n")
    log = os.path.join(shell5, "data", "shell.log")
    with io.open(log, "w", encoding="utf-8") as fh:
        fh.write("existing line\n")

    def fingerprint(path):
        st = os.stat(path)
        with open(path, "rb") as fh:
            return (st.st_mtime_ns, st.st_size, fh.read())

    before = {p: fingerprint(p) for p in (baseline5, arch, log)}
    listing_before = sorted(os.listdir(os.path.join(shell5, "data",
                                                    "config-backups")))
    # 让内容真的不一致，确保 --check 走的是"有差异"的分支（更容易写出多余动作）
    with io.open(os.path.join(core5, "wb_proxy.py"), "a", encoding="utf-8") as fh:
        fh.write("# drift\n")
    report = snap.check_baseline(core5, shell5)
    after = {p: fingerprint(p) for p in (baseline5, arch, log)}
    listing_after = sorted(os.listdir(os.path.join(shell5, "data",
                                                   "config-backups")))
    check("确实检测到了差异", report["ok"] is False)
    check("基准文件一字未变", before[baseline5] == after[baseline5])
    check("旧归档一字未变", before[arch] == after[arch])
    check("shell.log 一字未变（--check 不写日志）", before[log] == after[log])
    check("没有新增任何归档", listing_before == listing_after,
          "%r -> %r" % (listing_before, listing_after))

    # ---------------------------------------------------------------- 6
    section("6. 基准缺失 → 明确报错（不假装通过）")
    root6, shell6, core6 = make_env("nobase")
    report = snap.check_baseline(core6, shell6)
    check("ok 为假", report["ok"] is False)
    check("给出 error 说明", "基准文件不存在" in report["error"], report["error"])
    code = subprocess.call([sys.executable,
                            os.path.join(_SHELL_DIR, "tests", "snapshot_core.py"),
                            "--core", core6, "--shell", shell6, "--check"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check("命令行退出码是 2（不是 0）", code == 2, "exit=%d" % code)

    # ---------------------------------------------------------------- 7
    section("7. 版本不一致 → 提示需要重录")
    root7, shell7, core7 = make_env("verdrift")
    write_baseline(shell7, core7, version="1.7.12")   # 基准说 1.7.12，磁盘是 1.7.15
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        report = snap.check_baseline(core7, shell7)
    finally:
        sys.stdout = real
    text = buf.getvalue()
    check("检测到版本不一致", report["baseline_version"] == "1.7.12"
          and report["disk_version"] == "1.7.15",
          "%s vs %s" % (report["baseline_version"], report["disk_version"]))
    check("内容一致但版本不同时，仍提示基准过期要重录",
          "重录" in text,
          text.strip().splitlines()[-1])

    # ---------------------------------------------------------------- 8
    section("8. 死键不许回来（last_core_version / last_updated_at）")
    import config as config_mod
    check("DEFAULTS 里没有 last_core_version",
          "last_core_version" not in config_mod.DEFAULTS)
    check("DEFAULTS 里没有 last_updated_at",
          "last_updated_at" not in config_mod.DEFAULTS)
    # 旧配置里带着这两个键时，读配置不能报错、也不能把它们当"已知键"处理
    root8, shell8, _core8 = make_env("deadkey")
    legacy = {"port": 8788, "last_core_version": "1.7.12",
              "last_updated_at": "2026-10-18 22:00:03", "user_extra": 42}
    with io.open(os.path.join(shell8, "data", "shell.json"), "w",
                 encoding="utf-8") as fh:
        fh.write(json.dumps(legacy))
    loaded = config_mod.load(shell8)
    check("旧配置能正常读出来", loaded["config"].get("port") == 8788)
    check("没有因为死键产生警告", not loaded["warnings"], repr(loaded["warnings"]))
    # 未知键按设计收在 config["_extra"]（save() 时再摊回顶层，所以磁盘上不丢）
    extra = loaded["config"].get("_extra") or {}
    check("用户自己的未知键被保留在 _extra 里", extra.get("user_extra") == 42,
          repr(extra))
    check("死键也在 _extra 里（不会被当成已知键乱用）",
          extra.get("last_core_version") == "1.7.12", repr(extra))

    # ---------------------------------------------------------------- 结论
    section("结论")
    print("  隔离根：%s" % ROOT)
    print("  失败项：%s" % ("无" if not _failed else "、".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
