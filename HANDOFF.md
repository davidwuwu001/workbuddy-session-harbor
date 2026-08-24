# AI 账号坞 · 交接清单

> 编写时间：2026-08-24（最近更新：2026-08-24，Trae 只读会话导出突破）
> 关联仓库：`/Users/Zhuanz/Documents/project/workbuddy-session-sync`
> 当前定位：**多平台可插拔的 AI 办公平台账号管理工具**，模仿 Cockpit 做成模块化，单进程单端口（7531）承载多平台适配器。
>
> **本次固化范围**：账号授权、账号切换、账号导入/导出、千问适配器已稳定（测试全绿、已提交推送）。未实现项见第六、七节及专项攻坚文档。

## 正在进行的任务

- Trae 本地会话能力已从“持久导入当前账号”降级为“切换前保存来源索引、切换后只读识别和打开旧账号会话”；本轮先完成研究总结，尚未开始降级方案编码。

## 已完成的事项

- 已验证当前 Trae 账号能按已知会话 ID 读取另一账号的元数据和完整正文。
- 已验证运行时原型可以把旧账号会话显示在 Trae 原生侧栏并打开正文，但该效果不持久，未作为正式代码保留。
- 本页第十章已合并 Trae 的完整研究、真实测试、失败接口和降级设计。

## 当前遇到的卡点问题

- `fork_session` 会复制正文但把 `mode` 写成空值；Trae 原生侧栏按 mode 过滤，因此复制结果不可见。
- 没有公开接口可更新既有 session 的 `mode/work_mode`，也没有安全稳定的 SQLCipher 外部写入路径。

## 后续的计划安排

1. 在账号切换前分页扫描旧账号的 `code/work/design` 会话并原子保存来源索引。
2. 在账号坞增加按来源账号分组的“本机历史会话”只读列表与消息阅读器。
3. 用 B→A→重启→A 读取 B 正文的真实流程验收，不再以“账号切换成功”代替“会话可读”。

## 过往踩过的坑（避免重复犯错）

- “会话复制成功”不等于“原生列表可见”；必须读回 `mode` 并用 Trae 原生侧栏验收。
- `chat_migrate` 返回成功不代表历史已导入；现场生成了空白且 `mode=null` 的会话。
- 运行时 store 注入只证明显示可行，退出或刷新即丢失，不能表述为持久合并。

---

## 一、总览

AI 账号坞是一个 macOS 本地工具（Python HTTP 服务 + WebKit 壳），通过适配器层管理多个 AI 办公平台的账号授权、切换、会话同步。账号库与 Cockpit 完全互认，统一存放在 `~/.antigravity_cockpit/`，共用同一把 `secure-account-storage.key`（AES-256-GCM 信封）。

桌面 App：`~/Desktop/AI 账号坞.app`（ad-hoc 签名，重建自动删旧留新）。原名"WorkBuddy 会话港"，2026-08-24 更名。

---

## 二、三平台状态矩阵

| 平台 | 适配器 | 授权导入 | 切换账号 | 会话同步 | 云端会话 | 额度 | 状态 |
|------|--------|---------|---------|---------|---------|------|------|
| **WorkBuddy** | 内置（workbuddy-sync-app.py） | ✅ OAuth state+poll / Token / JSON / 本地导入 / 2FA TOTP | ✅ auth.info 注入切换 | ✅ SQLite user_id 归并 | — | ✅ Cockpit 资源包刷新 | **稳定** |
| **Trae Work** | platforms/trae.py | ✅ storage.json iCube 提取（双 App） | ✅ 退出→备份→注入→重启→校验 | 🟡 旧账号会话按 ID 可读；持久归并暂停 | ✅ Cloud-IDE-JWT 双网关 | — | **账号稳定，下一步做只读历史索引** |
| **千问办公** | platforms/qwen.py | ✅ safeStorage 提取（已破解） | ✅ 退出→备份→加密注入→重启→校验 | 🔴 未实现（待研究） | — | — | **账号功能稳定，会话同步未开始** |

> 本次固化的"已跑通"功能 = 上表所有 ✅ 项：**账号授权（导入）与账号切换，三个平台全部可用**。未实现项（两个平台的会话同步）见第六节。

### 2.1 WorkBuddy（最完整）

- **授权**：OAuth 2.0 设备授权模式变体。`POST /v2/plugin/auth/state?platform=workbuddy` 拿 state → 浏览器打开授权页 → 每 2s 轮询 `/v2/plugin/auth/token` 换 accessToken/refreshToken → `/v2/plugin/login/account` 拉 profile 落库。有效期 600s。
- **导入**：弹窗 4 个 tab（OAuth 授权 / Token 直接粘 / JSON 文件 / 本地扫描）；Token 导入给 refresh_token 时先经官方刷新接口校验。
- **2FA**：标准库实现 RFC 6238 TOTP，兼容 Base32 与 `otpauth://`，密钥不离开本机。
- **切换**：原子替换 `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`，重启 WorkBuddy 校验实际 UID 后再归并会话。
- **会话同步**：SQLite `sessions`/`automations` 表按 `user_id` 归并到主账号，支持 `--dry-run`/`--merge-once`/`--watch`。

### 2.2 Trae Work（核心已通，只读会话导出已解决）

- **双 App**：`solo_cn` = TRAE SOLO CN（工作台），`trae_cn` = Trae CN（IDE）。网关 `trae-api-cn.mchost.guru` / `work.enterprise.trae.cn`，鉴权 `Cloud-IDE-JWT`。
- **提取**：解密 `~/Library/Application Support/TRAE SOLO CN/User/globalStorage/storage.json` 的 iCube 信封（AES-128-CBC，PREFIX+salt 方案），账号存入 `~/.antigravity_cockpit/trae_work_accounts/`。
- **切换**：退出 App → 备份 storage.json（保留近 10 份）→ 注入 storage_payload → 重启 → 12s 后校验实际 UID；失败自动恢复原登录文件，成功后重新提取 Trae 轮换的新凭证。
- **✅ 本地正文只读导出**：已验证官方 `lite/export_past_chat`，无需 SQLCipher 密钥；新增 `trae-local-session-export.js`，通过 `--remote-debugging-pipe` 临时实例导出 Markdown，不开放 TCP 调试端口。
- **✅ 跨账号按 ID 读取**：当前账号可读取旧账号会话元数据与完整正文；真实 2 条消息会话已用原生界面打开验收。
- **🔴 持久跨账号归并暂停**：`fork_session` 复制正文后丢失 `mode`，`chat_migrate` 生成空白会话；运行时原型虽可显示和打开，但刷新即失效。
- **完整证据、测试矩阵与降级方案见本页第十章**。
- **本机已有账号**：`~/.antigravity_cockpit/trae_work_accounts/` 3 个真实账号。

### 2.3 千问办公（账号功能已稳定，会话同步未实现）

- App：`QwenWorkCN.app`（`cn.qwenwork.desktop.mac` v0.1.8）。
- **登录态已破解**：`~/Library/Application Support/QwenWorkCN/auth.dat` + `auth-v2.dat`，标准 Electron safeStorage v10 信封。**破解关键**：macOS Chromium 派生是 `PBKDF2-HMAC-SHA1(keychain密码, 'saltysalt', 1003, 16)` → AES-128-CBC，IV=16 空格，v10 头 3 字节（此前误用 SHA256 才失败）。
- `auth-v2.dat` 明文是 `schemaVersion=2` JSON：`token/refreshToken/user{id,name,email,orgName,planName}/expiresAt`。
- **已实现**：capture（提取当前登录）/ switch（退出→备份→加密注入→重启→校验）/ import / status 全接口，解密+加密双向，账号库 `qwen_accounts/`（Cockpit 互认）。实测读取当前登录成功。提交 `3f407e3`。
- **🔴 会话同步未实现**：千问的云端会话 API 尚未研究（`sessions_cloud: false`）。属"后续再搞"项，不在本次固化范围。

---

## 三、代码结构

```
workbuddy-session-sync/
├── workbuddy-sync-app.py        # 主应用：HTTP 服务 + Web UI + WorkBuddy 内置逻辑
│                                #   路由：/api/* + /api/platforms + /api/platform/<id>/<action>
├── workbuddy-session-sync.py    # CLI 同步器（--dry-run/--merge-once/--watch）
├── trae-local-session-export.js # Trae 官方接口只读导出器（remote-debugging-pipe）
├── trae-local-session-export.test.js
├── workbuddy-sync-app.test.py   # 聚焦测试（auth/quota/import/TOTP/平台注册表）
├── platforms/                   # 可插拔适配器层
│   ├── __init__.py              # 注册表：get_platform / list_platforms
│   ├── trae.py                  # Trae 适配器（提取/切换/云端会话/导入）
│   └── qwen.py                  # 千问适配器（safeStorage 破解，capture/switch/import/status）
├── macos/
│   ├── WorkBuddySyncApp.swift   # WebKit 壳（端口 7531，托管 Python 服务）
│   ├── Info.plist
│   └── MakeIcon.swift
├── build-macos-app.sh           # 构建脚本（打包 workbuddy-sync-app.py + platforms/）
├── overview.md / AGENTS.md / README.md
└── ios/WorkBuddySessionHarbor/   # iOS WebView 壳（待同步授权弹窗交互）
```

**新增平台流程**：在 `platforms/` 加一个 `<id>.py`，实现 `PLATFORM_ID/PLATFORM_NAME/status()/capture()/switch()`，在 `platforms/__init__.py` 的 `REGISTRY` 登记，自动出现在 UI 平台 Tab 和路由里。

---

## 四、账号库底座（Cockpit 互认）

| 路径 | 平台 |
|------|------|
| `~/.antigravity_cockpit/workbuddy_accounts/` | WorkBuddy |
| `~/.antigravity_cockpit/trae_work_accounts/` | Trae Work |
| `~/.antigravity_cockpit/qwen_accounts/`（预留） | 千问 |
| `~/.antigravity_cockpit/secure-account-storage.key` | **共用加密密钥** |

全部 AES-256-GCM 信封格式，与 Cockpit Tools 完全互认（Cockpit 导出的 JSON 可直接导入 AI 账号坞，反之亦然）。

---

## 五、构建与验证命令

```bash
cd /Users/Zhuanz/Documents/project/workbuddy-session-sync

# 语法 + 测试（系统 python3，含 cryptography）
/opt/homebrew/bin/python3 -m py_compile workbuddy-sync-app.py platforms/*.py
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py   # 预期输出 OK

# 本地启动（不打开浏览器）
/opt/homebrew/bin/python3 workbuddy-sync-app.py --no-browser --port 7531
curl -s http://127.0.0.1:7531/api/platforms | python3 -m json.tool

# CLI 同步（写库前先退出 WorkBuddy + 备份 workbuddy.db）
/opt/homebrew/bin/python3 workbuddy-session-sync.py --dry-run
/opt/homebrew/bin/python3 workbuddy-session-sync.py --whoami

# 重建桌面 App（带时间戳新路径，不动现有 App）
zsh build-macos-app.sh   # 默认输出 ~/Desktop/AI 账号坞.app，自动删旧留新
codesign --verify --deep --strict "<App 路径>"     # 必须通过
# 验证包内 platforms：
ls "<App>/Contents/Resources/platforms/"
```

---

## 六、阻塞项与已知问题（未实现清单）

> 本次固化**不包含**以下项，均属后续工作。Trae 的完整证据已合并到本页第十章。

| 未实现项 | 平台 | 阻塞原因 | 指引文档 |
|------|------|------|------|
| 持久会话归并 | Trae Work | 复制接口丢失 `mode`，无公开 mode 更新接口；SQLCipher 外部写入不安全 | 本页第十章 |
| 云端会话浏览 | 千问办公 | 千问云端会话 API 尚未研究 | 待立项 |
| iOS 端授权弹窗 | 全平台 | 手机端 WebView 交互未适配 | 单独开一轮 |

**已解决（本次固化前遗留）**：千问 `auth.dat` 加密已破解（此前列为阻塞），账号功能已并入稳定版。

### 6.1 已排除的死路（别再走）
1. **外部直连 ai-agent 的 AHA-IPC**——进程内 socketpair，无外部入口。
2. **`frb_api`/8717 端口**——实锤是用户自己的"分润宝"项目，与 Trae 会话无关，只是借用了 TRAE 的 Python 解释器。
3. **云端 API `chat_sessions`**——token 有效但服务端 `code=1001` 会话校验拒绝。

详见本页第十章“已排除的路径”。

---

## 七、下一步优先级（未实现项的攻坚顺序）

| 优先级 | 行动 | 说明 |
|--------|------|------|
| 🟥 P0 | Trae 只读会话索引 | 切换前分页保存旧账号 `code/work/design` 会话，切换后按来源账号展示 |
| 🟥 P0 | 账号坞历史阅读器 | 按已知 ID 调 `get_chat_session/get_messages`，只读展示旧账号正文 |
| 🟧 P1 | 千问云端会话 API 研究 | 补齐千问 `sessions_cloud` 能力 |
| 🟧 P1 | iOS 端授权弹窗交互 | 手机端可用 |
| 🟩 P2 | 持久原生归并 | 仅当只读方案不满足需求时再研究；不得把运行时注入冒充持久合并 |
| 🟩 P2 | 适配器能力探测自动渲染 UI | 不同平台按 features 矩阵渲染可用操作 |

> 注：桌面 App 重建"自动删旧留新"已固化进 `build-macos-app.sh`（提交 `e353530`），不再是待办。

---

## 八、已完成工作（2026-08-23 ~ 24 时间线）

1. **OAuth 授权登录迁移**：会话港加 Cockpit 式"添加 WorkBuddy 账号"弹窗（4 tab + 2FA 工具 + 在浏览器打开 + 倒计时轮询）。`/api/auth/open`、`/api/import/token`、`/api/totp`、`/api/scan-local` 四个新路由。提交 `fc4f3f5`。
2. **7 个账号导入**：从 `~/Downloads/workbuddy_accounts_2026-08-23.json` 导入 Cockpit 账号库（导入前已备份 `.bak.20260823-231038`）。
3. **多平台架构**：`platforms/` 适配器层 + Trae 完整移植。UI 平台 Tab + 动态面板。`build-macos-app.sh` 修打包 platforms。提交 `aeca1e3`。
4. **界面侧栏导航布局（方案 A）**：提交 `2dc06a9`。
5. **build 脚本自动删旧留新**：提交 `e353530`（用户长期要求：重建桌面 App 后桌面只留一个最新版）。
6. **千问办公适配器全功能**：safeStorage 加密破解 + capture/switch/import/status，实测读取登录成功。提交 `3f407e3`。
7. **桌面精简**：多次删旧留新，桌面始终只有一个 `AI 账号坞.app`。
8. **Trae 本地会话同步攻坚**：确认 AHA-IPC 无外部入口、SQLCipher 外部直写不可取、`frb_api` 实锤无关；进一步验证跨账号按 ID 可读、复制接口丢 mode、运行时原型可显示但不持久。完整结论见第十章。

---

## 九、安全与运维注意

- **不动 `~/.workbuddy/workbuddy.db` 的写操作前必须备份**（`cp workbuddy.db workbuddy.db.bak.<ts>`），写时退出 WorkBuddy 避免 WAL 锁。
- **账号库写入**走 `write_account`/`write_cockpit_account` 原子替换（temp→fsync→replace，权限 0600）。
- **open_authorization_url 有 CodeBuddy 官方域名白名单**，防止把任意 URL 交给浏览器。
- **2FA 密钥只在本地计算**，不持久化不上传；TOTP 用标准库实现，无第三方依赖。
- **不提交**：storage.json、账号导出 JSON、workbuddy.db、token、日志、构建产物。
- **依赖**：仅 `cryptography`（读 Cockpit 加密账号库 + Trae iCube 解密），其余全标准库。

---

## 十、Trae 本地会话合并：完整研究与测试

> 日期：2026-08-24
> 范围：TRAE SOLO CN（Trae Work）账号切换与本地会话跨账号读取
> 当前决策：暂停“把旧账号会话永久导入当前账号”的实现，优先做“切换前保存索引、切换后只读识别旧会话”。

### 10.1 结论

本轮把三个容易混淆的能力拆开并分别验证：

1. **账号切换后各账号原有本地会话不会被删除**：已确认。
2. **当前账号按已知会话 ID 读取另一账号的本地会话元数据和正文**：已确认；真实测试会话的标题、用户消息和 Trae 回复均可读取。
3. **把另一账号会话永久合并进当前账号的 Trae 原生会话列表**：尚未得到安全、稳定的持久化方案。

Trae 的官方复制接口可以复制正文，但会丢失 `mode/work_mode`。Trae 原生侧栏又严格按 `mode` 过滤，所以复制结果虽然存在，却不会显示。没有找到可补写既有会话 `mode` 的公开接口，也不应绕过 SQLCipher 直接改库。

本轮还做出了一个成功的**运行时原型**：不改数据库，把旧账号会话注入当前账号的 Trae 原生侧栏后，可以显示并打开完整正文。但该结果只存在于渲染进程内存，退出或刷新 Trae 后消失，因此没有作为正式功能保留。

### 10.2 目标与验收口径

用户真正关心的不是“切换账号功能本身”，而是：

> 登录当前账号后，能否看到、识别并读取上一个账号留在本机的会话。

本轮使用了两层验收口径：

- **完整合并**：当前账号的 Trae 原生侧栏长期显示另一账号的本地会话，冷启动后仍存在。
- **降级保留**：账号坞在切换前记录旧账号会话，切换后仍能按来源账号识别并只读打开正文。

前者尚未完成；后者已有完整技术依据，可以作为下一阶段的主方案。

### 10.3 已确认的架构事实

#### 数据库存储

- 本地正文位于 `~/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/database.db`。
- 数据库为 SQLCipher 加密库，文件头不是 `SQLite format 3`，不能用系统 `sqlite3` 安全直读或写入。
- 数据表包含 `chat_turn`、`chat_session`、`history_v2`、`core_memory`、`session_project`、`scheduled_task_executions` 等。
- 数据库初始化、密钥和备份逻辑在 `libai_agent.dylib` 内部；主进程只传 `DB_PATH`、机器 ID 和备份开关，没有公开 key 或 key-file 路径。
- 数据库目录现场只有 `database.db/-wal/-shm`，没有独立密钥文件。
- dylib 内有全库 backup/recovery 与 `sqlcipher_export` 实现，但当前备份开关关闭，也没有公开 RPC。
- 前端公开 Lite API 没有通用数据库查询、备份恢复或 `set_work_mode` 方法。

#### ai-agent 与 IPC

- ai-agent 是 Rust dylib，由 Electron helper 子进程加载。
- 它与主进程使用匿名 socketpair 通信，没有外部可连接的 Unix socket 或 TCP 端口。
- `1.10-main.sock` 是 VS Code IPC server，不是 ai-agent；已注册 channel 中没有 ai-agent 公共入口。
- 结论：外部直连 AHA-IPC 不是可行主路线。

#### Trae 原生列表过滤

```text
lite/list_chat_sessions
  → SessionManager / DataSource
  → raw session 转内部 Session
  → sessionDomainService.appendSessions
  → sessionStore.allSessionsForSidebar
  → 按 mode 和 project 分组渲染
```

本地列表查询会关联 `chat_session.project_id → project.user_id`，并按当前登录用户过滤项目。侧栏还会按当前 `code/work/design` 模式再次过滤会话。

#### 官方只读出口

- `lite/export_past_chat` 可导出 Markdown，不需要 SQLCipher 密钥。
- 真实端到端导出曾得到 112,640 字节、2,044 行；临时正文随后删除。
- `lite/get_chat_session` 和 `lite/get_messages` 可按已知 ID 读取结构化元数据与消息。
- `trae-local-session-export.js` 通过 `--remote-debugging-pipe` 启动临时实例，不开放 TCP 调试端口，导出完成后关闭。
- 切换成功后必须立即重新提取 `storage.json`；Trae 会轮换 token，不回写会导致后续注入 401。

#### 可恢复的会话 ID 与来源

- `snapshot/<24位会话ID>/` 可提供本机存在过的会话 ID 候选；本轮现场共发现 34 个目录。
- 使用当前账号的 `lite/get_chat_session` 按这 34 个 ID 查询，34 个均能读到元数据。
- `state.vscdb` 的 key 带账号 UID 前缀；结合模型选择、draft、revert key，现场可为 30 个会话恢复来源账号，另有少量来源待确认。
- `state.vscdb` 中 agent alias 可推断会话模式：`solo_agent_* → code`、`solo_work_* → work`、`solo_design_* → design`；内层数值 `mode:0` 不是会话模式，不能误用。
- `workspaceStorage` 的 `chat.ChatSessionStore.index` 现场为空；draft、chat-suggest、snapshot 都不含完整正文。
- 标题和正文不能从 snapshot 或 chat-suggest 猜测，必须以 `list/get/messages` 返回为准。

### 10.4 真实测试结果

| 测试项 | 结果 | 结论 |
|---|---|---|
| 当前账号按另一账号的已知会话 ID 调 `get_chat_session` | 成功 | 跨账号读取元数据可行 |
| 当前账号按同一 ID 调 `get_messages` | 成功，真实测试会话 2 条消息完整 | 跨账号读取正文可行 |
| `fork_session` 跨账号复制 | 成功复制标题和 2 条消息 | 正文复制能力存在 |
| 检查 fork 后新会话 `mode` | `null` | 复制结果被 Code/Work 侧栏过滤 |
| 向 `fork_session` 追加 `mode/work_mode/agent_mode` | 被忽略 | 不能靠请求参数修复 |
| `commit_chat_session` 更新 `mode/work_mode` | 被忽略 | commit 不是模式更新接口 |
| 先创建 `mode=code` 会话 | 可见，但为空 | 只能创建壳，不能导入任意历史角色消息 |
| `chat_migrate` 导入当前结构历史 | 返回成功但生成空白、`mode=null` 会话 | 不适合作为现成跨账号复制接口 |
| `teleport_session` | 返回 `unknown method`（code `1010003`） | 当前 Trae 版本不可用 |
| 外部 SQLCipher 直改 `work_mode` | 未执行 | 无安全密钥路径，版本和数据风险不可接受 |
| 运行时注入原生侧栏 | 成功显示并打开完整正文 | 只证明“可显示可读”，不具备持久性 |

### 10.5 运行时原型的准确边界

原型通过 Trae 渲染进程现有的会话服务完成，没有修改数据库：

1. 用官方 `get_chat_session` 读取旧账号会话。
2. 把标准化后的会话写入 `sessionDomainService.appendSessions()`。
3. 同时把会话 ID 加入当前模式的全局本地 DataSource；只写 store 不足以触发侧栏分组显示。
4. Trae 原生 Code 侧栏出现该会话。
5. 使用 GPT 内置电脑插件点击后，旧账号的用户问题和 Trae 回答均完整加载。

点击后，Trae 会重新读取真实项目关系，并把外部项目归入“默认”分组。这证明正文和真实元数据来自原数据库，而不是伪造的界面文本。

该方案没有落地，原因是：

- 退出、刷新、切换账号或 store reset 后内存数据消失。
- 需要以调试模式启动 Trae，并依赖当前版本的渲染模块锚点。
- 只能作为显示层兼容方案，不能称为持久会话合并。

### 10.6 已排除的路径

- **外部直连 ai-agent IPC**：进程内 socketpair，没有公共入口。
- **`frb_api`/8717**：是本机“分润宝”项目借用 Trae Python 解释器运行，与 Trae 会话无关。
- **云端 `chat_sessions`**：token 有效但服务端返回会话校验拒绝，不是本地同步主路。
- **Keychain 候选和 PBKDF2 变体**：已验证的 `Trae Safe Storage` 主密钥不是数据库 key，其他候选均失败。
- **获取 SQLCipher 密钥后直改库**：没有公开、安全、版本稳定的密钥路径。
- **`commit_chat_session` 修改 mode**：实际只更新标题、图标、时间和 context 等字段。
- **`send_message/append_msg` 重放历史**：只能发送新的用户消息并触发模型，不能保留原角色、时间和 assistant 历史。
- **`chat_migrate` 追加到已创建的 code 会话**：接口没有 `target_session_id`，现场结果也没有保留 mode 和正文。
- **`teleport_session`**：当前运行版本未注册该方法。

### 10.7 建议的降级方案

目标改为：**不把会话写进新账号名下，但在账号坞内长期保留来源和索引，切换后仍能识别并只读打开。**

#### 切换流程

```text
准备从账号 B 切到账号 A
  → B 仍登录时完整分页扫描 code/work/design 会话
  → 原子保存 B 的会话索引
  → 退出 B、注入 A、启动并校验 A
  → 账号坞展示“本机历史会话”，按来源账号分组
  → 点击 B 会话时，以会话 ID 调 get_chat_session/get_messages
  → 若原始记录未来不可读，再回退到预先导出的 Markdown 快照
```

#### 建议保存的数据

索引只保存元数据，不保存 token，不默认复制正文：

```json
{
  "version": 1,
  "owners": {
    "<owner_user_id>": {
      "account_id": "<cockpit_account_id>",
      "last_complete_scan_at": 0,
      "sessions": {
        "<session_id>": {
          "title": "...",
          "mode": "code",
          "status": 5,
          "project_path": "...",
          "created_at": 0,
          "updated_at": 0,
          "last_verified_at": 0
        }
      }
    }
  }
}
```

建议文件：`~/.antigravity_cockpit/trae_local_session_inventory.json`，权限 `0600`，使用临时文件、`fsync`、`os.replace` 原子更新。

#### UI 建议

- Trae 平台增加“本机历史会话”入口。
- 默认按来源账号分组，并明确显示“当前账号 / 旧账号 / 来源待确认”。
- 点击后在账号坞内用只读抽屉显示消息，不把“可读取”包装成“已导入”。
- 对旧账号会话提供“导出 Markdown”按钮，作为可选持久备份。
- 暂不提供“继续对话”按钮；是否能在不同项目上下文安全续聊尚未验证。

#### 更新时机与置信规则

- 切换最前面、退出旧账号前，完整分页扫描 `work/code/design` 并写入旧账号 inventory。
- 切换后校验新账号成功，再刷新新账号 inventory；失败回滚时不能把目标账号归属写入。
- AHA `list/get` 返回的 owner/title/mode 为权威；state key 前缀仅作为高置信来源线索；snapshot 只能证明 ID 候选存在。
- 一次列表缺失不删除历史 ID；只有完整扫描且连续读回 NotFound 后才标记失效。

### 10.8 下一阶段验收标准

1. 登录账号 B，创建或选定一条带唯一标题的本地会话。
2. 通过账号坞切换到账号 A。
3. 账号坞“本机历史会话”仍显示该会话，并标注来源 B。
4. 点击后能读取原用户消息和 Trae 回复。
5. 重启账号坞后索引仍在；重启 Trae 后仍可按 ID 读取。
6. 切回 B 后会话仍在 B 的原生侧栏，未发生过户、删除或重复复制。
7. 任一步读取失败时明确显示“索引存在、正文当前不可读”，不能误报成功。

### 10.9 清理与安全状态

- 所有 fork/create/migrate 测试产生的临时会话均已通过官方删除接口清理。
- 调试时误发到业务会话的一条测试文本已按准确消息 ID 删除，并读回确认不存在。
- 未完成的运行时注入代码已从工作区撤回，没有保留半成品实现。
- Trae 已停止运行。
- 写接口探测前创建的数据库备份仍保留在本机 `ModularData/ai-agent` 下；未提交、未删除。
- 本页不包含 token、密钥、完整用户 ID 或会话正文。

### 10.10 状态标签

- **已验证完成**：账号切换；旧会话不丢失；已知 ID 跨账号读取元数据与正文；运行时原型显示并打开正文。
- **已实现未验证**：无。
- **受阻未完成**：把旧账号本地会话永久写入当前账号并在 Trae 原生侧栏冷启动保持。
- **建议下一步**：实现只读会话索引与账号坞内历史阅读器。
