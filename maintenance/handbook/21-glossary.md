> 主题: glossary
> 何时读: 遇到看不懂的专有名词/缩写/自定义术语时
> 关键词: 术语, 外壳, 载荷, 基线, 双轨, portable.txt, PATCHED-BY-OPS, egress

# 术语表

| 术语 | 含义 |
|---|---|
| **内核 / core** | `wb_proxy.py` 及其 36 个 `wb_*.py` 模块，真正的反代网关 |
| **外壳 / shell / launcher** | `C:\Desktop\wb-shell\launcher\` 下的自研桌面层（托盘、面板宿主、更新、看门狗） |
| **载荷 / payload** | 发布包里承载功能的文件（`wb_*.py` + `dashboard.html` 等 60 个），与"运行时/数据"相对 |
| **基线 / baseline** | `data\baseline-core-sha256.txt`：64 条哈希，用于判断内核文件是否被本地改过 |
| **双轨（版本）** | release tag 名与包内 `version` 字段不一致的现象（如 tag v2.1.3 ↔ version 1.10.8） |
| **portable.txt** | 便携包的**受管文件白名单**；更新器只替换/删除它列出的文件，是"这个 release 拥有哪些文件"的权威答案 |
| **release-manifest.json** | 打包时生成的清单（含 version/tag/files/runtime），不是源码，也不该入库 |
| **protected 区** | manifest 里声明的 `accounts/` 与 `usage/`：升级永不覆盖 |
| **account pool / 账号池** | 多个 WorkBuddy 账号轮流承接请求；有亲和、权重、冷却 |
| **亲和 (affinity)** | 同一会话尽量粘同一个账号，避免上下文割裂 |
| **冷却 (cooldown)** | 失败后暂时不用某账号/出口，分三级（账号/出口/连接） |
| **egress** | 出口（代理）路径；`egress=direct` 表示直连不走代理 |
| **realm** | 账号所属地区域（本机 9 个账号全 `cn`） |
| **upstream** | 上游服务（腾讯 WorkBuddy / CodeBuddy / 华为云 CodeArts） |
| **SSE 转译** | 把上游流式格式转成客户端要的 `chat.completions` / `messages` 流 |
| **PATCHED-BY-OPS** | 上游代码里 5 处标记，表示"运维就地打的补丁"，无版本契约，同步上游时容易被覆盖 |
| **revert-guard** | 上游 CI 的一个检查：确保合并结果没把 base 新增的代码弄丢（因 issue #270 事故而加） |
| **矩阵 / matrix legs** | CI 的三条测试腿：py3.9 / py3.12 / win3.12 |
| **DPAPI** | Windows 数据保护 API，用户级加密；`panel-password.sealed` 用它 |
| **GCM** | Git Credential Manager，负责 GitHub 的浏览器 OAuth 与凭据存储 |
| **GH007** | GitHub 拒绝推送的错误码：提交邮箱是私密邮箱 |
| **旁路实例** | 用 8799 等端口 + 独立数据目录起的临时实例，用来验证而不碰生产 |
| **门 1/2/3** | 基线校验 / 全量测试 / 冒烟门禁（见 `10-verification.md`） |
| **0x40010004** | `STATUS_CONTROL_C_EXIT` 之类的 Windows 强制终止码；内核日志里出现过 38 次 |
| **DSH** | DeepSeek Harness，本对话所用工具；它的模型请求也经过 8788 |
