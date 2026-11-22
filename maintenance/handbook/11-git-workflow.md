> 主题: git
> 何时读: 提交/推送/建 tag、认证报错、别人克隆后脚本坏了时
> 关键词: push, GCM, OAuth, GH007, filter-branch, credential.helper, tag, eol, crlf

# Git 工作流（本仓库特有）

## 远端与现状

| 项 | 值 |
|---|---|
| origin | `https://github.com/sp8qjkrmby-arch/workbuddy2api-hub.git`（公开；**建议改成 private**，理由见 `15-security.md`） |
| 分支 | `main`（自维护主干） |
| tag | `v1.10.8-local.2`（锚点，附注标签）、`legacy-fork-v1.7.4`（旧快照留档） |
| 认证 | Git Credential Manager 浏览器 OAuth，令牌存 Windows 凭据管理器（DPAPI）；**不需要 PAT** |

## 日常推送

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'; $env:HTTP_PROXY='http://127.0.0.1:7897'
cd C:\Desktop\2api维护\repo
git add -A; git commit -m "<type>(<scope>): 摘要"
git push origin main
git push origin --tags        # 有新 tag 时
```

## 踩过的坑（都在这里踩过一遍）

### 1. GH007：私密邮箱导致推送被拒

```
remote: error: GH007: Your push would publish a private email address.
! [remote rejected] main -> main (push declined due to email privacy restrictions)
```

处置（只需做一次，已做过）：

```powershell
git config --local user.email "242735504+sp8qjkrmby-arch@users.noreply.github.com"
git filter-branch -f --env-filter "export GIT_AUTHOR_EMAIL=242735504+sp8qjkrmby-arch@users.noreply.github.com; export GIT_COMMITTER_EMAIL=242735504+sp8qjkrmby-arch@users.noreply.github.com; export GIT_COMMITTER_NAME=`$GIT_AUTHOR_NAME" -- 2fa6287..HEAD
```
`2fa6287` 是上游最后一条提交，这样只重写"我们自己的提交"，641 条上游历史不动。
重写后 tag 要重建（旧 tag 指向被替换的提交）。

### 2. `credential.helper` 路径没加引号

`C:\Users\63648\.gitconfig` 里曾写着 `C:/Program Files/Git/mingw64/bin/git-credential-manager.exe`（无引号），git 通过 sh 执行会被空格截断 →
`line 1: C:/Program: No such file or directory` + `git: 'credential-C:/Program Files/...' is not a git command`。
**处置**：删掉该覆盖，用系统级 `credential.helper=manager`（实测可用）。

### 3. `.cmd` 必须 CRLF + ASCII

上游 `.gitattributes` 的 `* text=auto eol=lf` 会把 `.cmd` 归一化成 LF；cmd.exe 按行解析，LF 会把
`set HTTPS_PROXY=http://127.0.0.1:7897` 截断成 `PROXY=...` → `'PROXY' 不是内部或外部命令`。
**已修**：`.gitattributes` 补 `*.cmd text eol=crlf`；`.cmd` 内容改纯 ASCII；中文路径用 `%~dp0` 代替。

### 4. 直连 GitHub 失败

`git clone` 报 `Recv failure: Connection was reset` → 必须设代理（见 `01-environment.md`）。

## 提交信息规范（沿用上游 `CONTRIBUTING.md`）

- 主题：`<type>(<scope>): 摘要`，简洁；
- 正文：**中英双语**（中文为主，末尾一段英文）；
- 一个提交一件事；
- **提交前必须过冒烟门禁**（见 `10-verification.md` 门 3）。

## 分支/tag 约定（我们自己的）

| 名字 | 用途 |
|---|---|
| `main` | 自维护主干（永远可通过门 1+门 2） |
| `v1.10.8-local.N` | 我们的发布锚点（附注标签，含说明） |
| `legacy-fork-v1.7.4` | 旧 fork 快照，只读留档 |

## 想改回旧状态

```powershell
git reset --hard legacy-fork-v1.7.4     # 未推送的改动才会丢
git revert <commit>                     # 已推送的，走 revert
```
