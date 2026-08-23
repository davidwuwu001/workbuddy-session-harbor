# WorkBuddy 会话港 · 交接清单

> 编写时间：2026-08-24（最近更新：2026-08-24 06:35，固化稳定版）
> 关联仓库：`/Users/Zhuanz/Documents/project/workbuddy-session-sync`
> 当前定位：**多平台可插拔的 AI 办公平台账号管理工具**，模仿 Cockpit 做成模块化，单进程单端口（7531）承载多平台适配器。
>
> **本次固化范围**：账号授权、账号切换、账号导入/导出、千问适配器已稳定（测试全绿、已提交推送）。未实现项见第六、七节及专项攻坚文档。

---

## 一、总览

会话港是一个 macOS 本地工具（Python HTTP 服务 + WebKit 壳），通过适配器层管理多个 AI 办公平台的账号授权、切换、会话同步。账号库与 Cockpit 完全互认，统一存放在 `~/.antigravity_cockpit/`，共用同一把 `secure-account-storage.key`（AES-256-GCM 信封）。

桌面 App：`~/Desktop/WorkBuddy 会话港.app`（ad-hoc 签名，每次重建带时间戳备份后改回干净名）。

---

## 二、三平台状态矩阵

| 平台 | 适配器 | 授权导入 | 切换账号 | 会话同步 | 云端会话 | 额度 | 状态 |
|------|--------|---------|---------|---------|---------|------|------|
| **WorkBuddy** | 内置（workbuddy-sync-app.py） | ✅ OAuth state+poll / Token / JSON / 本地导入 / 2FA TOTP | ✅ auth.info 注入切换 | ✅ SQLite user_id 归并 | — | ✅ Cockpit 资源包刷新 | **稳定** |
| **Trae Work** | platforms/trae.py | ✅ storage.json iCube 提取（双 App） | ✅ 退出→备份→注入→重启→校验 | 🔴 阻塞（SQLCipher 密钥未破） | ✅ Cloud-IDE-JWT 双网关 | — | **账号功能稳定，会话同步待破** |
| **千问办公** | platforms/qwen.py | ✅ safeStorage 提取（已破解） | ✅ 退出→备份→加密注入→重启→校验 | 🔴 未实现（待研究） | — | — | **账号功能稳定，会话同步未开始** |

> 本次固化的"已跑通"功能 = 上表所有 ✅ 项：**账号授权（导入）与账号切换，三个平台全部可用**。未实现项（两个平台的会话同步）见第六节。

### 2.1 WorkBuddy（最完整）

- **授权**：OAuth 2.0 设备授权模式变体。`POST /v2/plugin/auth/state?platform=workbuddy` 拿 state → 浏览器打开授权页 → 每 2s 轮询 `/v2/plugin/auth/token` 换 accessToken/refreshToken → `/v2/plugin/login/account` 拉 profile 落库。有效期 600s。
- **导入**：弹窗 4 个 tab（OAuth 授权 / Token 直接粘 / JSON 文件 / 本地扫描）；Token 导入给 refresh_token 时先经官方刷新接口校验。
- **2FA**：标准库实现 RFC 6238 TOTP，兼容 Base32 与 `otpauth://`，密钥不离开本机。
- **切换**：原子替换 `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`，重启 WorkBuddy 校验实际 UID 后再归并会话。
- **会话同步**：SQLite `sessions`/`automations` 表按 `user_id` 归并到主账号，支持 `--dry-run`/`--merge-once`/`--watch`。

### 2.2 Trae Work（核心已通，会话同步阻塞）

- **双 App**：`solo_cn` = TRAE SOLO CN（工作台），`trae_cn` = Trae CN（IDE）。网关 `trae-api-cn.mchost.guru` / `work.enterprise.trae.cn`，鉴权 `Cloud-IDE-JWT`。
- **提取**：解密 `~/Library/Application Support/TRAE SOLO CN/User/globalStorage/storage.json` 的 iCube 信封（AES-128-CBC，PREFIX+salt 方案），账号存入 `~/.antigravity_cockpit/trae_work_accounts/`。
- **切换**：退出 App → 备份 storage.json（保留近 10 份）→ 注入 storage_payload → 重启 → 12s 后校验登录。
- **🔴 会话同步阻塞**：ai-agent `database.db` 是 SQLCipher 加密，密钥未找到。已排除多条死路（外部直连 AHA-IPC、frb_api 8717 端口、云端 API code=1001 等）。
  - **详细结论与下一步路线见专项文档 `HANDOFF_Trae本地会话同步攻坚.md`**（含已确认架构事实、SQLCipher 排查记录、已排除死路、按成本排序的下一步路线）。
  - **下一步优先**：查渲染进程明文缓存 `state.vscdb`（已发现 `draft:session:<会话ID>:work` key），零风险、最可能直接拿到会话元数据。
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

全部 AES-256-GCM 信封格式，与 Cockpit Tools 完全互认（Cockpit 导出的 JSON 可直接导入会话港，反之亦然）。

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
zsh build-macos-app.sh "$HOME/Desktop/WorkBuddy 会话港-$(date +%Y年%m月%d日-%H-%M-%S).app"
codesign --verify --deep --strict "<App 路径>"     # 必须通过
# 验证包内 platforms：
ls "<App>/Contents/Resources/platforms/"
```

---

## 六、阻塞项与已知问题（未实现清单）

> 本次固化**不包含**以下项，均属"后续再搞"。每项已指明阻塞原因与专项文档。

| 未实现项 | 平台 | 阻塞原因 | 指引文档 |
|------|------|------|------|
| 本地会话同步 | Trae Work | ai-agent `database.db` SQLCipher 加密，密钥未找到；已排除外部直连/frb_api/云端等死路 | `HANDOFF_Trae本地会话同步攻坚.md` |
| 云端会话浏览 | 千问办公 | 千问云端会话 API 尚未研究 | 待立项 |
| iOS 端授权弹窗 | 全平台 | 手机端 WebView 交互未适配 | 单独开一轮 |

**已解决（本次固化前遗留）**：千问 `auth.dat` 加密已破解（此前列为阻塞），账号功能已并入稳定版。

### 6.1 已排除的死路（别再走）
1. **外部直连 ai-agent 的 AHA-IPC**——进程内 socketpair，无外部入口。
2. **`frb_api`/8717 端口**——实锤是用户自己的"分润宝"项目，与 Trae 会话无关，只是借用了 TRAE 的 Python 解释器。
3. **云端 API `chat_sessions`**——token 有效但服务端 `code=1001` 会话校验拒绝。

详见 `HANDOFF_Trae本地会话同步攻坚.md` 第四节。

---

## 七、下一步优先级（未实现项的攻坚顺序）

| 优先级 | 行动 | 说明 |
|--------|------|------|
| 🟥 P0 | Trae 渲染缓存 `state.vscdb` 挖掘 | 已发现 `draft:session:<会话ID>:work` key，零风险、最可能直接拿到会话元数据。详见攻坚文档第六节第 1 步 |
| 🟥 P0 | Trae Keychain 全量扫描 | dylib 有 keyring 字符串，服务名可能不含 "trae"，逐条试做 SQLCipher key |
| 🟧 P1 | 千问云端会话 API 研究 | 补齐千问 `sessions_cloud` 能力 |
| 🟧 P1 | iOS 端授权弹窗交互 | 手机端可用 |
| 🟩 P2 | 反汇编 `libai_agent.dylib` 找 SQLCipher key 派生 | 最后手段，项目级工作量 |
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
7. **桌面精简**：多次删旧留新，桌面始终只有一个 `WorkBuddy 会话港.app`。
8. **Trae 本地会话同步攻坚（阶段性结论）**：写 `HANDOFF_Trae本地会话同步攻坚.md`，确认 AHA-IPC 无外部入口、SQLCipher 密钥排查全失败、`frb_api` 实锤无关，明确下一步路线。

---

## 九、安全与运维注意

- **不动 `~/.workbuddy/workbuddy.db` 的写操作前必须备份**（`cp workbuddy.db workbuddy.db.bak.<ts>`），写时退出 WorkBuddy 避免 WAL 锁。
- **账号库写入**走 `write_account`/`write_cockpit_account` 原子替换（temp→fsync→replace，权限 0600）。
- **open_authorization_url 有 CodeBuddy 官方域名白名单**，防止把任意 URL 交给浏览器。
- **2FA 密钥只在本地计算**，不持久化不上传；TOTP 用标准库实现，无第三方依赖。
- **不提交**：storage.json、账号导出 JSON、workbuddy.db、token、日志、构建产物。
- **依赖**：仅 `cryptography`（读 Cockpit 加密账号库 + Trae iCube 解密），其余全标准库。
