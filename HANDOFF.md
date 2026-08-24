# Handoff · AI 账号坞

> 最近更新：2026-08-24
> 仓库：`/Users/Zhuanz/Documents/project/workbuddy-session-sync`
> 定位：本地 macOS 账号管理工具；Python HTTP 服务 + WebKit 壳，默认端口 `7531`。

## 正在进行的任务

- Trae 本地会话采用降级路线：切换前保存来源账号的会话索引，切换后在账号坞内识别并只读打开旧账号会话。
- 未开始该路线的编码；当前只完成了研究、真实读取验证和设计。

## 已完成的事项

- WorkBuddy、Trae、千问办公的账号提取和切换均已实现；Trae、千问切换均包含启动后登录校验与失败回滚。
- Trae：已验证当前账号可按已知会话 ID 读取另一账号的元数据和完整正文。
- Trae：已验证运行时原型能在原生侧栏显示并打开旧账号会话；该效果不持久，未保留代码。

## 当前遇到的卡点问题

- Trae `fork_session` 会复制正文，却把 `mode/work_mode` 留空；原生侧栏按 mode 过滤，因此复制结果不可见。
- 未发现公开接口可更新既有会话 mode；外部 SQLCipher 写库没有安全稳定的密钥路径。

## 后续的计划安排

1. 切换前分页扫描旧账号的 `code/work/design` 会话，原子保存来源索引。
2. 在账号坞增加按来源账号分组的“本机历史会话”列表和只读消息阅读器。
3. 用 B→A→重启→A 读取 B 正文的真实流程验收；不能以“账号切换成功”替代“会话可读”。

## 过往踩过的坑（避免重复犯错）

- “会话复制成功”不等于“原生列表可见”；必须读回 mode，并以 Trae 原生侧栏验收。
- `chat_migrate` 返回成功不代表历史已导入；现场生成过空白且 `mode=null` 的会话。
- 运行时 store 注入只证明显示可行，刷新、退出或切换账号即丢失，不能表述为持久合并。

## 平台状态

| 平台 | 账号导入与切换 | 会话能力 | 当前状态 |
|---|---|---|---|
| WorkBuddy | OAuth、Token、JSON、本地扫描、2FA；切换后归并会话 | SQLite 按 `user_id` 归并 | 稳定 |
| Trae Work | iCube `storage.json` 提取；退出→注入→启动校验→失败回滚 | 旧账号会话按 ID 可读；持久归并暂停 | 下一步做只读历史索引 |
| 千问办公 | Electron safeStorage 提取和加密注入 | 未实现 | 账号功能稳定 |

### 关键实现位置

- `workbuddy-sync-app.py`：本地 HTTP 服务、Web UI、WorkBuddy 逻辑。
- `workbuddy-session-sync.py`：WorkBuddy CLI 同步器，支持 `--dry-run`、`--merge-once`、`--watch`。
- `platforms/trae.py`：Trae 账号提取、切换、云端会话。
- `platforms/qwen.py`：千问账号提取、切换。
- `trae-local-session-export.js`：官方 Trae 本地会话 Markdown 导出器。
- `macos/WorkBuddySyncApp.swift` 和 `build-macos-app.sh`：桌面壳与打包。

### 本地账号存储

| 路径 | 用途 |
|---|---|
| `~/.antigravity_cockpit/workbuddy_accounts/` | WorkBuddy 账号 |
| `~/.antigravity_cockpit/trae_work_accounts/` | Trae 账号 |
| `~/.antigravity_cockpit/qwen_accounts/` | 千问账号 |
| `~/.antigravity_cockpit/secure-account-storage.key` | Cockpit 账号信封共用密钥 |

账号写入必须使用现有的原子写入函数，文件权限保持 `0600`。不得提交 token、导出账号、数据库、会话正文或构建产物。

## 常用验证

```bash
cd /Users/Zhuanz/Documents/project/workbuddy-session-sync

/opt/homebrew/bin/python3 -m py_compile workbuddy-session-sync.py workbuddy-sync-app.py platforms/*.py
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
node trae-local-session-export.test.js

/opt/homebrew/bin/python3 workbuddy-sync-app.py --no-browser --port 7531
/opt/homebrew/bin/python3 workbuddy-session-sync.py --dry-run
zsh build-macos-app.sh
```

数据库写入前：备份 WorkBuddy 数据库并尽量退出 WorkBuddy；Trae 不直接操作 SQLCipher 数据库。

## Trae 本地会话：决策记录

### 已验证的事实

- 本地正文位于 `~/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/database.db`，为 SQLCipher 加密库。
- 官方只读出口为 `lite/export_past_chat`、`lite/get_chat_session` 和 `lite/get_messages`，不需要数据库密钥。
- `snapshot/<24位会话ID>/` 可提供本机会话 ID 候选；现场发现 34 个，按 ID 均能读到元数据。
- `state.vscdb` 的 key 带账号 UID 前缀，可为多数会话恢复来源账号；标题和正文仍以 Lite API 返回为准。
- 原生列表链路为 `list_chat_sessions → DataSource → sessionDomainService.appendSessions → sessionStore.allSessionsForSidebar`，并按项目归属和 `code/work/design` 过滤。
- 切换成功后必须重新提取 `storage.json`，因为 Trae 会轮换 token。

### 真实测试矩阵

| 测试 | 结果 | 结论 |
|---|---|---|
| 当前账号按旧账号已知 ID 调 `get_chat_session` | 成功 | 跨账号读取元数据可行 |
| 当前账号按同一 ID 调 `get_messages` | 成功，真实测试会话 2 条消息完整 | 跨账号读取正文可行 |
| `fork_session` 跨账号复制 | 标题和 2 条消息被复制 | 正文复制能力存在 |
| fork 后读取 `mode` | `null` | 侧栏会过滤复制结果 |
| fork/commit 追加 `mode/work_mode` | 被忽略 | 不能靠请求参数修复 |
| 创建 `mode=code` 会话 | 可见但为空 | 不能作为历史导入容器 |
| `chat_migrate` | 成功响应但生成空白、`mode=null` 会话 | 不能作为现成合并接口 |
| `teleport_session` | `unknown method`，code `1010003` | 当前版本不可用 |
| 运行时注入侧栏 | 显示并打开完整正文 | 仅内存效果，不可持久 |

### 已排除的路径

- 外部直连 ai-agent：它使用进程内 socketpair，没有公共 IPC 入口。
- `frb_api`/8717：属于本机分润宝项目，与 Trae 会话无关。
- 云端 `chat_sessions`：服务端会话校验拒绝，不能替代本地读取。
- Keychain/PBKDF2 候选：已验证的 Trae Safe Storage 主密钥不是数据库 key。
- SQLCipher 外部直写：无公开、安全、版本稳定的密钥路径。
- `send_message/append_msg`：只能发送新消息，不能重放保留角色和时间的历史。

### 选定方案：只读历史索引

不把会话写进新账号名下；保留来源和元数据，在账号坞内读取。

```text
账号 B 仍登录
  → 分页扫描 code/work/design 会话并原子保存索引
  → 切换并校验账号 A
  → 账号坞按来源账号显示“本机历史会话”
  → 按会话 ID 调 get_chat_session/get_messages 只读打开
  → 原始记录不可读时，显示失败状态；可选回退 Markdown 备份
```

建议索引文件：`~/.antigravity_cockpit/trae_local_session_inventory.json`，权限 `0600`，临时文件 → `fsync` → `os.replace`。

每条只保存：来源 UID、Cockpit account ID、session ID、标题、mode、状态、项目路径、创建/更新时间、最近验证时间。不要保存 token 或默认复制正文。

UI 规则：按“当前账号 / 旧账号 / 来源待确认”分组；只读抽屉显示消息；明确标注“可读取，未导入”；暂不提供跨账号继续对话。

### 降级方案验收

1. 在账号 B 选定一条带唯一标题的本地会话。
2. 通过账号坞切到 A，历史列表仍显示该会话并标注来源 B。
3. 打开后可读取原用户消息和 Trae 回复。
4. 重启账号坞、重启 Trae 后，索引仍存在且可按 ID 读取。
5. 切回 B 后，该会话仍在 B 原生侧栏；不得发生过户、删除或重复复制。
6. 任一步读取失败时显示“索引存在、正文当前不可读”，不得误报成功。

### 边界与清理

- 运行时注入原型没有修改数据库，但已撤回，未保留半成品代码。
- fork/create/migrate 测试产生的临时会话已通过官方接口清理。
- 写接口探测前创建的 Trae 数据库备份仍保留在本机 `ModularData/ai-agent` 下；未提交、未删除。
- 当前 Trae 已停止运行。

## 其他未完成项

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | Trae 只读会话索引和历史阅读器 | 见上文选定方案 |
| P1 | 千问云端会话浏览 | 尚未研究会话 API |
| P1 | iOS 授权弹窗 | WebView 交互未适配 |
| P2 | Trae 持久原生归并 | 仅当只读方案不满足需求时再研究 |

## 运维边界

- WorkBuddy 写库前备份并退出应用，避免 WAL 锁。
- Trae 不直接修改 SQLCipher 数据库；只通过官方 Lite API 读取或导出。
- `open_authorization_url` 只能打开 CodeBuddy 白名单域名。
- 2FA 仅本地计算，不持久化、不上传。
- 桌面 App 重建时，`build-macos-app.sh` 会将旧版移入废纸篓并生成新版；完成后运行 `codesign --verify --deep --strict`。
