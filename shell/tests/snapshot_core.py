# -*- coding: utf-8 -*-
"""snapshot_core.py — 给内核目录做一份可校验的只读快照。

用途有三个，都只读、都不写内核目录：

1. **录基准**（默认）：`data/baseline-core-sha256.txt`，用于以后证明"外壳没有改过内核"。
   内核被外部升级后基准会失效，必须重录——留着过期基准比没有基准更危险，
   它会让每一次比对都误报。
2. **校基准**（`--check`）：拿现有基准去比磁盘，**只读**、不写任何文件。
   这是接手体检（交接文档 §2 第 7 条）里那段 PowerShell 的正式版本——
   它必须**列出具体文件名**并分类（内容不同 / 缺失 / 基准外多出），
   因为"1 处不一致"这种结论看不出是谁、也就下不了判断。
3. **备份**（`--backup`）：`data/backups/core-<版本>-<时间戳>.zip`，含 `__manifest__.json`
   （逐文件 sha256），可用 updater 的回滚路径，也可手工解压覆盖回去。

用法：
    python tests/snapshot_core.py --core <内核目录>              # 只录基准
    python tests/snapshot_core.py --core <内核目录> --check      # 只校验，不写盘
    python tests/snapshot_core.py --core <内核目录> --backup     # 录基准 + 备份

退出码：0 = 一切正常；1 = 校验发现差异 / 备份自证失败；2 = 参数或目录问题。
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile

_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
              or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "launcher"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as config_mod      # noqa: E402
import logging_setup             # noqa: E402
import paths                     # noqa: E402
import service as service_mod    # noqa: E402
import updater as updater_mod    # noqa: E402

BASELINE_NAME = "baseline-core-sha256.txt"
BASELINE_LINE = r"^\s*([0-9a-fA-F]{64})\s+(.+)$"


def read_baseline(path):
    """读基准文件。返回 dict：entries{相对路径: sha} / header{键: 值} / error。"""
    result = {"entries": {}, "header": {}, "error": ""}
    if not os.path.isfile(path):
        result["error"] = "基准文件不存在：%s" % path
        return result
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except Exception as exc:
        result["error"] = "基准文件读不出来：%r" % (exc,)
        return result
    import re
    pattern = re.compile(BASELINE_LINE)
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            body = line.lstrip("#").strip()
            if ":" in body:
                key, _, value = body.partition(":")
                result["header"][key.strip()] = value.strip()
            continue
        match = pattern.match(line)
        if match:
            result["entries"][match.group(2).strip()] = match.group(1).upper()
    return result


def check_baseline(core, shell, quiet=False):
    """把现有基准和磁盘比一遍。返回 dict（ok / counts / details）。

    **绝不写盘**：不重录、不归档、不动 config-backups。
    """
    data_dir = os.path.join(shell, "data")
    baseline_path = os.path.join(data_dir, BASELINE_NAME)
    baseline = read_baseline(baseline_path)
    report = {
        "ok": False,
        "baseline": baseline_path,
        "baseline_version": baseline["header"].get("version", ""),
        "baseline_created": baseline["header"].get("created", ""),
        "disk_version": "",
        "counts": {},
        "changed": [],
        "missing": [],
        "extra": [],
        "error": baseline["error"],
    }
    if baseline["error"]:
        return report

    report["disk_version"] = service_mod.read_core_version(core) or ""
    disk = updater_mod.core_manifest(core, skip_protected=True)

    entries = baseline["entries"]
    for relative, expected in sorted(entries.items()):
        actual = disk.get(relative)
        if actual is None:
            report["missing"].append(relative)
        elif actual.upper() != expected:
            report["changed"].append(relative)
    for relative in sorted(disk):
        if relative not in entries:
            report["extra"].append(relative)

    report["counts"] = {"baseline": len(entries), "disk": len(disk),
                        "changed": len(report["changed"]),
                        "missing": len(report["missing"]),
                        "extra": len(report["extra"])}
    report["ok"] = not (report["changed"] or report["missing"] or report["extra"])

    if not quiet:
        print("基准文件 : %s" % baseline_path)
        print("基准头   : 版本 %s，录制于 %s"
              % (report["baseline_version"] or "?",
                 report["baseline_created"] or "?"))
        print("磁盘版本 : %s" % (report["disk_version"] or "未识别"))
        print("文件数   : 基准 %d / 磁盘 %d"
              % (report["counts"]["baseline"], report["counts"]["disk"]))
        if report["changed"]:
            print("\n内容不同（%d 个）——" % len(report["changed"]))
            for name in report["changed"]:
                print("  ! %s" % name)
        if report["missing"]:
            print("\n基准里有、磁盘上没有（%d 个）——" % len(report["missing"]))
            for name in report["missing"]:
                print("  - %s" % name)
        if report["extra"]:
            print("\n磁盘上有、基准里没有（%d 个）——" % len(report["extra"]))
            for name in report["extra"]:
                print("  + %s" % name)
        print()
        version_mismatch = (report["baseline_version"] and report["disk_version"]
                            and report["baseline_version"] != report["disk_version"])
        if report["ok"]:
            print("结论：基准与磁盘完全一致（%d 个文件），内核未被改动。"
                  % report["counts"]["baseline"])
            if version_mismatch:
                # 内容一致但基准头版本旧：最容易误导的一种状态——
                # 看起来"没问题"，其实基准记录的是上一版，下次内核真被改了也未必报得准。
                print("      注意：基准头版本 %s ≠ 磁盘版本 %s（内容虽一致），"
                      "建议重录一次基准（去掉 --check 跑一次）。"
                      % (report["baseline_version"], report["disk_version"]))
        else:
            print("结论：发现差异（不同 %d / 缺失 %d / 多出 %d）。"
                  % (len(report["changed"]), len(report["missing"]),
                     len(report["extra"])))
            if version_mismatch:
                print("      基准版本 %s ≠ 磁盘版本 %s：多半是内核被升级过，"
                      "需要重录基准（去掉 --check 跑一次）。"
                      % (report["baseline_version"], report["disk_version"]))
            else:
                print("      版本相同却有差异：可能是外壳动过内核，或内核自己写了运行时文件"
                      "（catalog/ 之类应当已在保护名单里）。")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", required=True, help="内核目录")
    parser.add_argument("--shell", default=_SHELL_DIR, help="外壳目录")
    parser.add_argument("--check", action="store_true",
                        help="只校验现有基准与磁盘是否一致（只读，不写任何文件）")
    parser.add_argument("--backup", action="store_true", help="同时生成备份 zip")
    parser.add_argument("--label", default="", help="写进基准文件头的一行说明")
    args = parser.parse_args()

    core = os.path.abspath(args.core)
    shell = os.path.abspath(args.shell)
    data_dir = os.path.join(shell, "data")

    if not os.path.isdir(core):
        print("内核目录不存在：%s" % core)
        return 2

    if args.check:
        # --check 是纯只读的：连日志都不写（logging_setup 会往 data\shell.log 追加行，
        # 那会让"壳没动过东西"的复核多出一行噪音，也让 --check 有写副作用）。
        report = check_baseline(core, shell)
        if report["error"]:
            print("无法校验：%s" % report["error"])
            return 2
        return 0 if report["ok"] else 1

    logging_setup.setup(data_dir)

    version = service_mod.read_core_version(core)
    print("内核目录 : %s" % core)
    print("版本串   : %s" % (version or "未识别"))

    started = time.time()
    manifest = updater_mod.core_manifest(core, skip_protected=True)
    took = time.time() - started
    print("文件数   : %d（已排除 accounts/ usage/ __pycache__/ data/ tests/ 等保护项）"
          % len(manifest))
    print("耗时     : %.1f 秒" % took)

    # --- 基准 ---
    baseline = os.path.join(data_dir, BASELINE_NAME)
    if os.path.isfile(baseline):
        old = os.path.join(data_dir, "config-backups",
                           "baseline-core-sha256.%s.txt"
                           % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(old), exist_ok=True)
        try:
            shutil.copy2(baseline, old)
            print("旧基准已归档：%s" % old)
        except Exception as exc:
            print("旧基准归档失败（继续）：%r" % (exc,))
    lines = ["# core_dir: %s" % core,
             "# version : %s" % (version or "unknown"),
             "# created : %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "# note    : %s" % (args.label or "外壳只读基准；用于证明外壳未修改内核")]
    for relative in sorted(manifest):
        lines.append("%s  %s" % (manifest[relative], relative))
    temp = baseline + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, baseline)
    print("基准已写入：%s" % baseline)

    if not args.backup:
        return 0

    # --- 备份 ---
    backups = os.path.join(data_dir, "backups")
    os.makedirs(backups, exist_ok=True)
    tag = (version or "unknown").replace("/", "_")
    archive = os.path.join(backups, "core-%s-%s.zip"
                           % (tag, time.strftime("%Y%m%d-%H%M%S")))
    print("正在打包备份…")
    written = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, relative in enumerate(sorted(manifest)):
            full = os.path.join(core, relative.replace("/", os.sep))
            try:
                zf.write(full, relative)
                written += 1
            except Exception as exc:
                print("  跳过 %s：%r" % (relative, exc))
            if index and index % 100 == 0:
                sys.stdout.write("\r  已写入 %d/%d" % (index, len(manifest)))
                sys.stdout.flush()
        zf.writestr("__manifest__.json", json.dumps(
            {"version": version, "core_dir": core,
             "created": time.strftime("%Y-%m-%d %H:%M:%S"),
             "files": manifest}, ensure_ascii=False, indent=1))
    sys.stdout.write("\r")
    size = os.path.getsize(archive)
    print("备份已写入：%s（%d 个文件，%.1f MB）"
          % (archive, written, size / 1048576.0))

    # 读回自证：解出 manifest 并抽查若干文件与磁盘一致
    print("正在校验备份可用性…")
    with zipfile.ZipFile(archive) as zf:
        stored = json.loads(zf.read("__manifest__.json").decode("utf-8"))
        stored_files = set(stored.get("files") or {})
        missing = set(manifest) - stored_files
        bad_names = zf.namelist()
    checks = [name for name in sorted(manifest)][:5] + \
             [name for name in sorted(manifest)][-5:]
    mismatched = []
    with zipfile.ZipFile(archive) as zf:
        for relative in checks:
            try:
                with zf.open(relative) as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
                if digest != manifest[relative]:
                    mismatched.append(relative)
            except KeyError:
                mismatched.append(relative + "（备份里缺失）")
    ok = not missing and not mismatched
    print("  备份内 manifest 文件数：%d；zip 条目数：%d" % (len(stored_files),
                                                          len(bad_names)))
    print("  manifest 缺失：%s" % ("无" if not missing else "、".join(sorted(missing)[:5])))
    print("  抽查 %d 个文件：%s" % (len(checks),
                                   "全部一致" if not mismatched
                                   else "不一致 " + "、".join(mismatched)))
    print("备份结论：%s" % ("可用（可原地解压覆盖回滚）" if ok else "有问题，请勿依赖"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
