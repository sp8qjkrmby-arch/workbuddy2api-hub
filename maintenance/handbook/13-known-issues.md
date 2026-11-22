> 主题: known-issues
> 何时读: 遇到怪现象先查这里；计划下一步做修复时
> 关键词: bug, 待修, 版本双轨, 404, 0x40010004, setup_vendor, DPAPI

# 已知问题清单

状态说明：✅ 已修 / 🟡 待修 / 🔵 观察中

| # | 问题 | 状态 | 位置 | 影响 |
|---|---|---|---|---|
| 1 | `read_core_version()` 只读文件前 400 000 字符，而 v1.10.8 的 `version` 在偏移 **423 277** | ✅ **已修**（2026-11-22，全文读取 8 MB 上限） | `launcher\service.py:84` | 底栏显示"版本 ?"、`update-state.current_version` 为空 |
| 2 | **版本双轨**：release tag ≠ 包内 `version` | ✅ **已解决**（约定 tag == manifest == server_version；v1.10.9 三处一致） | `wb-proxy\wb_update_portable.py` | — |
| 3 | 更新仓库硬编码 4 处指向已 404 的仓库 | ✅ **已修**（4 处全改指自维护仓库；通道已实测可用） | `launcher\updater.py:553`、`launcher\config.py:53`、`wb-proxy\wb_updates.py:35`、`wb-proxy\wb_update_portable.py:25` | 已恢复检查（`check_update_on_launch=true`） |
| 4 | 内核被外部终止：82 次启动中 38 次退出码 `0x40010004` | 🔵 未查根因 | `data\core-8788.log` | 偶发断流，看门狗兜住 |
| 5 | `build_shell.ps1` 引用的 `tests\setup_vendor.ps1` 在生产目录**不存在** | 🟡 待修 | `wb-shell\build_shell.ps1` | 外壳无法从生产目录重建 |
| 6 | 生产目录无任何测试；外壳的 31 个探针测试只在旧目录 `C:\Desktop\2api\tests\` | 🟡 待迁移 | — | 外壳层没有回归网 |
| 7 | `docker-compose.yml` 与 README 徽章仍指向上游 `ardeyouxipianyi/...` | 🟡 待改 | `repo\docker-compose.yml`、`repo\README.md` | fork 里直接拉镜像会拿到不存在的产物 |
| 8 | `ops/aliyun/` 里残留 `xybzljc` 仓库引用 | 🟡 待清理 | `repo\ops\aliyun\` | 未使用的更新器，指向不明 |
| 9 | `read_core_version` 修复后 `shell.json.last_core_version` 仍为空 | 🔵 观察中 | — | `shell.log` 已能打印"版本 1.10.8"，字段可能要下次保存配置才写 |
| 10 | 交接文档章节乱序（0-8→11-13→9-10），10-18 ACL 事故未记录 | 🟡 待重写 | `wb-shell\交接文档.md` | 新人踩坑 |

## 已确认"不是 bug"的几件事（别再查一遍）

| 现象 | 真相 |
|---|---|
| `/v1/models` 不带 Key 返回 401 | 设计如此（需 Bearer） |
| 面板日志刷 `releases` 404 | 仓库已删，非代码 bug |
| `catalog : app cache not found` | 指桌面 App 缓存路径，实际走实时模型 API |
| 日志"没有轮转" | **有的**：外壳 2 MB×3、内核 8 MB×3 |
| 杀内核 6 秒又起来 | 看门狗设计行为 |
| `pywebview：可用 unknown` | 版本探测方式受限，不影响使用 |

## 修 bug 的标准流程

1. 记下复现条件（能复现 > 不能复现）；
2. 门 1 确认基线没被动过；
3. 改代码 → 门 2 全量测试 → 门 3 冒烟（若涉及外壳/链路）；
4. 部署到生产：先备份原件到 `backups\prod-files\`，再覆盖；
5. 重启生效（脱离会话的方式）；
6. 把结论写进本文件（状态改为 ✅）与 `14-gotchas-windows.md`（若学到新坑）。
