# Trae 本地会话同步 · 交接文档（逆向攻坚）

> 更新时间：2026-08-24
> 关联项目：`workbuddy-session-sync`（多平台会话港）、`trae_sycn`（Trae 会话港前身）
> 本文档只覆盖 **Trae（TRAE SOLO CN / Trae Work）本地会话同步** 这一个未解问题。

---

## 一、目标

把 TRAE SOLO CN（对外名 **Trae Work**，bundle id `cn.trae.solo.app`）的**本地对话会话**读出来，接入会话港的"切换账号 + 会话归并"流程。

- **账号管理已解决**：账号提取（storage.json iCube 解密）、切换（注入+重启）、账号库（`~/.antigravity_cockpit/trae_work_accounts/`）全部可用。
- **唯一卡点**：会话正文存在 SQLCipher 加密的 `database.db` 里，**密钥至今没找到**。

---

## 二、一句话现状

**账号切换 ✅ 已通；本地会话读取 ❌ 被 SQLCipher 密钥卡住。已排除多条死路（详见第四节），下一步优先查渲染进程明文缓存。**

---

## 三、已确认的架构事实（实测，可信任）

### 3.1 会话数据的存储位置
- 会话正文在：`~/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/database.db`
  - **确认是真加密**：文件头 16 字节为随机字节（非 `SQLite format 3`），大小 ~163MB，另有 `-shm`/`-wal`。
  - 迁移脚本字符串显示表结构含 `chat_turn`、`chat_session`、`history_v2`、`core_memory`、`session_project`、`scheduled_task_executions` 等——**会话数据确实在这个库里**。
- 会话的**文件快照**（非对话正文）在：`.../ModularData/ai-agent/snapshot/<24位hex会话ID>/v2/`，是 git 仓库，存工作区文件状态。目录名即会话 ID，与云端 API 的 session id 格式一致。

### 3.2 ai-agent 进程形态（关键，决定了 IPC 路线的死局）
- ai-agent 是 **Rust 写的 dylib**（`libai_agent.dylib`），由 Electron 主进程以 `type:"lib"` 方式加载进一个 helper 子进程（`basil.mojom.NativeExtensionService`）。
- 它与主进程之间用**匿名 socketpair** 通信（lsof 可见成对的 `unix` fd），**没有对外暴露的 unix socket 或端口**。
- `1.10-main.sock`（在 AppSupport 根目录）是 **VS Code IPC server**（主进程的），不是 ai-agent 的。握手响应已用 ProtocolWriter 解码为 `[200]`=Initialize。已注册的 34 个 channel 里**没有 "ai-agent"**——它走内部 `IQ.Connect` 路由，外部连不进去。
- **结论：想从外部直接连 ai-agent 的 IPC 拿会话，此路不通。**（这是上一轮花大量时间才确认的教训，见第五节）

### 3.3 SQLCipher 密钥排查记录（全部失败）
| 尝试 | 结果 |
|---|---|
| 启动参数/环境变量 | 只有 `DB_PATH`，**没有传 key** |
| Keychain（`Trae Safe Storage` / acct=`Trae Key`，24 字符） | 是 Electron safeStorage 主密钥，**不是 db key** |
| 18 个候选（machineId、各类 uuid）+ PBKDF2 变体 | 全失败 |
| ai-agent 的 stdout/stderr/alaudalog 日志 | 无 key / PRAGMA 记录 |
| dylib 含 `keyring communication timed out` 字符串 | 提示它**可能**会读钥匙串，但按 `trae/icube/solo` 关键词没扫到匹配条目 |

---

## 四、已排除的死路（重要：别再走）

1. **外部直连 ai-agent 的 AHA-IPC** —— ai-agent 在进程内、走 socketpair，无外部入口。协议解析（13 字节帧头、ProtocolWriter、channel 表）虽然完整，但**没有可用对象**。
2. **`frb_api`（8717 端口）** —— ⚠️ **这是最容易踩的坑，已实锤无关**。
   - 它的工作目录是 `/Users/Zhuanz/Documents/project/fenrunbao-admin`（**用户自己的"分润宝"项目**，`frb` = fenrunbao 缩写）。
   - 它只是**借用**了 TRAE 的 `vm/tools` Python 解释器在跑，所以进程命令行里挂着 TRAE 的路径，造成"它是 TRAE 的会话 API"的错觉。
   - 8717 返回 401 是因为分润宝自己的鉴权，**与 Trae 会话毫无关系**。
3. **云端 API `chat_sessions`** —— token 有效（JWT 到 9 月）但服务端 `code=1001` 会话校验拒绝，疑似缺设备绑定头。可作为备选但非本地同步主路。

---

## 五、思路纠偏记录（为什么之前走偏）

上一轮被用户两次叫停，复盘出三个错误模式，**接手者务必避免**：
1. **先解协议、后看通路**——花大量时间剥 AHA-IPC 协议，最后才发现 ai-agent 根本连不上。正确顺序：**先确认"能不能连上"，再研究"连上后怎么说"**。
2. **从没问"TRAE 自己的界面从哪读到会话的"**——官方 UI 明明能显示会话列表，数据必然流经某个**明文缓存**（`state.vscdb` 是明文 SQLite、Local Storage、IndexedDB）。这是成本最低的突破口。
3. **沉没成本驱动**——每碰一堵墙就"再试一种变体"，而不是退一步换路线。

---

## 六、下一步路线（按成本从低到高）

### 第 1 步：查渲染进程明文缓存（零风险，优先）★
- 已找到 3 个 `state.vscdb`（明文可读）：
  - `~/Library/Application Support/TRAE SOLO CN/User/globalStorage/state.vscdb`
  - `.../User/workspaceStorage/d7e0b2809976c5c93ea42af299537ae0/state.vscdb`
  - `.../User/workspaceStorage/e04cdd/state.vscdb`
- 已知线索：
  - `workspaceStorage` 里 `chat.ChatSessionStore.index` 目前是空 `{"version":1,"entries":{}}`。
  - `globalStorage` 里有 **`draft:session:<会话ID>:work`** 和 `revert:attachment-meta:v1:session:<会话ID>`、`chat-suggest:cache:<会话ID>` 等 key，会话 ID 与 snapshot 目录一致。
- **待办**：读 `globalStorage/state.vscdb` 里 `draft:session:*` 的完整内容，判断是否含会话正文或足够的会话元数据；同时扫 `Local Storage/leveldb` 与 `IndexedDB`。
- **注意**：这些可能是"草稿/缓存"，未必是完整历史；若只够拿会话列表+标题，也足以先做"会话级同步"，正文再单独攻坚。

### 第 2 步：Keychain 全量扫描（不限关键词）
- dylib 有 keyring 字符串，可能服务名不含 `trae`。全量枚举登录钥匙串的通用密码条目，逐个试做 SQLCipher key。

### 第 3 步：反汇编 `libai_agent.dylib` 找 key 派生（最后手段，项目级工作量）
- 定位 `PRAGMA key` 调用点，反推 key 来源（可能是机器指纹派生）。建议用 `Hopper`/`Ghidra`，或先 `strings` + `nm` 找符号。

---

## 七、常用命令速查

```bash
# 确认 TRAE 是否在跑（socket/进程依赖运行态）
pgrep -fl "TRAE SOLO CN.app" | head

# 读 state.vscdb（明文）
sqlite3 "$HOME/Library/Application Support/TRAE SOLO CN/User/globalStorage/state.vscdb" \
  "SELECT key FROM ItemTable WHERE key LIKE '%session%' LIMIT 30"

# 看 ai-agent 进程与其 socketpair
lsof -p <helper_pid> | grep -E "unix|libai_agent|database.db"

# 判断 database.db 是否加密（应为随机字节）
head -c 16 "$HOME/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/database.db" | cat -v
```

---

## 八、安全与运维注意

- 所有操作**只读优先**；任何写库/动账号的动作前先备份。
- 不外泄完整 token / 密钥；日志、文档里只留前缀。
- `database.db` 属用户敏感数据，解密产物不要提交进 git。
