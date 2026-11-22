> 主题: release-pipeline
> 何时读: 要发新版、打便携包、验证安装/回滚、排查"更新按钮没反应"时
> 关键词: 打包, release, portable, SHA256SUMS, publish, run_update, 回滚, 8799, promote

# 发布流水线（2026-11-23 首次跑通）

## 一条龙

```
源码(repo/main) → 打包(portable zip) → 校验和 → 发 Release(自己的仓库)
                                                   ↓
                              外壳/面板检查更新 → 下载 → 校验 → 暂存 → 备份 → 替换 → 重启 → 健康检查
                                                   ↓ 失败自动回滚
```

## 1. 打包（本地可复现）

```powershell
cd C:\Desktop\2api维护\repo
$py = 'C:\Desktop\wb-shell\wb-proxy\python\python.exe'   # 必须是 3.12 腿

# ① 取固定版本的 CPython（下载 44 MB，sha256 已钉死）
& $py release\portable_runtime.py info        # 打印 RUNTIME_URL / RUNTIME_SHA256 / 3.12.15
curl.exe -fsSL --retry 3 -o .rt.tar.gz <RUNTIME_URL>
(Get-FileHash .rt.tar.gz -Algorithm SHA256).Hash.ToLower()   # 必须等于 RUNTIME_SHA256

# ② 裁剪运行时到 repo\python\（gitignored，528 文件）
& $py release\portable_runtime.py prepare --tarball .rt.tar.gz --dest python

# ③ 打便携包（61 个受管文件 + python\ + release-manifest.json）
& $py release\release_tools.py portable --version 1.10.9 --tag v1.10.9 `
      --runtime python --out dist\workbuddy2api-hub-v1.10.9.zip

# ④ 校验和
& $py release\release_tools.py checksums --out dist\SHA256SUMS dist\workbuddy2api-hub-v1.10.9.zip
```

硬约束（`release_tools.py` 会自己拒绝）：

| 约束 | 说明 |
|---|---|
| `--version` 必须等于 `wb_proxy.py` 的 `server_version` | 否则 `SystemExit` |
| 必须给 `--runtime` | 便携包没有运行时就不是绿色包 |
| `portable.txt` 里列的文件必须都在 | 漏登记会报"清单里的文件不存在"；反过来磁盘多出的模块由 `_test_release_assets.py` 抓 |

产物实测：**10.31 MB**，sha256 `0a0ce7b4…f5345a`（1.10.9）。

## 2. 发布

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'
python C:\Desktop\2api维护\scripts\publish_release.py v1.10.9 `
       dist\workbuddy2api-hub-v1.10.9.zip dist\SHA256SUMS body.md
```

- 凭证来自 `git credential fill`（GCM → Windows 凭据管理器），**不落盘、不回显**；
- 先 POST 建 release（已存在则复用），再向 `uploads.github.com` 传资产；
- 资产 URL 匿名可下（实测 HEAD 200），所以**更新不需要 token**。

## 3. 安装链路（产品代码，已实测）

`wb_update_portable.run_update(root, package, tag, version, port, lan, pid, repo, keep_backups)`：

1. `asset_urls()` 拼 `.../releases/download/<tag>/workbuddy2api-hub-v<ver>.zip` + `SHA256SUMS`
2. `download()` → `parse_checksums()` → `sha256_file()` 比对（**校验和不符即中止**）
3. `read_zip_manifest()` → `safe_extract()` → `validate_stage()`（manifest.version / root / 每个文件 / server_version 四重校验）
4. `backup_current()` → `updates\backup-<version>\`（同时记录"原本缺失"的文件，回滚时会删掉新增的）
5. `stop_gateway(pid)` → 等端口释放 → `apply_stage()` 只替换 `manifest.files`
6. `start_service()` → `wait_health(expect_version=...)`
7. 任一步抛错 → `restore_backup()` + 重启 + `wait_health()`，状态写 `updates\status.json`

回滚：`run_rollback(root, package, version, port, pid, keep_backups)`，用第 4 步留下的备份，不再下载。

## 4. 演练与上线（我们实际怎么做的）

| 阶段 | 做法 | 结果 |
|---|---|---|
| 克隆演练 | 把生产 `wb-proxy` 复制到 `.build\prod-clone`，在 **8799** 上跑 `run_update` → 冒烟 → `run_rollback` → 冒烟 | INSTALL PASS（11.8 s）/ ROLLBACK PASS（8.7 s） |
| 生产上线 | `scripts\promote-production.ps1`（脱离会话）：停外壳 → `promote.py`（复用 `backup_current`/`apply_stage`）→ 起外壳 → 轮询 `/health` | PASS，断流 **~11 秒**，备份在 `wb-proxy\updates\backup-1.10.9\` |
| 上线后 | 真请求冒烟（8788）+ 重录基线 `core-sha256.v1.10.9.txt` | `/health` 200、`content="PACKAGE_OK"`、门 1 61 MATCH |

## 5. 坑（都踩过）

| 坑 | 现象 | 正解 |
|---|---|---|
| **manifest 位置不同** | 便携包：`<root>\release-manifest.json`；生产：`wb-proxy\release-manifest.json` | `promote.py` 的 `package` 参数要指 `wb-proxy` 本身 |
| 用 Linux 打包 | CI 明确拒绝 | 必须 Windows（要 `python\pythonw.exe` 与 `.bat`） |
| 包太小 | CI 断言 < 5 MB 即失败 | 说明运行时没进去 |
| 装了包但版本没变 | 检查 `validate_stage` 的 server_version 校验 | 版本号必须三处一致（tag / manifest / server_version） |
| 停外壳时误杀演练实例 | 我的过滤器曾匹配 `wb_proxy.py` 全路径 | 过滤条件限定 `wb-shell` 路径 |

## 6. 下次发版清单（照抄）

1. 改 `wb_proxy.py` 两处版本串 + `wrt` Makefile `PKG_VERSION`；
2. `docs/CHANGELOG.md` 顶部加条目；
3. 门 1（`-AllowChanged wb_proxy.py`）+ 门 2（261 套件）全绿；
4. 打包 → 8799 冒烟；
5. 提交并 push（tag 由 Release 创建，不要再手工打数字 tag）；
6. `publish_release.py` 发 Release；
7. 克隆演练或直接 `promote-production.ps1`；
8. 重录基线（`maintenance\baseline\core-sha256.v<版本>.txt`）+ 同步 handbook。
