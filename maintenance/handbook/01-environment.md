> 主题: environment
> 何时读: 换机器、排查"命令找不到/连不上网/端口占用"、确认工具版本时
> 关键词: Windows, Python, node, 代理, 7897, 端口, 8788, 63045, 硬件

# 环境（这台机器）

## 硬件 / 系统

| 项 | 值 |
|---|---|
| OS | Windows 11 专业工作站版 10.0.26200 |
| 主机名 | JARVISPC |
| CPU | 16 逻辑核（测试脚本 `--jobs 8` 是安全并发上限） |
| 内存 | 31.8 GB |
| C: 盘 | 已用 747 GB / 可用约 206 GB |
| 用户目录 | `C:\Users\63648` |

## 解释器与运行时

| 用途 | 路径 | 版本 |
|---|---|---|
| 系统 Python | `python`（PATH） | 3.14.4 |
| 外壳（launcher）运行时 | `C:\Desktop\wb-shell\runtime\python.exe` / `pythonw.exe` | **3.14.4**（外壳和内核都用它启动） |
| 便携版内核运行时（发布包自带） | `C:\Desktop\wb-shell\wb-proxy\python\python.exe` | **3.12.14** ← **跑测试用这个**，对齐上游 CI 的 3.12 腿 |
| Node | PATH 上 `node` | v24.18.0（38 个 JS 测试套件需要） |
| git | `C:\Program Files\Git` | 2.54.0.windows.1 |
| 凭据管理器 | GCM 2.7.3，由系统级 `credential.helper=manager` 解析 | 浏览器 OAuth，存 Windows 凭据管理器 |
| gh CLI | **没有安装**（所以发布/推送全靠 git + GCM） | — |
| PowerShell | 7.6.6（Store 版，路径含 `WindowsApps`，**会随商店更新变化**）+ 系统 5.1 | 计划任务里一律用 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` |

## 网络

- **GitHub 直连会被重置**（`git clone` 报 `Recv failure: Connection was reset`）。
- 本机有本地代理：`http://127.0.0.1:7897`（Clash 类），访问 GitHub 前必须设：
  ```powershell
  $env:HTTPS_PROXY='http://127.0.0.1:7897'; $env:HTTP_PROXY='http://127.0.0.1:7897'
  ```
- 业务上游（腾讯 WorkBuddy / CodeBuddy）**不需要代理**，直连即可（`egress=direct`）。

## 本机相关端口

| 端口 | 用途 |
|---|---|
| **8788** | **wb-proxy 生产内核**（`127.0.0.1`，本文件所有"生产"指它） |
| 8799 | 冒烟/旁路实例专用（约定，勿与生产混用） |
| 18788 | 旧实验目录 `C:\Desktop\2api` 的内核端口 |
| 63045 | DeepSeek Harness Web GUI（本对话的界面） |
| 7897 | 本地 HTTP 代理 |
| 18899 / 18899+ | 旧外壳测试用的隔离内核端口（`tests\update_isolated_test.py`） |

> 关于**我自己**：这个对话的模型请求也经过 8788。所以任何重启生产的动作都必须**脱离当前会话**执行（见 `06-runbook-start-stop.md`）。
