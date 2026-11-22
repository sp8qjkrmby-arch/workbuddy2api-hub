> 主题: sources
> 何时读: 要补代码/比对上游/怀疑"我们这份是不是原版"、找替代源时
> 关键词: 溯源, M-strange, niyouqianma, 404, 删号, 哈希, wb_entitlements, acc0b332

# 代码从哪来（溯源）

## 结论一句话

原仓库和作者账号都已删除（不是改名）。我们当前主干 = `M-strange/workbuddy2api-hub` 的 **tag v1.10.8** 快照 + 一个补回的文件，且**与生产二进制逐字节一致**。

## 原仓库怎么了

| 事实 | 证据 |
|---|---|
| `ardeyouxipianyi/workbuddy2api-hub` 404 | `git ls-remote` → `Repository not found`；releases/forks 子资源全 404 |
| **作者账号本身** 404 | 网页与 API 双 404，**且无 301 重定向**（改名会 301 → 所以是删号） |
| `workbuddy2api-intl` 同样 404 | 无备用官方仓库 |
| fork 元数据里的 parent 仍显示 4019★/960 fork/11-13 push | **GitHub 过期缓存，别信** |

时间线：v2.0.0 发停更说明 → 11-07 作者公告"恢复更新" → 11-13 最后 push → 11-20 后清号。

## 社区留存（可用但只作参考源）

| 仓库 | 状态 | 用途 |
|---|---|---|
| `M-strange/workbuddy2api-hub` | 115 个 tag，到 v2.2.0，**含 v1.10.8** | ✅ 我们的基线来源；有便携 zip/apk/ipk + SHA256SUMS |
| `niyouqianma/workbuddy2api-hub` | 322★，v2.2.1（11-21 仍在提交），CI 在跑 | 参考最新修复；GHCR 镜像可拉（`/app` 即源码） |
| `ghcr.io/ardeyouxipianyi/*` | 403 | 官方镜像已死 |

## 我们的重建方式（可复现）

1. 取 `https://codeload.github.com/M-strange/workbuddy2api-hub/zip/refs/tags/v1.10.8`（走代理）；
2. 与生产 `baseline-core-sha256.txt` 的 64 条逐文件比对 → **60 项 MATCH、0 DIFF**；
3. 4 个"缺失"逐个查清：

| 缺失 | 真相 |
|---|---|
| `release-manifest.json` | **构建产物**（`release/release_tools.py` 按 `portable.txt` 生成），上游全历史 0 提交 |
| `wb_entitlements.py` | 上游 10-20 加（提交 `acc0b332`）、10-22 删；但 11-03 的发布包**残留**，生产在跑 → 从 `acc0b332` 取回，sha256 `ba28cd3d…f4106` 与基线**逐字节相同** |
| `wb_proxy.py.bak-20261021-231708` | 生产本地手工备份（10-21 版） |
| `wb_proxy.py.bak-20261028-172236` | 生产本地手工备份（v1.8.0） |

4. 提交进仓库（`9340aec`），并把 `wb_entitlements.py` 登记进 `release/portable.txt`（`6af1706`）。

## 复验命令

```powershell
cd C:\Desktop\2api维护\repo
powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1
# 期望：MATCH 61 / MISSING-EXPECTED 3 / DIFF 0，退出码 0
```

## 若将来要换源

只认哈希三步：① 下载候选 → ② 对 `wb_*.py` + `dashboard.html` 等 60 个载荷求 SHA256 → ③ 与 `baseline\core-sha256.v1.10.8.txt` 比。全部命中才可用；版本号不作为依据（见 `04-versions.md`）。
