# WorkBuddy 会话港

WorkBuddy 会话港是一个 macOS 本地工具，用来管理 WorkBuddy/Cockpit 账号、刷新额度，并在账号切换后同步本地会话记录。它通过本机文件和 SQLite 工作，不上传 WorkBuddy 会话内容。

## 功能

- 从 Cockpit 本地账号库读取并刷新授权与额度。
- 通过授权链接或 Cockpit JSON 文件导入账号，也支持多选导出 JSON。
- 切换账号时备份并同步 `sessions`、`automations` 归属，然后启动 WorkBuddy。
- 提供轻量 Web UI 和可调整大小的 macOS WebKit 应用壳。

账号 JSON、访问令牌、数据库和会话内容均属于敏感数据；请勿提交到 Git 或分享给他人。

## 环境要求

- macOS 13 或更新版本
- Python 3.10+；Web UI 需要 `cryptography`
- 已安装 WorkBuddy 与 Cockpit，并已有本机账号数据
- Swift/Xcode Command Line Tools（仅源码构建桌面 App 时需要）

## 快速开始

```bash
python3 -m pip install -r requirements.txt
python3 workbuddy-sync-app.py --port 7531
```

打开 `http://127.0.0.1:7531/`。构建桌面 App：

```bash
zsh build-macos-app.sh
```

应用会生成到 `~/Desktop/WorkBuddy 会话港.app`。脚本会签名并校验 App，但当前版本仍使用本机 Python 运行时；首次使用前请完成依赖安装。

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
