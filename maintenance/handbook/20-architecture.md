> 主题: architecture
> 何时读: 要改内核、理解一次请求怎么走、找某个功能在哪个模块时
> 关键词: 模块, 请求链路, SSE, 账号池, 冷却, 端点, wb_proxy

# 架构（够用就好）

## 形态

单文件巨石为核心的 Python 标准库 HTTP 服务：

- 入口：`wb_proxy.py`（**23 535 行 / 1.16 MB**，`ThreadingHTTPServer` 子类，backlog 128）
- 面板：`dashboard.html`（**1.07 MB / 18 279 行**，无构建步骤，单文件含 HTML+CSS+JS）
- 依赖：**纯标准库**（无第三方包，便携包用 CPython 3.12 + 精简运行时）

## 一次 chat 请求怎么走

```
客户端(POST /v1/chat/completions)
  → Handler.do_POST（上百条 if path == 线性分发）
  → 鉴权（API Key / 面板会话）
  → build_upstream_body（协议转换：OpenAI / Anthropic / Codex 三种入参归一）
  → open_upstream（日限额守卫 + 账号池亲和/权重轮换）
  → wb_accounts.urlopen（keep-alive 连接池、出口绑定）
  → 上游（WorkBuddy / CodeBuddy；失败换号/冷却）
  → SSE 转译 + 泄漏过滤（把上游格式转成客户端要的格式）
  → record_usage（写 usage.jsonl + 增量聚合缓存）
  → wb_pricing 估价
```

## 协议面（对外兼容）

| 端点 | 说明 |
|---|---|
| `POST /v1/chat/completions` | OpenAI 兼容（最常用） |
| `POST /v1/responses` | OpenAI Responses（Codex 系） |
| `POST /v1/messages`（+`/count_tokens`） | Anthropic 兼容（Claude Code） |
| `GET /v1/models` | 模型列表（需 Key） |
| `GET /health` | 健康检查（**唯一免鉴权判据**） |
| `/panel/*`、`/api/*` | 面板与其后端 |

## 模块地图（37 个 `wb_*.py`）

| 模块 | 职责 |
|---|---|
| `wb_proxy.py` | 入口 + HTTP 全部分发 + 请求转换 + SSE |
| `wb_accounts.py` (273 KB) | 账号模型、登录/刷新、token、权重、冷却 |
| `wb_upstream_pool.py` | 连接池、出口键、复用与退避 |
| `wb_egress.py` | 出口（代理）级秒级退避 |
| `wb_scheduler.py` | 调度（亲和/优先级） |
| `wb_pricing.py` (101 KB) + `pricing/pricing.json` | 计价与价格表 |
| `wb_catalog.py` + `catalog/product-config-*.json` | 模型目录/产品配置 |
| `wb_settings.py` (131 KB) | 配置读写（`accounts/settings.json`） |
| `wb_media*.py`（3 个） | 多模态 |
| `wb_codearts*.py`（2 个） | 第二上游（华为云 CodeArts） |
| `wb_quota_scan.py` / `wb_tiers.py` / `wb_entitlements.py` / `wb_usage_io.py` | 额度经济 |
| `wb_update*.py`（5 个） | 自更新（对外部仓库，已冻结） |
| `wb_identity.py` / `wb_fingerprint.py` / `wb_atrest.py` | 身份、指纹、静态加密 |
| `wb_logstore.py` / `wb_activity.py` | 日志库与活动记录 |
| `wb_webagent.py` / `wb_webtools.py` / `wb_webprompts.py` / `wb_window_probe.py` | Web 能力与窗口探测 |
| `wb_modelsdev.py` / `wb_probes.py` / `wb_prompt.py` / `wb_tasks.py` | 杂项 |

## 三级冷却（理解排障的关键）

| 层级 | 触发 | 时长 |
|---|---|---|
| 账号级 | 连续失败/401 | 30 min → 6 h 熔断（`wb_accounts`） |
| 出口级 | 一次网络抖动 | 秒级退避（`wb_egress`，专治"一次抖动关掉 13 个号"） |
| 连接级 | 单条连接异常 | 复用池内替换（`wb_upstream_pool`） |

## 状态文件

- 配置真源：`accounts/settings.json`
- 账号：`accounts/*.json`（每账号一个）
- 账本：`usage/usage.jsonl`（只追加）+ 5 个增量聚合缓存（可重建）
- 缓存：`accounts/model.json`、`catalog/*`

## 改代码时的现实建议

1. **改动定位**：先 grep 端点字符串（`/v1/chat/completions`）再顺着调用走，`do_POST` 是线性 if 链，好找；
2. **别指望类型检查**：全仓库无 lint / mypy / 依赖锁，157 处宽泛 `except Exception`；
3. **前端改动**：`dashboard.html` 单文件 18 k 行，只有 DOM 桩测试，改完必须真开面板点一遍。
