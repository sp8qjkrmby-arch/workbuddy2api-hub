> 主题: next-steps
> 何时读: 准备继续做维护、排优先级时
> 关键词: 待办, P1-4, 便携包, 更新通道, 外壳测试, 监控

# 待办与优先级

## 立即可做的三件（按推荐顺序）

### A. P1-4 便携包 ✅ 已完成（2026-11-23，见 `22-release-pipeline.md`）

**目标**：证明"我们能产出可安装的新版本"。

1. 用 `repo\release\release_tools.py` 生成便携包 + `release-manifest.json` + `SHA256SUMS`；
2. 在 **8799** 旁路实例启动它，过门 1 + 门 3；
3. 产物不入库（`dist/` 被 gitignore），只上传到 GitHub release。

**验收**：旁路实例 `/health` 200 + 一次真实 chat 成功。

### B. P2-1 自建更新通道 ✅ 已完成（2026-11-23：Release v1.10.9 已发布并装到生产）

**目标**：让外壳"检查更新"重新可用，且只信我们自己的源。

1. 修 4 处硬编码（`updater.py:553`、`config.py:53`、`wb_updates.py:35`、`wb_update_portable.py:25`）→ 指向自己的仓库；
2. 或更省事：`shell.json.update_mirror` 指向自建静态 JSON + `WB_UPDATE_REPO` 环境变量；
3. **同时修版本双轨**（`validate_stage` 语义），否则面板安装仍会失败；
4. 做一次**端到端安装 + 回滚演练**（交接文档承认从未跑通过）。

**验收**：装一次成功、回滚一次成功、两次都有日志。

### C. P2-2 迁入外壳测试 ✅ 已完成（2026-11-23：10 套件 headless 全绿，GUI 探针保留手工档）

**目标**：外壳层不再裸奔。

`C:\Desktop\2api\tests\` 有 31 个测试，按三档：

| 档 | 例子 | 能否直接跑 |
|---|---|---|
| 纯 headless | `check_js_api.py`、`ui_api_test.py`、`lan_share_test.py` | ✅ 可以 |
| 隔离集成 | `update_isolated_test.py`（18899 真内核）、`tray_autostart_test.py`（改注册表） | ⚠️ 需清理 |
| GUI 探针 | `probe_*.py`（真窗口、标题与生产同名） | ⚠️ 跑前要告知，跑后 `kill_test_processes.py` |

先迁第一档（收益/风险比最高）。

## 其次是"隐患"类

| 项 | 说明 | 优先级 |
|---|---|---|
| `0x40010004` 根因 | 38/82 次被外部终止 | 中（看门狗兜着） |
| `build_shell.ps1` 缺 `setup_vendor.ps1` | 生产目录无法重建外壳 | 中 |
| `docker-compose`/README 仍指向上游 | 会拉到不存在的产物 | 低 |
| `ops/aliyun` 的 `xybzljc` 残留 | 未使用的更新器 | 低 |
| 文档重生（README/交接文档） | 章节乱序、ACL 事故未记录 | 中（不急） |

## 长期（持续）

| 项 | 频率 |
|---|---|
| 上游协议漂移跟踪（UA/指纹/接口） | 每日看一次报错趋势 |
| 社区巡视（M-strange / niyouqianma 的 release notes） | 每月一次，只挑风控/安全类 cherry-pick |
| 替代 provider 评估 | 与"通道还能撑多久"挂钩 |
| 备份恢复演练 | 每季度一次 |

## 明确"不做"的

- 升级到社区 v2.2.x（见 `16-decisions.md` D1）
- 大重构拆巨石（D4）
- 把更新自动安装指向第三方仓库（D2）

## 账号侧的时间敏感项

| 日期 | 事件 |
|---|---|
| 2026-11-23 | 2 个账号 access token 到期（自动刷新，需观察是否成功） |
| 2026-12-18 | 5 个账号 access token 到期 |
| 随时 | 上游风控（按客户端指纹拦截）——这才是真正的时间炸弹 |
