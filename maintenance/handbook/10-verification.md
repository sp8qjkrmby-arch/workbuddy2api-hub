> 主题: verification
> 何时读: 改完代码要验收、想确认"仓库==生产"、准备提交/发版前
> 关键词: 基线校验, 冒烟, run_all, 261, check_clean_checkout, 61 MATCH

# 三道验收门（从小到达，成本递增）

## 门 1：基线校验（10 秒）—— "仓库还是不是生产那份代码"

```powershell
cd C:\Desktop\2api维护\repo
powershell -ExecutionPolicy Bypass -File maintenance\verify-trunk-baseline.ps1
```

期望输出：`MATCH 61 / MISSING-EXPECTED 3 / DIFF 0`，退出码 0。

| 状态 | 含义 |
|---|---|
| `MATCH 61` | 与生产 `baseline-core-sha256.txt` 逐字节一致（64 条减去下面 3 条） |
| `MISSING-EXPECTED 3` | `release-manifest.json`（构建产物）+ 2 个 `wb_proxy.py.bak-*`（本地备份），不该入库 |
| `DIFF n` | **有人改了载荷** → 要么回退，要么在提交正文里解释并更新基线 |

## 门 2：全量测试（60 秒）—— "有没有回归"

```powershell
cd C:\Desktop\2api维护\repo
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Desktop\wb-shell\wb-proxy\python\python.exe' tests\run_all.py --jobs 8 --logs suite-logs
& 'C:\Desktop\wb-shell\wb-proxy\python\python.exe' scripts\check_clean_checkout.py
```

当前基线：**261 passed / 0 failed / 0 skipped，59.9 秒**（146 套件，CPython 3.12.14 + node v24）。

- 单跑一个：`python tests\_test_xxx.py`
- 按名过滤：`python tests\run_all.py usage`
- 必须用 `wb-proxy\python`（3.12），别用系统 3.14（CI 的腿是 3.9/3.12）
- 必须 `PYTHONDONTWRITEBYTECODE=1`，否则仓库里堆 `__pycache__`
- 跑完 `check_clean_checkout.py` 应输出 `checkout is clean`

覆盖盲区：面板前端只有 DOM 桩（`tests/_dom_stub.js`），**真浏览器行为没测**；外壳（`shell\launcher`）不在这批测试里。

## 门 3：冒烟门禁（2 分钟）—— "端到端还能不能通到上游"

上游仓库 `CONTRIBUTING.md` 第 13~20 行把它定为**硬性门禁**：提交前必须过，四项全绿。

| 项 | 判据 |
|---|---|
| 服务 | 隔离端口（**8799**）起实例，`/health` 200，启动日志无 Traceback/ERROR |
| 面板 | `GET /` 200，`Content-Length` ≈ 1,071,023 B，关键 DOM id 存在，未登录 `/settings` 应 401 |
| **端到端** | `/v1/models` 200；`POST /v1/chat/completions` 200 且拿到**真实模型回复**（不是错误 JSON） |
| 隔离 | **绝不碰 8788**；用独立 `--accounts-dir/--usage-dir`；跑完杀进程、删含 token 的临时目录 |

实测记录（2026-11-22）：`SMOKE_OK`，2.10 s，49 tokens，22 个模型。证据见 `reports\07-smoke-test.md`。

## 三道的用法

| 场景 | 跑哪几道 |
|---|---|
| 只改文档/脚本 | 门 1 |
| 改载荷代码 | 门 1 + 门 2 |
| 改外壳或出发布包 | 门 1 + 门 2 + 门 3 |
| 升级内核 | 门 1（新包先过）+ 门 3（旁路实例）→ 再动生产 |
