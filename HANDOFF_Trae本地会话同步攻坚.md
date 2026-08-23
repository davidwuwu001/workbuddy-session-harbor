# Trae 本地会话同步 · 交接文档（逆向攻坚）

> 更新时间：2026-08-24
> 关联项目：`workbuddy-session-sync`（多平台会话港）、`trae_sycn`（Trae 会话港前身）
> 本文档只覆盖 **Trae（TRAE SOLO CN / Trae Work）本地会话同步**。2026-08-24 已解决只读导出，账号归并仍未实现。

---

## 一、目标

把 TRAE SOLO CN（对外名 **Trae Work**，bundle id `cn.trae.solo.app`）的**本地对话会话**读出来，接入会话港的"切换账号 + 会话归并"流程。

- **账号管理已解决**：账号提取（storage.json iCube 解密）、切换（注入+重启）、账号库（`~/.antigravity_cockpit/trae_work_accounts/`）全部可用。
- **只读正文已解决**：调用 ai-agent 官方 `lite/export_past_chat`，无需取得 SQLCipher 密钥。
- **剩余卡点**：如何安全地把结构化历史写入目标账号；`chat/chat_migrate` 是候选写接口，但尚未在数据库副本上验证。

---

## 二、一句话现状

**账号切换 ✅；本地正文只读导出 ✅（112,640 字节 / 2,044 行真实会话已验证）；跨账号归并 🟡 尚未做写库验证。**

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
| dylib 含 `keyring communication timed out` 字符串 | 已定位到 tunnel 组件，不能证明 SQLCipher 密钥来自通用密码钥匙串 |

### 3.4 官方只读出口（已验证）

- 官方调用：`chat.exportPastChat` → `service="lite"` / `method="export_past_chat"`。
- 请求字段：`session_id`、`export_path`、`header_extra`；返回 `data.file_path`。
- `get_chat_session` 与 `get_messages` 也可按已知 session ID 读取结构化元数据和消息；实测会话返回 86 条消息。
- 新增 `trae-local-session-export.js`：仅在 Trae 已退出时启动 `--remote-debugging-pipe` 临时实例，不监听 TCP，导出完成自动关闭。
- 真实端到端验证：在已登出状态仍成功导出 112,640 字节、2,044 行 Markdown；临时正文已随即删除。

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

### 第 1 步：把只读导出接入账号坞 UI ★

- 先用 `state.vscdb` / snapshot 目录生成 session ID 索引，再按需调用官方导出。
- 明文缓存已确认只能恢复部分标题和账号归属，不能替代正文；正文必须走官方导出/消息接口。
- 当前 CLI 已可单会话导出，下一步才做批量归档与 UI，不写 ai-agent 数据库。

### 第 2 步：在数据库副本上验证 `chat/chat_migrate`

- 该接口用于旧历史结构化迁移，请求包含 session/project/messages 完整结构。
- 它不是现成的账号复制接口，可能触发 session/message 唯一键冲突。
- 必须先做数据库副本、去重规则和回滚验证；未完成前不得宣称“跨账号归并”。

### 已完成：渲染进程明文缓存排查
- 已找到 3 个 `state.vscdb`（明文可读）：
  - `~/Library/Application Support/TRAE SOLO CN/User/globalStorage/state.vscdb`
  - `.../User/workspaceStorage/d7e0b2809976c5c93ea42af299537ae0/state.vscdb`
  - `.../User/workspaceStorage/e04cdd/state.vscdb`
- 已知线索：
  - `workspaceStorage` 里 `chat.ChatSessionStore.index` 目前是空 `{"version":1,"entries":{}}`。
  - `globalStorage` 里有 **`draft:session:<会话ID>:work`** 和 `revert:attachment-meta:v1:session:<会话ID>`、`chat-suggest:cache:<会话ID>` 等 key，会话 ID 与 snapshot 目录一致。
- 结论：可关联 28/34 个 snapshot 会话的账号，仅少量活跃标题；draft、chat-suggest、Local Storage、IndexedDB 均无完整正文。

### 已降级：SQLCipher 密钥与 Keychain

- 导出/读取不再依赖密钥，不应继续全量扫描 Keychain。
- 静态符号不支持“dylib 从 generic-password Keychain 读取 DB key”的旧假设；只有未来必须直接写数据库副本时，才考虑继续逆向派生链。

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

# 官方只读导出（Trae 必须先退出；目标文件不能已存在）
node trae-local-session-export.js <24位会话ID> /absolute/path/session.md
```

---

## 八、安全与运维注意

- 所有操作**只读优先**；任何写库/动账号的动作前先备份。
- 不外泄完整 token / 密钥；日志、文档里只留前缀。
- `database.db` 属用户敏感数据，解密产物不要提交进 git。
- 导出的 Markdown 含完整会话正文，同样不得提交；验收临时文件应立即删除。
