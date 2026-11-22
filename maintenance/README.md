# 本仓库现在是「自维护分支」

上游原仓库 `ardeyouxipianyi/workbuddy2api-hub` 已随作者删号消失（2026-11-20 后账号与全部仓库 404，**无 301 重定向**）。
本目录是从社区留存镜像取回、并用哈希逐字节验证过的 **v1.10.8 主干**，由我们自己维护，仅供自用。

## 一、这份代码的身份证明

| 事实 | 证据 |
|---|---|
| 载荷与生产实例**逐字节一致** | 与 `C:\Desktop\wb-shell\wb-proxy`（生产在跑）的 64 条基线比对：**61 MATCH / 0 DIFF / 3 MISSING-EXPECTED** |
| 来源可追溯 | `M-strange/workbuddy2api-hub` tag `v1.10.8` 快照；相邻 tag 的 `wb_proxy.py` 哈希均不命中，只有它命中 |
| 可随时复验 | `powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1` |

**只认哈希，不认版本号**：这个项目存在"release tag 名 ≠ 包内 `version` 字段"的双轨问题（例如 release `v2.1.3` 的包内核心版本是 `1.10.8`）。任何"升级/换源"动作都必须先过基线校验。

细节见 [`maintenance/baseline/README.md`](baseline/README.md) 与工作区 `reports/06-source-provenance.md`。

## 二、目录约定

```
/                      ← 上游应用载荷，**保持上游布局不动**（便于将来与社区版本 diff）
  wb_*.py              37 个模块
  dashboard.html       面板（1.07 MB 单文件，无构建步骤）
  tests/               276 个测试文件 + run_all.py
  .github/workflows/   5 个 CI（tests / release / release-checksums / docker-publish / revert-guard）
  release/             便携包打包工具（portable.txt 白名单是更新器的契约）
  docs/CHANGELOG.md    1104 行变更史
shell/                 ← **我们自研的外壳**（托盘 / 面板 / 更新 / 看门狗），生产在 C:\Desktop\wb-shell
  launcher/            16 个模块（ui_web.py 143 KB 是面板前端宿主）
  ops/                 运维脚本（backup-wb-shell.ps1）
maintenance/           ← 维护资产（基线、校验脚本、维护计划）
```

外壳与内核的耦合约定：外壳启动 `wb_proxy.py`（`--port/--accounts-dir/--usage-dir`），读 `release-manifest.json` 判断版本，用 `baseline-core-sha256.txt` 检测核心是否被本地改动。**改动任一侧前，先看 `maintenance/PLAN.md` 第 7 节红线。**

## 三、日常命令

```powershell
# 基线回归（改了载荷之后必须跑，应为 61 MATCH / 0 DIFF）
powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1

# 全量测试（需 Python 3.9+ 与 PATH 上的 node；约 30 个套件会起真网关，自动用临时目录隔离）
python tests\run_all.py --jobs 4 --logs suite-logs
python scripts\check_clean_checkout.py

# 备份生产实例（只读源目录，写 backups\）
powershell -ExecutionPolicy Bypass -File shell\ops\backup-wb-shell.ps1 -IncludeCode
```

## 四、提交纪律（沿用上游 `CONTRIBUTING.md`，并加两条本地规则）

沿用：`<type>(<scope>): 摘要`；**提交前必须过冒烟门禁**（隔离端口起实例 + 面板可开 + 端到端真实上游请求成功），见 `CONTRIBUTING.md` 第 13~20 行。

本地新增：

1. **不许停 8788**：生产实例可能在跑活跃会话，冒烟一律用 `8799` 等隔离端口 + 独立 `--accounts-dir/--usage-dir`。
2. **改载荷 = 必须能解释基线差异**：若 `verify-trunk-baseline.ps1` 出现 `DIFF`，要么改回去，要么在提交正文里写明为什么这条基线作废并同步更新 `maintenance/baseline/`。

## 五、当前状态与下一步

见 [`maintenance/PLAN.md`](PLAN.md)：P0 止血已完成（冷备份 + 日备 + 冻结失效的更新检查），P1 主干重建进行中（本仓库就是成果），P2 待做自建更新通道。
