# WorkBuddy 会话港 · 交接清单

> 编写时间：2026-08-24
> 关联仓库：`/Users/Zhuanz/Documents/project/workbuddy-session-sync`
> 当前定位：**多平台可插拔的 AI 办公平台账号管理工具**，模仿 Cockpit 做成模块化，单进程单端口（7531）承载多平台适配器。

---

## 一、总览

会话港是一个 macOS 本地工具（Python HTTP 服务 + WebKit 壳），通过适配器层管理多个 AI 办公平台的账号授权、切换、会话同步。账号库与 Cockpit 完全互认，统一存放在 `~/.antigravity_cockpit/`，共用同一把 `secure-account-storage.key`（AES-256-GCM 信封）。

桌面 App：`~/Desktop/WorkBuddy 会话港.app`（ad-hoc 签名，每次重建带时间戳备份后改回干净名）。

---

## 二、三平台状态矩阵

| 平台 | 适配器 | 授权导入 | 切换账号 | 会话同步 | 云端会话 | 额度 | 状态 |
|------|--------|---------|---------|---------|---------|------|------|
| **WorkBuddy** | 内置（workbuddy-sync-app.py） | ✅ OAuth state+poll / Token / JSON / 本地导入 / 2FA TOTP | ✅ auth.info 注入切换 | ✅ SQLite user_id 归并 | — | ✅ Cockpit 资源包刷新 | 稳定 |
| **Trae Work** | platforms/trae.py | ✅ storage.json iCube 提取（双 App） | ✅ 退出→备份→注入→重启→校验 | 🔴 阻塞 | ✅ Cloud-IDE-JWT 双网关 | — | 核心已通，会话同步待破 |
| **千问办公** | platforms/qwen.py（占位） | 🔴 Phase 2（auth.dat 自定义加密待逆向） | 🔴 Phase 2 | 🔴 Phase 2 | — | — | 调研结论已落档 |

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
- **🔴 会话同步阻塞**：ai-agent `database.db` 是 SQLCipher 4 加密（PBKDF2-HMAC-SHA512 256000 轮、AES-256-CBC 每页独立 IV）。密钥运行时随机生成并持久化，**SIP + hardened runtime 封死内存扫描/lldb**，Keychain/环境变量/machine_id 派生全排除。
  - **P0 出路**：逆向 AHA-IPC 协议（unix socket + JSON-RPC）直连 ai-agent 进程取会话，绕过数据库解密。dylib 内有完整 aha-ipc crate 源码路径线索。详见 `trae_sycn/HANDOFF_本地会话同步.md`。
  - **P0 兜底**：探测云端 API 是否支持查"进行中"会话（当前只返回 status=5 已完成）。
- **本机已有账号**：`~/.antigravity_cockpit/trae_work_accounts/` 3 个真实账号。

### 2.3 千问办公（Phase 2 占位）

- App：`QwenWorkCN.app`（`cn.qwenwork.desktop.mac` v0.1.8）。
- 登录态：`~/Library/Application Support/QwenWorkCN/auth.dat` + `auth-v2.dat`，v10 信封头（类 Electron safeStorage）。
- Keychain 有 `QwenWorkCN Safe Storage`（base64 raw 16B 密钥），但**标准 Electron safeStorage 三种派生（PBKDF2 saltysalt/1003、raw 直接做 key、固定 IV 空格）全部解不开** → 自定义加密方案。
- **破解需反汇编 `app.asar`** 找加密实现。占位模块 `status()` 能探测安装/登录态，`capture()`/`switch()` 守卫抛 Phase 2 错误。

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
│   └── qwen.py                  # 千问 Phase 2 占位（含调研结论）
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

## 六、已完成工作（2026-08-23 时间线）

1. **OAuth 授权登录迁移**：会话港加 Cockpit 式"添加 WorkBuddy 账号"弹窗（4 tab + 2FA 工具 + 在浏览器打开 + 倒计时轮询）。`/api/auth/open`、`/api/import/token`、`/api/totp`、`/api/scan-local` 四个新路由。提交 `fc4f3f5`。
2. **7 个账号导入**：从 `~/Downloads/workbuddy_accounts_2026-08-23.json` 导入 Cockpit 账号库（导入前已备份 `.bak.20260823-231038`）。
3. **多平台架构**：`platforms/` 适配器层 + Trae 完整移植 + 千问占位。UI 平台 Tab + 动态面板。`build-macos-app.sh` 修打包 platforms。最新提交见 git log。
4. **桌面 App 精简**：每次重建带时间戳备份，旧版送 `~/.Trash`（可恢复），新版改回干净名 `WorkBuddy 会话港.app`。

---

## 七、阻塞项与已知问题

| 问题 | 影响 | 出路 |
|------|------|------|
| Trae 本地会话同步 | 看不到进行中会话 | 逆向 AHA-IPC 协议（P0），或云端 API status 探测（P0 兜底） |
| 千问 auth.dat 自定义加密 | 无法提取/切换千问账号 | 反汇编 `app.asar` 找加密实现（Phase 2） |
| iOS 端授权弹窗 | 手机端交互未适配 | 单独开一轮做 iOS WebView 交互 |
| 桌面 App 重建带时间戳 | 旧版易堆积 | 习惯：重建后立即删旧留新改干净名（脚本可固化） |

---

## 八、下一步优先级

| 优先级 | 行动 | 说明 |
|--------|------|------|
| 🟥 P0 | Trae AHA-IPC 协议逆向 | 唯一能拿到进行中会话的正路，dylib 有源码路径线索 |
| 🟥 P0 | Trae 云端 API status 参数探测 | 30 分钟零成本，可能直接拿到进行中会话 |
| 🟧 P1 | 千问 app.asar 反汇编 | 破 auth.dat 加密后补齐 capture/switch |
| 🟧 P1 | iOS 端授权弹窗交互 | 手机端可用 |
| 🟩 P2 | 把"重建→删旧→改名"固化进 build 脚本 | 避免桌面堆积 |
| 🟩 P2 | 适配器能力探测自动渲染 UI | 不同平台按 features 矩阵渲染可用操作 |

---

## 九、安全与运维注意

- **不动 `~/.workbuddy/workbuddy.db` 的写操作前必须备份**（`cp workbuddy.db workbuddy.db.bak.<ts>`），写时退出 WorkBuddy 避免 WAL 锁。
- **账号库写入**走 `write_account`/`write_cockpit_account` 原子替换（temp→fsync→replace，权限 0600）。
- **open_authorization_url 有 CodeBuddy 官方域名白名单**，防止把任意 URL 交给浏览器。
- **2FA 密钥只在本地计算**，不持久化不上传；TOTP 用标准库实现，无第三方依赖。
- **不提交**：storage.json、账号导出 JSON、workbuddy.db、token、日志、构建产物。
- **依赖**：仅 `cryptography`（读 Cockpit 加密账号库 + Trae iCube 解密），其余全标准库。
