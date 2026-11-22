> 主题: logs
> 何时读: 排查异常、怀疑有静默错误、想知道日志写在哪/怎么轮转时
> 关键词: shell.log, core-8788.log, 轮转, 0x40010004, Traceback, ERROR

# 日志

## 位置与轮转

| 文件 | 内容 | 轮转 |
|---|---|---|
| `C:\Desktop\wb-shell\data\shell.log` | **外壳**日志（启动、托盘、看门狗、更新检查、面板密码动作） | 2 MB × 3 |
| `C:\Desktop\wb-shell\data\core-8788.log` | **内核** stdout/stderr（启动、账号、请求、错误） | 8 MB × 3 |
| `C:\Desktop\2api维护\logs\restart-*.log` | 我们的重启脚本日志（UTF-8） | 手动 |
| `C:\Desktop\2api维护\logs\run_all-*.log` | 全量测试输出 | 手动 |
| `repo\suite-logs\` | 每个测试套件的完整输出（gitignore） | 每次覆盖 |

> 早期判断"日志没轮转"是错的：**轮转是有的**（外壳 2 MB×3、内核 8 MB×3），风险在别处（增长与关键信息被冲掉）。

## 当前统计（2026-11-22 采集）

| 指标 | 值 |
|---|---|
| `shell.log` | 404 KB；**0 Traceback / 126 ERROR / 185 WARN**，主体是更新检查 404 噪音 |
| `core-8788.log` | 6.4 MB |
| 内核启动次数 | 82 次，其中 **38 次退出码 `0x40010004`**（Windows 强制终止） |

## 健康判据（按优先级）

1. `data\shell.log` 出现 `内核已就绪：账号 N 个（可用 M），版本 x.y.z` ← 最有用的一行；
2. 无新增 Traceback；
3. `/health` 200。

## 常用查看命令

```powershell
# 外壳日志尾部（UTF-8）
Get-Content C:\Desktop\wb-shell\data\shell.log -Tail 30 -Encoding UTF8

# 内核日志尾部
Get-Content C:\Desktop\wb-shell\data\core-8788.log -Tail 50

# 统计错误量
(Select-String -Path C:\Desktop\wb-shell\data\shell.log -Pattern 'Traceback|ERROR').Count

# 找 404 噪音（冻结更新后应停止增长）
(Select-String -Path C:\Desktop\wb-shell\data\shell.log -Pattern '404').Count
```

## 已知噪音（不用管）

| 噪音 | 来源 | 处置 |
|---|---|---|
| `releases` 404 | 外壳每 6h 检查更新，仓库已删 | 已 `check_update_on_launch=false`，**下次重启后不再出现** |
| `catalog : app cache not found` | 指向桌面 App 缓存路径，实际清单走实时模型 API | 无害 |
| `pywebview：可用 unknown` | 版本探测方式受限 | 无害 |

## 真正要盯的两类

1. **`0x40010004`**：谁在杀内核？根因未查（见 `13-known-issues.md`）；
2. **上游风控类错误**（"没通过渠道校验"、403/401 批量出现）：这是业务断供的前兆，比代码错误更致命。
