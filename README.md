# AI 账号坞

AI 账号坞（原 WorkBuddy 会话港）是一个 macOS 本地工具，多平台可插拔地管理 WorkBuddy / Trae Work / 千问办公的账号授权与切换，并为 WorkBuddy 提供会话同步与额度刷新。它通过本机文件和 SQLite 工作，不上传会话内容。

## 功能

- 从 Cockpit 本地账号库读取并刷新授权与额度。
- 通过授权链接或 Cockpit JSON 文件导入账号，也支持多选导出 JSON。
- 切换账号时备份并同步 `sessions`、`automations` 归属，然后启动 WorkBuddy。
- 提供轻量 Web UI 和可调整大小的 macOS WebKit 应用壳。

账号 JSON、访问令牌、数据库和会话内容均属于敏感数据；请勿提交到 Git 或分享给他人。

## 需要配置什么

本项目没有 API Key、账号密码或 `.env` 配置。它直接读取当前 Mac 上已有的 WorkBuddy/Cockpit 数据，因此首次使用前只需要准备：

1. 安装并登录 WorkBuddy 和 Cockpit，至少有一个可用账号。
2. 安装 Python 3.10+。推荐使用 Homebrew：`brew install python`。
3. 用桌面 App 会找到的同一个 `python3` 安装依赖：

   ```bash
   python3 -m pip install -r requirements.txt
   ```

   桌面壳按顺序查找 `/opt/homebrew/bin/python3`、`/usr/local/bin/python3`、`/usr/bin/python3`；如果这些解释器中有多个，请对实际使用的那个执行安装命令。
4. 确认本机存在 Cockpit 账号库：`~/.antigravity_cockpit/`。该目录由 Cockpit 自动创建，不要手动填写密钥。

工具会自动读取以下本地文件：

- `~/.antigravity_cockpit/workbuddy_accounts/`：授权账号和 token
- `~/.antigravity_cockpit/secure-account-storage.key`：Cockpit 加密密钥
- `~/.workbuddy/workbuddy.db`：会话与自动化任务
- `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`：当前登录身份

## 快速开始

```bash
python3 workbuddy-sync-app.py --port 7531
```

打开 `http://127.0.0.1:7531/`。首次授权可在页面点击“生成授权链接”，复制链接到浏览器完成登录；也可以点击“导入 JSON 文件”导入 Cockpit 导出的账号 JSON。导出的账号 JSON 默认保存到 `~/Downloads/`，包含敏感 token，请妥善保管。

构建桌面 App：

```bash
zsh build-macos-app.sh
```

应用会生成到 `~/Desktop/AI 账号坞.app`。脚本会签名并校验 App，但当前版本仍使用本机 Python 运行时；首次使用前必须完成上面的依赖安装。

如果页面显示“无法读取 Cockpit 账号库”，通常是 Cockpit 尚未登录、账号库目录不存在，或 `cryptography` 安装到了另一个 Python 解释器。

## CLI 同步

```bash
python3 workbuddy-session-sync.py --dry-run
python3 workbuddy-session-sync.py --merge-once
python3 workbuddy-session-sync.py --watch
```

写入数据库前请退出 WorkBuddy 并先备份 `~/.workbuddy/workbuddy.db`。同步只处理本地归属，不替代 Cockpit 的账号授权或云端数据同步。

## 开发验证

```bash
python3 -m py_compile workbuddy-session-sync.py workbuddy-sync-app.py
python3 workbuddy-sync-app.test.py
git diff --check
```

## 许可证

本项目采用 MIT License，详见 [LICENSE](LICENSE)。
