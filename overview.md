# WorkBuddy 跨账号会话同步 · 分析报告

> 生成日期：2026-08-07
> 分析对象：~/.workbuddy 本地数据存储 + cockpit 账号切换机制

---

## 一、问题根因

通过 cockpit 切换账号后，会话任务列表"消失"。经本地存储分析，**数据并未丢失**，全部仍在本地磁盘，只是被账号隔离机制过滤了。

### 隔离机制（已验证）

| 存储位置 | 内容 | 是否按账号隔离 |
|----------|------|----------------|
| `~/.workbuddy/workbuddy.db` → `sessions` 表 | 会话元数据（id/user_id/title/status/cwd/model…） | **是**，按 `user_id` 字段过滤 |
| `~/.workbuddy/workbuddy.db` → `automations` 表 | 自动化任务 | **是**，按 `owner_user_id` 过滤 |
| `~/.workbuddy/projects/{cwd路径}/{conversationId}.jsonl` | 完整对话内容 | 否，按工作区分目录 |
| `~/.workbuddy/tasks/{conversationId}/{n}.json` | 任务列表 | 否，按 conversationId |
| `~/.workbuddy/teams/{team_name}/` | 团队配置 | 否 |
| `~/.workbuddy/app/sessions.json` | 当前活跃会话（含 userId） | 记录当前账号 |

UI 查询逻辑等价于：
```sql
SELECT * FROM sessions WHERE user_id = '当前登录账号' AND deleted_at IS NULL;
```
切换账号 → 查询的 `user_id` 变化 → 其他账号的会话记录不匹配 → 列表为空。

### 当前账号分布（实测）

```
bb7bd6e3-29c2-4f10-8786-5b0de76b485b  →  119 个会话（主账号，当前登录）
ddf6f125-5d5c-43d7-993b-15466d237b60  →  见于 settings.json claw.users（本地 sessions 表无记录）
41e15573-247c-469d-9769-525981997f78  →  见于 settings.json claw.users（本地 sessions 表无记录）
```

### 重要发现（影响方案适用性）

dry-run 实测：本地 `sessions` 表**只有主账号** 119 条记录，另外两个账号在本地 DB 里**零会话**。

这意味着切换到其他账号后会话"消失"，有两种可能，方案适用性不同：

| 情形 | 真实原因 | 合并方案是否有效 |
|------|----------|------------------|
| **A. 其他账号的会话存在本地，只是被 user_id 过滤** | 纯本地隔离 | ✅ 合并 user_id 立即生效 |
| **B. 其他账号会话在云端，切换时才拉取覆盖本地** | 云端账号隔离 | ⚠️ 需先让云端会话落地本地（切换到该账号等同步完成），再跑合并 |
| **C. 本地 sessions 表随账号切换被云端覆写** | 切换即重置本地视图 | ⚠️ 需在每次切换同步后立即合并，或改用"统一导出归档"而非合并 |

**建议先用脚本 `--dry-run` 在不同账号下各跑一次**：若切换到账号 B 后本地 sessions 表出现 B 的会话，则是情形 A/B，合并有效；若本地表始终只有当前账号数据，则是情形 C，需换思路（见第六节）。

### 关键约束（决定方案空间）

`sessions.id`（即 conversationId）是主键，且对话文件 `.jsonl`、任务目录 `tasks/{id}/` 都用这个 id 命名。因此：
- **一个会话只能归属一个 user_id**（不能给每个账号都复制一份记录，会主键冲突）
- "同步"的本质 = 让所有会话归并到一个主账号，或切换时过户

---

## 二、方案对比

| 方案 | 原理 | 优点 | 缺点 | 适合 |
|------|------|------|------|------|
| **A 一次性合并** | 把所有账号会话的 `user_id` 改成主账号 | 立即全部可见，零常驻 | 之后新建会话又会分裂 | 想"现在就全看到" |
| **B 合并 + 守护进程（推荐）** | 先合并，再常驻轮询 sessions 表，新会话自动过户到主账号 | 新建也自动归并，彻底解决 | 需常驻；额度算到主账号 | 长期使用多账号 |
| **C 切换钩子** | 监听 app/sessions.json 的 userId 变化，切换时过户新增会话 | 不常驻，按需触发 | 只跟账号走，非真同步 | 偶尔切换 |

---

## 三、推荐方案 B 实现步骤

### 步骤 1：备份（强制）
```bash
cp ~/.workbuddy/workbuddy.db ~/.workbuddy/workbuddy.db.bak.$(date +%Y%m%d%H%M%S)
```

### 步骤 2：一次性合并
```sql
-- 把非主账号的会话全部过户到主账号
UPDATE sessions
SET user_id = 'bb7bd6e3-29c2-4f10-8786-5b0de76b485b'
WHERE user_id != 'bb7bd6e3-29c2-4f10-8786-5b0de76b485b'
  AND deleted_at IS NULL;

-- 自动化任务同理
UPDATE automations
SET owner_user_id = 'bb7bd6e3-29c2-4f10-8786-5b0de76b485b',
    owner_status = 'confirmed'
WHERE owner_user_id != 'bb7bd6e3-29c2-4f10-8786-5b0de76b485b'
  AND deleted_at IS NULL;
```

### 步骤 3：守护进程（轮询归并）
轻量 Python 脚本，每 10s 扫一次 sessions 表，发现 `user_id ≠ 主账号` 的新会话即改 user_id。见随附 `workbuddy-session-sync.py`。

### 步骤 4：重启 WorkBuddy 刷新 UI
UI 可能缓存会话列表，改完 DB 后重启 WorkBuddy 才能看到合并结果。

---

## 四、风险与注意事项

| 风险 | 说明 | 缓解 |
|------|------|------|
| **WAL 写锁冲突** | workbuddy.db 是 WAL 模式（有 -wal/-shm）。WorkBuddy 运行时外部写入可能 busy | 脚本设 `busy_timeout=5000`；合并时建议先退出 WorkBuddy |
| **云端同步归属** | edge-sync-mapping.db 按 session_id 映射 conversation_id，不依赖 user_id，风险低 | 改 user_id 后云端仍按原账号可能错位，留意 |
| **会员额度** | 合并后所有会话算到主账号，可能触及额度上限 | 主账号选额度最大的 |
| **账号区分丢失** | 合并后无法区分某会话原属哪个账号 | 可选：合并前给 title 加 `[账号B]` 前缀 |
| **WorkBuddy 升级** | 未来版本若改 schema 或加校验，脚本可能失效 | 升级后重新核对 sessions 表结构 |

---

## 五、为什么不能用"真·插件"拦截

WorkBuddy 的扩展点（Skill / MCP connector）都是给 AI 用的工具层，**无法拦截 session 写入或修改 UI 的查询逻辑**。cockpit 切账号是 Electron 主进程行为，无公开 hook。因此"同步"只能从数据层（直接读写 SQLite）入手，做成独立守护脚本，而非 WorkBuddy 内置插件。

---

## 六、交付物

- `workbuddy-session-sync.py` — 可运行脚本，含 `--dry-run` / `--merge-once` / `--watch` 三种模式
