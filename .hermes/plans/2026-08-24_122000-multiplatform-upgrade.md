# 多平台账号与会话体验升级 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 修复 WorkBuddy 额度 403 错误阻断账号切换的问题，并让 AI 账号坞在三平台具备一致、可验证的账号管理体验；优先让 Trae 在不冒险合并数据库的前提下保留并读取旧账号本地会话，同时为千问办公建立可验证的会话读取路线。

**Architecture:** 账号层统一为“提取 / 导入 / 导出 / 切换 / 目标校验 / 回滚”。WorkBuddy 的额度查询是展示性能力，必须与登录切换解耦；会话层按平台能力分流：WorkBuddy 继续使用已验证的归并；Trae 使用“来源索引 + 官方只读 API”；千问先完成只读数据源探测，未发现可靠通路前不写会话或宣称同步完成。

**Tech Stack:** Python 3 标准库、现有 `cryptography`、本地 HTTP/Web UI、Trae 官方 Lite API、现有 Node `--remote-debugging-pipe` 辅助脚本、macOS WebKit 壳。

---

## 范围与不做事项

本计划只做以下三项升级：

1. 紧急修复 WorkBuddy 的“额度 403 阻断切换”缺陷。
2. Trae 只读历史会话索引和阅读器。
3. Trae / 千问账号导入导出 UI 补齐、千问切换校验回滚，以及千问会话只读探测。

不做：

- 不直接打开、修改或逆向写入 Trae 的 SQLCipher 数据库。
- 不把 Trae 的运行时侧栏注入当作持久合并功能。
- 不在千问未确认会话数据源前做跨账号复制、导入或合并。
- 不新增第三方依赖。

## 当前依据

- WorkBuddy 已实现“切换目标账号 → 校验目标 UID → 备份 → 会话和自动化归并 → 重启”。
- WorkBuddy 当前把 `refresh_cockpit_account()` 同时用于 token 刷新和 billing/额度读取；任一 billing HTTP 403 会在写登录态前中止切换，截图中的 `70250244` 已复现此问题。
- Trae 已验证：当前账号可按已知 ID 用 `get_chat_session/get_messages` 读取旧账号正文；`fork_session` 会丢 `mode`，不能用作原生持久合并。
- 千问已实现 `auth-v2.dat` 的解密、提取、导入和写回；当前 `switch()` 只确认“存在用户”，没有核验目标 `user_id`，也没有失败回滚。
- 动态平台 UI 目前仅暴露“提取当前登录”和“切换”，没有 Trae / 千问的导入、导出或历史会话入口。

## 紧急缺陷：WorkBuddy 额度 403 不得阻断切换

### Task 0: 将登录 token 刷新与额度刷新解耦

**Objective:** billing/额度接口返回 403 时，账号切换仍可继续；只有“实际启动后的 UID 不等于目标 UID”才阻止会话合并。

**Files:**

- Modify: `workbuddy-sync-app.py:289-350,896-930`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

用 mock 分别模拟 token 刷新、额度查询和真实 UID 校验：

```python
app.refresh_cockpit_account = lambda uid: {
    "account": {"uid": uid},
    "quota_warning": "HTTP Error 403: Forbidden",
}
app.wait_for_running_account = lambda: "target-user"
result = app.do_switch_full("target-user")
assert result["ok"] is True
assert "quota_warning" in result
```

再覆盖：token 刷新不可用但已有登录态仍可进入启动 UID 校验；实际 UID 不匹配时依旧 `ok: false` 且绝不调用 `do_sync()`。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL；当前 `refresh_cockpit_account()` 的 billing 403 会让 `do_switch_full()` 在第 0 步返回失败。

**Step 3: Write minimal implementation**

- 拆分 `refresh_cockpit_account()` 的职责：`refresh_switch_credentials(uid)` 只处理 token 刷新和本地账户原子保存；`refresh_quota_data(account)` 只用于卡片展示。
- `do_switch_full()` 只调用前者。若 token 刷新返回 403，但本地完整登录态仍存在，则记录 warning 并继续“写入 → 启动 → 实际 UID 校验”。
- 额度 403 只写入 `quota_warning`，返回给 UI 显示“额度暂不可刷新”，不得调用 `do_sync()` 之前中断切换。
- 目标 UID 校验失败、认证文件写入失败或启动失败仍保持硬失败；这些情况绝不能合并会话。

**Step 4: Run regression and manual check**

Run:

```bash
/opt/homebrew/bin/python3 -m py_compile workbuddy-sync-app.py
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Manual check: 对截图中 `70250244` 账号执行一次“切换并同步”。预期 UI 显示额度 warning 或正常额度，不再显示“目标账号刷新失败”；仅在 WorkBuddy 实际 UID 验证成功后才显示会话过户数量。

**Step 5: Commit**

```bash
git add workbuddy-sync-app.py workbuddy-sync-app.test.py
git commit -m "fix: 额度 403 不阻断 WorkBuddy 切换"
```

## 升级一：Trae 只读历史会话索引和阅读器

### Task 1: 定义索引格式与纯函数测试

**Objective:** 建立不保存 token 或正文的跨账号会话元数据索引，并保证合并、去重和失效标记可预测。

**Files:**

- Modify: `platforms/trae.py`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

在测试文件中覆盖以下纯函数行为：

```python
inventory = merge_local_session_inventory({}, "owner-b", [{
    "id": "a" * 24,
    "title": "B 的会话",
    "mode": "code",
    "updated_at": 10,
}])
assert inventory["owners"]["owner-b"]["sessions"]["a" * 24]["title"] == "B 的会话"
```

同时覆盖：相同 ID 新时间覆盖旧元数据、不同 owner 不串数据、单次扫描缺失不删除旧记录。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，缺少索引合并函数。

**Step 3: Write minimal implementation**

在 `platforms/trae.py` 新增：

- `LOCAL_SESSION_INVENTORY_PATH`：`~/.antigravity_cockpit/trae_local_session_inventory.json`
- `read_local_session_inventory()`
- `write_local_session_inventory()`：临时文件、`fsync`、`os.replace`、`0600`
- `merge_local_session_inventory()`：只保存来源 UID、account ID、session ID、标题、mode、状态、项目路径、时间和扫描状态

不得保存 token、refresh token、消息正文或导出的 Markdown 内容。

**Step 4: Run test to verify pass**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: `OK`。

**Step 5: Commit**

```bash
git add platforms/trae.py workbuddy-sync-app.test.py
git commit -m "feat: 保存 Trae 本地会话索引"
```

### Task 2: 在切换前完整采集当前 Trae 会话

**Objective:** 在退出旧账号前，以旧账号上下文扫描 `code/work/design` 的所有分页，写入权威索引。

**Files:**

- Modify: `platforms/trae.py`
- Modify: `trae-local-session-export.js` only if必须抽取已有调试管道公共逻辑
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

模拟三种 mode 的多页返回，验证：

- 三种 mode 都被请求；
- 使用 `next_page_token` 直到为空；
- `switch()` 在 `quit_app()` 前调用采集；
- 采集失败只返回 warning，不阻断已验证的账号切换。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，当前切换前没有本地会话采集。

**Step 3: Write minimal implementation**

- 新增一个最小 Node 辅助入口，复用 `trae-local-session-export.js` 已有的调试管道、适配器捕获逻辑；它只输出当前登录 Trae 的本地会话列表 JSON，不导出正文、不写数据库。
- Python 侧通过 `subprocess` 调用该入口，将三种 mode 的完整列表归入“当前旧账号 UID”。
- 如果 Trae 已关闭，使用当前已登录态临时启动只读实例采集；若采集仍失败，保留既有索引并标明 `incomplete`，不得伪造“完整扫描”。
- 在 `switch()` 里先采集、再退出、注入、启动并核验目标账号。

**Step 4: Run tests and live preflight**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
node trae-local-session-export.test.js
```

Live preflight: 仅对当前账号运行列表采集，确认索引中有来源 UID、标题、mode 和会话数；不切换、不删除、不写 Trae 数据库。

**Step 5: Commit**

```bash
git add platforms/trae.py trae-local-session-export.js workbuddy-sync-app.test.py
git commit -m "feat: 切换前采集 Trae 本地会话"
```

### Task 3: 提供 Trae 历史会话与消息 API

**Objective:** 让账号坞可按来源账号列出历史会话，并通过官方 Lite API 只读打开正文。

**Files:**

- Modify: `platforms/trae.py`
- Modify: `workbuddy-sync-app.py`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

覆盖：

- 历史列表返回当前账号与旧账号分组；
- 读取旧会话时调用 `get_chat_session` 与 `get_messages`；
- Lite API 返回 NotFound 时，接口返回 `readable: false` 与明确错误，不删除索引；
- 非 Trae 平台调用该动作返回可理解的 404。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，当前没有本地历史会话路由。

**Step 3: Write minimal implementation**

- 在 `platforms/trae.py` 新增 `list_local_history()`、`get_local_history_messages(session_id)`。
- 在 `workbuddy-sync-app.py` 的 `/api/platform/<id>/<action>` 增加明确动作：`local-history`、`local-history-messages`，而不是复用目前只服务云端的 `sessions`。
- 返回项必须包含 `owner_user_id`、`owner_label`、`source_confidence`、`readable`、`title`、`mode`、`updated_at`。
- 不支持写入、继续对话、删除历史或把历史会话标记为“已导入”。

**Step 4: Run test to verify pass**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: `OK`。

**Step 5: Commit**

```bash
git add platforms/trae.py workbuddy-sync-app.py workbuddy-sync-app.test.py
git commit -m "feat: 提供 Trae 历史会话只读接口"
```

### Task 4: 在动态平台 UI 增加 Trae 历史阅读器

**Objective:** 用户不需要知道 session ID，也能识别并打开旧账号会话。

**Files:**

- Modify: `workbuddy-sync-app.py`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

断言 Web 源码包含历史入口、来源账号标签、只读提示和消息加载动作；不依赖浏览器框架。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，动态平台面板没有历史会话入口。

**Step 3: Write minimal implementation**

- Trae 面板增加“本机历史会话”按钮。
- 列表按来源账号折叠分组；每条显示标题、mode、更新时间、可读状态。
- 点击时在只读抽屉或弹层显示消息；标题固定显示“旧账号历史，只读，未导入”。
- 读取失败显示“索引存在，正文当前不可读”，不隐藏该会话。

**Step 4: Run focused test and real acceptance**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Real acceptance: 使用 B→A 流程，在 A 下的账号坞打开 B 的真实会话，确认用户消息和 Trae 回复均出现。

**Step 5: Commit**

```bash
git add workbuddy-sync-app.py workbuddy-sync-app.test.py
git commit -m "feat: 展示 Trae 旧账号历史会话"
```

## 升级二：账号操作一致性与千问切换安全

### Task 5: 修正千问目标账号校验与回滚

**Objective:** 千问切换的成功条件必须是“实际 UID 等于目标 UID”，否则恢复原认证文件并重启原账号。

**Files:**

- Modify: `platforms/qwen.py`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

沿用 Trae 切换测试的 mock 风格，覆盖：

- 启动后 UID 与目标 UID 不同；
- 原 `auth-v2.dat` 被恢复；
- 原应用被重新启动；
- 返回 `ok: false`、`verified: false`、`rolled_back: true`；
- UID 相同才返回成功。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，当前实现只检查“存在用户”。

**Step 3: Write minimal implementation**

- 在 `switch()` 开始前保存 `read_auth()` 的完整原始认证对象。
- 目标账号写入并启动后，比较 `get_current_login()["user_id"]` 与账户库的目标 `user_id`。
- 不匹配、启动失败或读取失败时：退出 App、恢复原认证对象、重新启动、返回回滚状态。
- 沿用现有 backup 文件保留策略；不增加新配置项。

**Step 4: Run tests**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
/opt/homebrew/bin/python3 -m py_compile platforms/qwen.py
```

Expected: `OK`。

**Step 5: Commit**

```bash
git add platforms/qwen.py workbuddy-sync-app.test.py
git commit -m "fix: 校验并回滚千问账号切换"
```

### Task 6: 为 Trae 与千问补齐导入导出 UI

**Objective:** 三个平台都能在 UI 中明确完成提取、导入、导出和切换；导出风险可见。

**Files:**

- Modify: `platforms/trae.py`
- Modify: `platforms/qwen.py`
- Modify: `workbuddy-sync-app.py`
- Test: `workbuddy-sync-app.test.py`

**Step 1: Write failing test**

覆盖：

- 两个适配器各自提供仅导出明确选中账号的序列化函数；
- 不选账号、账号不存在或缺少完整认证 payload 时拒绝导出；
- 平台导入和导出 POST 路由返回一致的 `{ok, imported}` 或文件下载；
- 页面存在 Trae / 千问的导入、导出按钮及凭证风险提示。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，当前动态平台 UI 只有提取和切换。

**Step 3: Write minimal implementation**

- 在两个适配器新增 `export_accounts(account_ids)`；输出只包含导入切换所必需的结构，不输出无关本机路径或临时状态。
- 在通用平台 API 增加 `export` 动作；复用已有安全下载响应，不再创建第二种下载机制。
- 在动态平台面板加入账号选择框、导入文件按钮和“导出包含登录凭证，仅保存到受信位置”的醒目提示。
- 导入完成后重新加载平台状态；不要自动切换导入的账号。

**Step 4: Run tests and manual UI check**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Manual check: Trae、千问各导出一个测试账号，再重新导入；仅核验账号卡出现，不用真实凭证覆盖当前登录。

**Step 5: Commit**

```bash
git add platforms/trae.py platforms/qwen.py workbuddy-sync-app.py workbuddy-sync-app.test.py
git commit -m "feat: 补齐平台账号导入导出"
```

## 升级三：千问会话只读数据源探测

### Task 7: 建立只读探测脚本与结果记录

**Objective:** 先确定千问会话来自本地文件、SQLite、IndexedDB 还是官方网络 API；未发现可靠数据源也要形成可复现结论。

**Files:**

- Create: `qwen-session-probe.py`
- Create: `qwen-session-probe.test.py`
- Modify: `HANDOFF.md`

**Step 1: Write failing test**

测试脚本的纯筛选函数：仅返回会话相关的文件、数据库表名、IndexedDB 目录或可公开观察的请求元数据；不返回 token、Cookie、完整消息正文。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 qwen-session-probe.test.py
```

Expected: FAIL，脚本不存在。

**Step 3: Write minimal implementation**

- 只读扫描 `~/Library/Application Support/QwenWorkCN` 的文件清单、SQLite schema、Chromium IndexedDB/Local Storage 名称。
- 检查 App bundle 的静态字符串和已有网络配置，寻找会话列表、消息、历史、conversation 等公开路由线索。
- 输出结构化摘要：候选来源、证据路径、是否含可用会话 ID、是否需要登录态、风险等级。
- 默认不读取消息正文，不打印令牌，不启动代理，不修改 App、数据库或网络配置。

**Step 4: Run read-only probe**

Run:

```bash
/opt/homebrew/bin/python3 qwen-session-probe.py
```

Expected: 输出候选来源和证据等级；若没有可用接口，明确输出“未发现可安全读取的会话数据源”。

**Step 5: Commit**

```bash
git add qwen-session-probe.py qwen-session-probe.test.py HANDOFF.md
git commit -m "feat: 增加千问会话只读探测"
```

### Task 8: 仅在读通后接入千问历史阅读器

**Objective:** 将已验证的千问会话读取路径接入 UI；若 Task 7 未找到路径，本任务明确跳过。

**Files:**

- Modify: `platforms/qwen.py`
- Modify: `workbuddy-sync-app.py`
- Modify: `workbuddy-sync-app.test.py`

**Precondition:** Task 7 必须得出可重复、只读且不依赖未授权逆向写入的会话列表和消息读取路径。

**Step 1: Write failing test**

覆盖列表、消息、空状态、错误状态和来源账号标记；不写测试数据库或真实会话。

**Step 2: Run test to verify failure**

Run:

```bash
/opt/homebrew/bin/python3 workbuddy-sync-app.test.py
```

Expected: FAIL，接口尚不存在。

**Step 3: Write minimal implementation**

- 只新增 `list_local_history()` 与 `get_local_history_messages()`。
- UI 复用 Trae 历史阅读器的只读组件和错误文案。
- 不实现导入、复制、合并、继续对话或数据库写入。

**Step 4: Run real acceptance or mark skipped**

- 若数据源已验证：用两账号真实样本证明切换后仍能读取来源会话。
- 若数据源未验证：不落地该接口，在 `HANDOFF.md` 标记为“探测完成，读取路径未确认”。

**Step 5: Commit**

```bash
git add platforms/qwen.py workbuddy-sync-app.py workbuddy-sync-app.test.py HANDOFF.md
git commit -m "feat: 提供千问历史会话只读查看"
```

## 总体验收清单

### 账号能力

- [ ] WorkBuddy 的 billing/额度接口 403 只显示 warning，不阻断“写入登录态 → 启动 → 目标 UID 校验 → 会话合并”。
- [ ] WorkBuddy 实际 UID 不等于目标 UID 时，仍不执行会话合并。
- [ ] WorkBuddy、Trae、千问均有提取、导入、导出和切换的可见 UI。
- [ ] 导出仅包含用户明确选择的账号，下载前显示“包含登录凭证”的提示。
- [ ] Trae 和千问导入后只入库，不自动切换。
- [ ] 千问切换成功时实际 UID 等于目标 UID。
- [ ] 千问切换失败时原认证文件恢复、原账号重启、结果明确标记回滚。

### Trae 历史会话

- [ ] B 账号会话在 B→A 切换前被完整分页记录到索引。
- [ ] A 下的账号坞按来源账号显示 B 会话；标题、mode、更新时间正确。
- [ ] 点击可读取 B 的原用户消息和 Trae 回复。
- [ ] 重启账号坞、重启 Trae 后索引仍在，已知 ID 仍可读取。
- [ ] B 原生侧栏仍保留原会话；没有过户、删除或重复复制。
- [ ] 无法读取时显示“索引存在，正文当前不可读”，不把失败伪装成空会话。

### 千问会话

- [ ] 探测报告只包含结构、候选路径和证据等级，不含 token、Cookie 或消息正文。
- [ ] 只有在列表与消息读取均可重复验证后，才显示千问历史会话入口。
- [ ] 未发现安全数据源时，UI 明确显示“会话读取待支持”，不提供假按钮。

### 回归与交付

- [ ] `/opt/homebrew/bin/python3 -m py_compile workbuddy-session-sync.py workbuddy-sync-app.py platforms/*.py` 通过。
- [ ] `/opt/homebrew/bin/python3 workbuddy-sync-app.test.py` 输出 `OK`。
- [ ] `node trae-local-session-export.test.js` 输出 `OK`。
- [ ] macOS 构建后 `codesign --verify --deep --strict "<App 路径>"` 通过。
- [ ] `HANDOFF.md` 更新为最终能力、验证证据和未完成边界。
