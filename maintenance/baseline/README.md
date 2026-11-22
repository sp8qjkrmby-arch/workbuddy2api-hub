# 基线：本仓库主干 == 生产内核 v1.10.8

这个目录保存「事实基准」，用来回答两个问题：

1. **我们的主干，是不是就是生产在跑的那份代码？**
2. **如果有人（包括未来的我）改了载荷文件，能立刻发现吗？**

## 文件

| 文件 | 内容 | 是否含机密 |
|---|---|---|
| `core-sha256.v1.10.8.txt` | 生产实例 `C:\Desktop\wb-shell\data\baseline-core-sha256.txt` 的原样副本：64 条 `sha256sum` 记录，由外壳在 2026-11-03 23:47:14 安装 v1.10.8 后自动重录 | 否（只有哈希与文件名） |
| `release-manifest.v1.10.8.json` | 生产目录里 `/release-manifest.json` 的副本：60 个发布载荷清单 + `tag`/`version`/`runtime` 描述。它是**构建产物**（由 `release/release_tools.py` 依 `release/portable.txt` 生成），入库只为留档对比 | 否 |

## 校验

```powershell
powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1
```

预期输出：`MATCH 61 / MISSING-EXPECTED 3 / DIFF 0`，退出码 0。

## 为什么不是 64/64

基线 64 条里有 3 条**不可能**出现在 git 主干中，已在校验脚本里列为 `MISSING-EXPECTED`：

| 文件 | 为什么不在主干 |
|---|---|
| `release-manifest.json` | 构建产物。上游全历史 0 提交；由打包脚本按 `portable.txt` 现场生成。重建包时应让它重新生成，而不是手工维护。 |
| `wb_proxy.py.bak-20261021-231708` | 生产环境升级前的手工备份（对应 10-21 版本）。 |
| `wb_proxy.py.bak-20261028-172236` | 同上（对应 v1.8.0）。 |

另有 1 个文件虽然「上游后来删了」，但**生产在跑、必须保留**：

| 文件 | 说明 |
|---|---|
| `wb_entitlements.py` | 上游 2026-10-20 加入（提交 `acc0b332`）、10-22 又从仓库删除；但 11-03 打包发布的 v2.1.3 里仍带着它（升级链路不清理残留文件），因此生产内核有、基线也有。本仓库从 `acc0b332` 取回该文件，SHA256 = `ba28cd3d…f4106`，与基线**逐字节一致**。 |

## 与生产的一致性结论（2026-11-22 实测）

- 生产载荷 60 个：与 `M-strange/workbuddy2api-hub` tag **v1.10.8** 快照**逐字节一致**（0 差异）。
- 相邻 tag 的 `wb_proxy.py` 哈希均不等于基线（v1.10.7 `84759901…`、v1.10.9 `138522e0…`、v1.9.2 `0b7d1799…`），只有 v1.10.8 命中 —— **只认哈希，不认版本号**（这个项目存在"release tag 名 ≠ 包内 version"的双轨问题）。
- 完整溯源过程见工作区 `reports/06-source-provenance.md`。
