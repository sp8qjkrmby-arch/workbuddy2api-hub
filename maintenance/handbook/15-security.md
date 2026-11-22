> 主题: security
> 何时读: 改仓库可见性、处理备份、怀疑泄露、账号异常时
> 关键词: token, 明文, DPAPI, .gitignore, private, LAN, 面板密码, 封号

# 安全与密钥

## 机密分布

| 机密 | 位置 | 保护 |
|---|---|---|
| 账号 accessToken / refreshToken | `wb-proxy\accounts\*.json`（**明文 JSON**） | 仅靠文件系统 ACL + `.gitignore` |
| 面板密码哈希 | `wb-proxy\accounts\settings.json`（PBKDF2, rounds=120000） | 同上 |
| 面板密码密文 | `data\panel-password.sealed`（DPAPI 用户作用域） | Windows 用户隔离 |
| 客户端 API Key | `accounts\settings.json` 里（多 Key） | 同上 |
| 备份里的账号 | `backups\*\core-config\accounts\*` | **同样是明文**，备份目录按机密数据处理 |

## 仓库为什么还算安全

`.gitignore`（上游原版）已经挡住：`accounts/*.json`、`usage/*.jsonl`、`usage/*.sqlite3`、`logs/`、`python/`、`*.zip`、`dist/`。已核对：仓库里只有 `accounts/README.txt`、`usage/README.txt` 两个占位文件被跟踪。
另外推前扫描过 `sk-`/`Bearer`/`accessToken":"…"` 等模式，**未发现明文密钥**。

## 但仍建议：把 fork 改成 private

仓库里现在有 `shell\交接文档.md`（记录了你的目录结构、面板密码机制、备份策略、LAN 分享设计）与 `maintenance\handbook\`（本知识库）。没有密钥，但属于"环境情报"，没必要公开。

改法：GitHub → 该仓库 → Settings → General → 最下方 Danger Zone → **Change repository visibility → Private**。

## 账号侧的真实风险（比代码更致命）

| 风险 | 现状 | 应对 |
|---|---|---|
| 上游按客户端指纹拦截 | 2026-10-20 起，UA 硬编码 `5.8.4` | 关注 `wb_fingerprint.py` / `wb_egress.py`，换 UA/出口要谨慎 |
| 封号 | 社区有批量封号反馈 | 7 个启用账号，2 个已停用（`b29ff4bf…` 11-12、`d7bbe199…` 11-13） |
| token 到期 | 2 个在 **11-23**，5 个在 **12-18**（access token JWT，会自动用 refreshToken 换新） | 到期≠死亡；真验需要发真实请求 |
| 活跃会话数 | 上游限制（≤10 会话） | 别并发太多实例 |

## 本地暴露面

- 默认只绑 `127.0.0.1:8788`（好）。
- `--lan` 会绑 `0.0.0.0` 并强制生成 Key；`lanshare.py` 提供 LAN 分享。**没需求就别开**。
- 面板 `?pwd=` / `?key=` 会出现在 URL 里（浏览器历史、截图、日志）。
- 面板默认密码 `admin`（本机已改，勿回退）。

## 处置"疑似泄露"的顺序

1. 立即停服务（托盘退出）；
2. 换面板密码 + 轮换 API Key；
3. 到上游重新登录/刷新账号（旧 token 失效）；
4. 检查 `git log --all -S '<疑似字符串>'` 确认没进过仓库；
5. 若有备份/镜像：删除对应文件；
6. 记录进 `maintenance/LOCAL-LOG.md`。
