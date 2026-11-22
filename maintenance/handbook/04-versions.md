> 主题: versions
> 何时读: 看到版本号对不上、"底栏显示版本 ?"、想升级、判断某个 tag 能不能用时
> 关键词: 版本, 1.10.8, 1.7.4, 2.1.3, 双轨, tag, release-manifest, validate_stage

# 版本体系（这个项目最容易踩的坑）

## 一张表看清所有"版本"

| 对象 | 值 | 说明 |
|---|---|---|
| 生产内核版本 | **1.10.8** | 来自 `wb-proxy\release-manifest.json` 的 `version` 字段；基线文件头也写 1.10.8 |
| 产生它的 release tag | **v2.1.3** | `update-state.json`: `tag=v2.1.3`, `asset=workbuddy2api-hub-v2.1.3.zip`(23 MB), `to_version=1.10.8` |
| 安装时间 | 2026-11-03 23:47:13 | 从 1.9.2 升到 1.10.8；备份 `data\backups\core-1.9.2-20261103-234707.zip` |
| 旧实验目录内核 | 1.7.12 | `C:\Desktop\2api\wb-proxy` |
| 仓库历史快照 | v1.7.4 时代 | 提交 `2fa6287`，2026-10-14，641 提交 |
| 外壳版本 | 0.1.0 | 日志"外壳启动 v0.1.0" |
| 社区最新 | v2.2.1（niyouqianma）/ v2.2.0（M-strange） | 我们**不跟**，见 `16-decisions.md` |

## 核心陷阱：版本双轨

**release tag 名 ≠ 包内 `version` 字段。**

- 例：tag `v2.1.3` 的包里，`release-manifest.json` 写的是 `1.10.8`。
- 后果：`wb_update_portable.validate_stage()` 要求"下载到的包版本 == 期望版本"，两者不相等 → **即使仓库恢复，面板"安装更新"也会失败**（外壳 updater 不校验，所以 11-03 那次是外壳装的，成功了）。
- 结论：**判断"是不是同一份代码"只认文件哈希，绝不认版本号。**

## 交叉验证过的结论（2026-11-22）

对 `M-strange/workbuddy2api-hub` 各 tag 的 `wb_proxy.py` 求 SHA256：

| tag | wb_proxy.py sha256（前 8） | 是否等于生产基线 |
|---|---|---|
| v1.9.2 | `0b7d1799…` | 否 |
| v1.10.7 | `84759901…` | 否 |
| **v1.10.8** | **`ecf49467…`** | **是（唯一命中）** |
| v1.10.9 | `138522e0…` | 否 |

所以"v1.10.8 是生产二进制对应的源码"是被哈希证明的，不是猜的。

## 版本号在代码里的 6 个位置（CI 会断言一致）

1. `wb_proxy.py` 里的两条字符串（`version` 键）—— **CI 强制这两个相等**
2. `wrt/openwrt/workbuddy2api/Makefile`（OpenWrt 包版本）
3. `README.md` 版本徽章与适用范围
4. `docs/CHANGELOG.md` 最新条目
5. `release/portable.txt` 不写版本，但打包时生成 `release-manifest.json`
6. （本地）`data\shell.json` 的 `last_core_version`、`data\update-state.json`

改版本时这 6 处要一起动，否则 CI 或外壳会显示不一致。

## 相关文件

- 基线哈希：`C:\Desktop\wb-shell\data\baseline-core-sha256.txt`（64 条）
- 溯源报告：`reports\06-source-provenance.md`
- 校验脚本：`repo\maintenance\verify-trunk-baseline.ps1`（见 `10-verification.md`）
