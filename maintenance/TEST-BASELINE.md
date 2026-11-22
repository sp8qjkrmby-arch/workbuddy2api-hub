# 测试基线（v1.10.8 自维护主干）

> 首次全量记录：2026-11-22 21:05（`--jobs 8`，耗时 59.9 秒）

## 结果

```
261 passed, 0 failed, 0 skipped          (# 146 套件：约 108 Python + 38 JS)
slowest: _test_desktop_atrest.py 32.2s, _test_growth_prereq.py 23.6s,
         _test_run_all_contracts.py 22.0s
```

| 项 | 值 |
|---|---|
| 解释器 | `C:\Desktop\wb-shell\wb-proxy\python\python.exe`（CPython **3.12.14**，与 CI 的 3.12 腿对齐） |
| Node | v24.18.0（38 个 JS 套件需要 PATH 上有 node） |
| 依赖 | **纯标准库**（无 pytest / requests / flask，不需要 venv） |
| 命令 | `python tests\run_all.py --jobs 8 --logs suite-logs` |
| 副作用 | 无（`PYTHONDONTWRITEBYTECODE=1`，套件全部用临时目录；`scripts/check_clean_checkout.py` 通过） |
| 触及生产? | 否。套件自带隔离端口/临时目录，8788 全程未被使用 |

## 修掉的一个失败

首轮 260 passed / 1 failed：`_test_release_assets.py::test_every_module_is_listed`

```
AssertionError: Lists differ: ['wb_entitlements.py'] != []
```

原因：我们为了对齐生产而从 `acc0b332` 取回了 `wb_entitlements.py`（上游 10-22 删了它，但 11-03 发布的包里仍残留，生产在跑它），而 `release/portable.txt`（打包白名单）里没有它。生产包内的 `release-manifest.json` 是**有**这一条的，所以正确做法是把它补进白名单，而不是删掉模块。

处置：`release/portable.txt` 在 `wb_egress.py` 之后插入 `wb_entitlements.py`（保持字母序），复跑 → 261/261。

> 这条失败其实是个好信号：**打包白名单与磁盘文件的一致性是被测试钉住的**，我们照抄生产的做法才让它变绿。

## 这个基线说明什么

1. **代码是可改的**：261 个套件全绿，意味着改动后跑一遍就能知道有没有回归（1 分钟成本）。
2. **测试重心偏后端**：`usage/pricing/aggregate` 类套件最重（`_test_usage_etag_cache.py` 单套 221 个断言），面板前端主要靠 DOM 桩（`tests/_dom_stub.js`），**真浏览器行为不在覆盖内**。
3. **外壳（`shell/launcher`）不在这批测试里**：那 31 个探针测试在旧目录 `C:\Desktop\2api\tests\`，尚未迁入，见 PLAN 的 P2-2。

## 复现方法

```powershell
cd C:\Desktop\2api维护\repo
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Desktop\wb-shell\wb-proxy\python\python.exe' tests\run_all.py --jobs 8 --logs suite-logs
& 'C:\Desktop\wb-shell\wb-proxy\python\python.exe' scripts\check_clean_checkout.py
```

单跑一个套件：`python tests\_test_xxx.py`；按名字过滤：`python tests\run_all.py usage`。
