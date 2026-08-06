#!/usr/bin/env python3
"""
WorkBuddy 会话同步器（过户模式）- 独立带界面应用

用法:
  python3 workbuddy-sync-app.py            # 启动并自动打开浏览器
  python3 workbuddy-sync-app.py --port 8000 # 指定端口

工作流:
  1. 用 cockpit 切换到目标账号
  2. 打开本应用，点"一键同步"
  3. 所有会话过户到当前登录账号，重启 WorkBuddy 即可看到全部

零依赖，仅 Python 标准库。
"""

import http.server
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

DB_PATH = os.path.expanduser("~/.workbuddy/workbuddy.db")
SESSIONS_JSON = os.path.expanduser("~/.workbuddy/app/sessions.json")
SETTINGS_JSON = os.path.expanduser("~/.workbuddy/settings.json")
DEFAULT_PORT = 7531
EXPORT_GLOB = os.path.expanduser("~/Downloads/workbuddy_accounts_*.json")
LEVELDB_PATH = os.path.expanduser("~/.workbuddy/app/session/Local Storage/leveldb")
NODE_BIN = "/Users/Zhuanz/.workbuddy/binaries/node/versions/22.22.2/bin/node"
NODE_WORKSPACE = "/Users/Zhuanz/.workbuddy/binaries/node/workspace"


def find_accounts_export():
    """找最新的账号导出文件（~/Downloads/workbuddy_accounts_*.json）"""
    import glob
    files = sorted(glob.glob(EXPORT_GLOB), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def parse_quota(acc):
    """从 usage_raw 解析积分：基础体验包(CapacityType=4) + 活动赠送包(其他聚合)"""
    base = {"used": 0, "total": 0, "unit": "credits"}
    gift = {"used": 0, "total": 0, "unit": "credits"}
    cycle_end = ""
    try:
        accts = acc["usage_raw"]["data"]["Response"]["Data"]["Accounts"]
        for c in accts:
            used = c.get("CapacityUsed", 0)
            size = c.get("CapacitySize", 0)
            unit = c.get("CapacityUnit", "credits")
            if c.get("CapacityType") == 4:
                base["used"] += used
                base["total"] += size
                base["unit"] = unit
            else:
                gift["used"] += used
                gift["total"] += size
                gift["unit"] = unit
            ce = c.get("CycleEndTime", "")
            if ce and ce > cycle_end:
                cycle_end = ce
    except Exception:
        pass
    return base, gift, cycle_end[:10]


def load_accounts_export():
    """读导出文件，返回 {uid: {nickname, access_token, refresh_token, ...积分}}"""
    path = find_accounts_export()
    if not path:
        return {}
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    result = {}
    for acc in data:
        uid = acc.get("uid", "")
        if not uid:
            continue
        base, gift, cycle_end = parse_quota(acc)
        result[uid] = {
            "nickname": acc.get("nickname", uid[:8]),
            "access_token": acc.get("access_token", ""),
            "refresh_token": acc.get("refresh_token", ""),
            "token_type": acc.get("token_type", "Bearer"),
            "expires_at": acc.get("expires_at"),
            "domain": acc.get("domain", "www.codebuddy.cn"),
            "payment_type": acc.get("payment_type", "free"),
            "base": base,
            "gift": gift,
            "cycle_end": cycle_end,
            "export_file": os.path.basename(path),
        }
    return result


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_current_account():
    """当前 cockpit 登录账号 = DB 里正在运行的对话(status=working, 最新updated)的 user_id
    原理：会话 user_id 在创建时锁定为当时登录账号，当前 working 会话即当前登录账号。
    回退：最新创建的会话 user_id。"""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    # 优先：working 状态最新会话（当前正在聊的）
    row = conn.execute(
        "SELECT user_id FROM sessions WHERE status='working' AND deleted_at IS NULL "
        "ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        # 回退：最新创建的会话
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    conn.close()
    return row[0] if row else None


def get_distribution():
    """返回 [(user_id, count)] 各账号会话数（降序）"""
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    cur = conn.execute(
        "SELECT user_id, COUNT(*) FROM sessions "
        "WHERE deleted_at IS NULL GROUP BY user_id ORDER BY COUNT(*) DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_accounts():
    """聚合账号：优先用导出文件（含昵称+积分+token），合并 DB 会话数 + is_current"""
    current = get_current_account()
    export = load_accounts_export()
    accs = {}  # uid -> info dict

    # 先用导出文件初始化（有昵称+积分+token）
    for uid, info in export.items():
        accs[uid] = {"sessions": 0, **info}

    # settings.json claw.users（曾配置过的账号）
    if os.path.exists(SETTINGS_JSON):
        try:
            d = json.load(open(SETTINGS_JSON))
            for u in d.get("claw", {}).get("users", {}):
                accs.setdefault(u, {"sessions": 0, "nickname": u[:8]})
        except Exception:
            pass

    # DB 会话数
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA busy_timeout = 5000")
        for u, n in conn.execute(
            "SELECT user_id, COUNT(*) FROM sessions "
            "WHERE deleted_at IS NULL GROUP BY user_id"
        ):
            if u not in accs:
                accs[u] = {"sessions": 0, "nickname": u[:8]}
            accs[u]["sessions"] = accs[u].get("sessions", 0) + n
        conn.close()

    # sessions.json 出现过的 userId
    if os.path.exists(SESSIONS_JSON):
        try:
            d = json.load(open(SESSIONS_JSON))
            for s in d.get("sessions", []):
                uid = s.get("userId", "")
                if uid:
                    accs.setdefault(uid, {"sessions": 0, "nickname": uid[:8]})
        except Exception:
            pass

    # 组装 + 排序（当前账号优先，然后会话数降序）
    items = []
    for uid, info in accs.items():
        items.append({
            "user_id": uid,
            "nickname": info.get("nickname", uid[:8]),
            "sessions": info.get("sessions", 0),
            "is_current": uid == current,
            "payment_type": info.get("payment_type", "unknown"),
            "base": info.get("base", {"used": 0, "total": 0, "unit": ""}),
            "gift": info.get("gift", {"used": 0, "total": 0, "unit": ""}),
            "total": {
                "used": info.get("base", {}).get("used", 0) + info.get("gift", {}).get("used", 0),
                "total": info.get("base", {}).get("total", 0) + info.get("gift", {}).get("total", 0),
                "unit": info.get("base", {}).get("unit", "") or "credits",
            },
            "cycle_end": info.get("cycle_end", ""),
            "has_token": bool(info.get("access_token")),
        })
    items.sort(key=lambda x: (not x["is_current"], -x["sessions"]))
    return items


def do_sync(target_uid):
    """把所有非 target 账号的会话过户到 target"""
    if not target_uid:
        return {"ok": False, "error": "未检测到当前登录账号"}
    if not os.path.exists(DB_PATH):
        return {"ok": False, "error": f"找不到数据库 {DB_PATH}"}

    # 备份
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak = f"{DB_PATH}.bak.{ts}"
    shutil.copy2(DB_PATH, bak)

    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA busy_timeout = 15000")

    # 统计待过户
    cur = conn.execute(
        "SELECT user_id, COUNT(*) FROM sessions "
        "WHERE user_id != ? AND deleted_at IS NULL GROUP BY user_id",
        (target_uid,),
    )
    others = cur.fetchall()
    moved_sessions = sum(n for _, n in others) if others else 0

    # 统计待过户 automations
    cur2 = conn.execute(
        "SELECT COUNT(*) FROM automations "
        "WHERE owner_user_id != ? AND deleted_at IS NULL",
        (target_uid,),
    )
    moved_automations = cur2.fetchone()[0]

    # 执行过户
    conn.execute(
        "UPDATE sessions SET user_id = ? "
        "WHERE user_id != ? AND deleted_at IS NULL",
        (target_uid, target_uid),
    )
    conn.execute(
        "UPDATE automations SET owner_user_id = ?, owner_status = 'confirmed' "
        "WHERE owner_user_id != ? AND deleted_at IS NULL",
        (target_uid, target_uid),
    )
    conn.commit()

    # 验证
    total = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ? AND deleted_at IS NULL",
        (target_uid,),
    ).fetchone()[0]
    conn.close()

    return {
        "ok": True,
        "target": target_uid,
        "moved_sessions": moved_sessions,
        "moved_automations": moved_automations,
        "total_now": total,
        "backup": os.path.basename(bak),
        "from_accounts": [{"uid": u[:8], "count": n} for u, n in (others or [])],
    }


def do_switch(target_uid):
    """切换登录账号：写 Local Storage leveldb 的 accountInfo（token+refreshToken）
    用 node + classic-level 模块写 leveldb（Python 无标准库）。
    前提：WorkBuddy 未运行（否则 leveldb 被锁）。"""
    export = load_accounts_export()
    acc = export.get(target_uid)
    if not acc:
        return {"ok": False, "error": "导出文件中找不到该账号"}
    if not acc.get("access_token"):
        return {"ok": False, "error": "该账号没有 access_token"}
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leveldb-write.js")
    if not os.path.exists(script):
        return {"ok": False, "error": f"找不到 {script}"}
    import subprocess
    payload = json.dumps({"token": acc["access_token"], "refreshToken": acc["refresh_token"]})
    env = os.environ.copy()
    env["NODE_PATH"] = os.path.join(NODE_WORKSPACE, "node_modules")
    try:
        result = subprocess.run(
            [NODE_BIN, script, LEVELDB_PATH, "accountInfo", payload],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode == 0:
            return {"ok": True, "detail": f"accountInfo 已更新（token {acc['access_token'][:12]}…）"}
        return {"ok": False, "error": (result.stderr or result.stdout)[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def is_workbuddy_running():
    """进程级检测：pgrep 匹配主二进制（Electron，路径含 WorkBuddy.app/Contents/MacOS）。
    osascript 的 is running 基于 LaunchServices 注册，退出/被杀后有延迟，不可靠。"""
    import subprocess as sp
    try:
        r = sp.run(["pgrep", "-f", "WorkBuddy.app/Contents/MacOS/Electron"],
                   capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        try:
            r = sp.run(["osascript", "-e", 'application "WorkBuddy" is running'],
                       capture_output=True, text=True, timeout=10)
            return "true" in r.stdout.lower()
        except Exception:
            return False


def quit_workbuddy():
    """退出 WorkBuddy（释放 leveldb 锁）。先优雅 quit，超时则 pkill 主进程。"""
    import subprocess as sp
    try:
        sp.run(["osascript", "-e", 'quit app "WorkBuddy"'], capture_output=True, timeout=10)
    except Exception:
        pass
    for _ in range(8):
        if not is_workbuddy_running():
            return True
        time.sleep(1)
    # 强杀主进程（服务在沙箱外运行，可直接 pkill）。主二进制名是 Electron，路径含 WorkBuddy.app
    try:
        sp.run(["pkill", "-9", "-f", "WorkBuddy.app/Contents/MacOS/Electron"],
               capture_output=True, timeout=10)
    except Exception:
        pass
    for _ in range(10):
        if not is_workbuddy_running():
            return True
        time.sleep(1)
    return not is_workbuddy_running()


def start_workbuddy():
    import subprocess as sp
    try:
        sp.run(["open", "-a", "WorkBuddy"], capture_output=True, timeout=15)
        return True
    except Exception:
        return False


def do_switch_full(target_uid):
    """一体化：退出 WorkBuddy → 写 leveldb 切换登录 → 过户会话 → 启动 WorkBuddy"""
    log(f"  [1/4] 退出 WorkBuddy...")
    if not quit_workbuddy():
        return {"ok": False, "error": "无法退出 WorkBuddy，请手动退出后重试"}
    log(f"  [2/4] 写入登录凭据...")
    sw = do_switch(target_uid)
    if not sw.get("ok"):
        start_workbuddy()  # 失败也要恢复 WorkBuddy
        return sw
    log(f"  [3/4] 过户会话...")
    sy = do_sync(target_uid)
    log(f"  [4/4] 启动 WorkBuddy...")
    started = start_workbuddy()
    return {
        "ok": True,
        "switch_detail": sw.get("detail", ""),
        "moved_sessions": sy.get("moved_sessions", 0),
        "total_now": sy.get("total_now", 0),
        "backup": sy.get("backup", ""),
        "restarted": started,
    }


def refresh_quota(target_uid):
    """调 codebuddy.cn API 实时查询账号积分（/v2/billing/meter/get-user-resource）"""
    import urllib.request, ssl
    from datetime import datetime, timedelta
    export = load_accounts_export()
    acc = export.get(target_uid)
    if not acc or not acc.get("access_token"):
        return {"ok": False, "error": "无 access_token"}
    now = datetime.now()
    body = json.dumps({
        "PageNumber": 1, "PageSize": 100,
        "ProductCode": "p_tcaca", "Status": [0, 3],
        "PackageEndTimeRangeBegin": now.strftime('%Y-%m-%d 00:00:00'),
        "PackageEndTimeRangeEnd": (now + timedelta(days=365*101)).strftime('%Y-%m-%d 23:59:59'),
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://www.codebuddy.cn/v2/billing/meter/get-user-resource',
        data=body, method='POST',
        headers={
            'Authorization': f'Bearer {acc["access_token"]}',
            'Content-Type': 'application/json',
            'X-User-Id': target_uid,
            'X-Domain': acc.get('domain', 'www.codebuddy.cn'),
            'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12, context=ssl.create_default_context()) as r:
            resp = json.loads(r.read().decode('utf-8'))
            accts = resp.get('data', {}).get('Response', {}).get('Data', {}).get('Accounts', [])
            used = sum(c.get('CapacityUsed', 0) for c in accts)
            total = sum(c.get('CapacitySize', 0) for c in accts)
            return {"ok": True, "used": used, "total": total, "unit": "credits", "packages": len(accts)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkBuddy 会话同步器</title>
<style>
  :root {
    --bg: #1a1a1a; --surface: #242424; --surface2: #2e2e2e;
    --border: #3a3a3a; --text: #e8e8e8; --text2: #999; --text3: #666;
    --accent: #4a9eff; --accent2: #2d7dd2; --green: #4caf50; --warn: #ff9800;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 14px; line-height: 1.6; padding: 24px; max-width: 720px; margin: 0 auto; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
  .sub { color: var(--text2); font-size: 13px; margin-bottom: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; margin-bottom: 16px; }
  .label { color: var(--text3); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
  .current-uid { font-family: "SF Mono", Consolas, monospace; font-size: 15px; color: var(--green); word-break: break-all; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border); }
  .row:last-child { border-bottom: none; }
  .uid { font-family: "SF Mono", Consolas, monospace; font-size: 13px; }
  .count { font-weight: 600; font-size: 15px; }
  .badge { display: inline-block; background: var(--green); color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 4px; margin-left: 8px; }
  .sel { cursor: pointer; display: flex; align-items: center; }
  .sel input { margin-right: 10px; accent-color: var(--accent); }
  .btn { width: 100%; padding: 16px; background: var(--accent); color: #fff; border: none; border-radius: var(--radius); font-size: 16px; font-weight: 600; cursor: pointer; transition: background 0.15s; }
  .btn:hover { background: var(--accent2); }
  .btn:disabled { background: var(--surface2); color: var(--text3); cursor: not-allowed; }
  .log { background: var(--surface2); border-radius: var(--radius); padding: 14px 16px; margin-top: 16px; font-family: "SF Mono", Consolas, monospace; font-size: 12px; color: var(--text2); white-space: pre-wrap; min-height: 20px; max-height: 240px; overflow-y: auto; }
  .log:empty::before { content: "等待操作..."; color: var(--text3); }
  .ok { color: var(--green); } .err { color: #ff5252; } .warn { color: var(--warn); }
  .tip { color: var(--text3); font-size: 12px; margin-top: 20px; line-height: 1.7; }
  .tip b { color: var(--warn); }
  .acc-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; margin-bottom: 12px; }
  .acc-card.cur { border-color: var(--green); }
  .acc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .acc-nick { font-size: 16px; font-weight: 600; }
  .badge-free { background: var(--surface2); color: var(--text2); font-size: 10px; padding: 2px 7px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.3px; }
  .badge-cur { background: var(--green); color: #fff; font-size: 10px; padding: 2px 7px; border-radius: 3px; }
  .pkg { margin-bottom: 8px; }
  .pkg-row { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
  .pkg-name { color: var(--text2); }
  .pkg-val { font-weight: 500; }
  .quota-bar { height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; }
  .quota-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }
  .quota-fill.warn { background: var(--warn); }
  .acc-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; font-size: 12px; color: var(--text3); gap: 8px; flex-wrap: wrap; }
  .acc-actions { display: flex; gap: 10px; align-items: center; }
  .switch-btn { background: transparent; border: 1px solid var(--accent); color: var(--accent); font-size: 12px; padding: 5px 14px; border-radius: 6px; cursor: pointer; font-weight: 500; }
  .switch-btn:hover { background: var(--accent); color: #fff; }
  .switch-btn:disabled { border-color: var(--text3); color: var(--text3); cursor: not-allowed; }
  .refresh-btn { background: transparent; border: 1px solid var(--border); color: var(--text2); font-size: 11px; padding: 2px 10px; border-radius: 5px; cursor: pointer; margin-left: auto; }
  .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }
  .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .sync-radio { font-size: 12px; color: var(--text2); cursor: pointer; display: flex; align-items: center; gap: 4px; }
  .sync-radio input { accent-color: var(--accent); }
  .spin { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--text3); border-top-color: var(--accent); border-radius: 50%; animation: sp 0.7s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes sp { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <h1>WorkBuddy 会话同步器</h1>
  <p class="sub">过户模式 · 把所有账号的会话归并到当前登录账号</p>

  <div class="card">
    <div class="label">当前 cockpit 登录账号</div>
    <div class="current-uid" id="current">检测中...</div>
  </div>

  <div class="label" style="margin-bottom:12px">账号列表 · 点"切换登录"换号 · 勾选后点下方按钮同步会话</div>
  <div id="dist"><span class="spin"></span>加载中</div>

  <button class="btn" id="syncBtn" disabled>同步到选中账号</button>

  <div class="log" id="log"></div>

  <div class="tip">
    <b>使用步骤</b><br>
    1. 在上方选择目标账号（默认当前登录账号）<br>
    2. 点击按钮，把所有会话过户到该账号<br>
    3. 去 cockpit 切换到该账号（若尚未切换）<br>
    4. <b>重启 WorkBuddy</b>，即可看到全部会话<br><br>
    <b>原理</b>：将所有会话的 user_id 改为选中的目标账号。会话跟随目标账号。<br>
    <b>切换登录态</b>：cockpit 是 WorkBuddy 内置的账号管理，登录 token 由后端管理，需在 cockpit 内切换。本工具负责会话归属同步。<br>
    <b>安全</b>：每次同步自动备份 workbuddy.db，可随时回滚。
  </div>

<script>
const $ = id => document.getElementById(id);
function appendLog(text, cls) {
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = text;
  $('log').appendChild(line);
  $('log').scrollTop = $('log').scrollHeight;
}

async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (!d.current) {
      $('current').textContent = '未检测到（请先用 cockpit 登录任意账号）';
      $('current').style.color = 'var(--warn)';
      return;
    }
    $('current').textContent = d.current;
    $('syncBtn').disabled = false;

    let html = '';
    for (const a of d.accounts) {
      const pct = (q) => q.total > 0 ? Math.min(100, Math.round(q.used/q.total*100)) : 0;
      const fillCls = (q) => pct(q) >= 80 ? ' warn' : '';
      html += '<div class="acc-card' + (a.is_current ? ' cur' : '') + '">';
      html += '<div class="acc-head"><span class="acc-nick">' + a.nickname + '</span>';
      html += '<span class="badge-free">' + (a.payment_type||'free').toUpperCase() + '</span>';
      if (a.is_current) html += '<span class="badge-cur">当前登录</span>';
      html += '</div>';
      html += '<div class="pkg"><div class="pkg-row"><span class="pkg-name">积分</span><span class="pkg-val" id="quota-' + a.user_id + '">' + a.total.used + '/' + a.total.total + ' ' + a.total.unit + '</span><button class="refresh-btn" data-uid="' + a.user_id + '">刷新</button></div>';
      html += '<div class="quota-bar" id="bar-' + a.user_id + '"><div class="quota-fill' + fillCls(a.total) + '" style="width:' + pct(a.total) + '%"></div></div></div>';
      html += '<div class="acc-foot"><span>' + a.sessions + ' 会话' + (a.cycle_end ? ' · 周期至 ' + a.cycle_end : '') + '</span>';
      html += '<div class="acc-actions">';
      const swBtn = a.is_current ? '当前账号' : (a.has_token ? '切换并同步' : '无token');
      html += '<button class="switch-btn" data-uid="' + a.user_id + '" ' + (a.is_current || !a.has_token ? 'disabled' : '') + '>' + swBtn + '</button>';
      html += '<label class="sync-radio"><input type="radio" name="target" value="' + a.user_id + '"' + (a.is_current ? ' checked' : '') + '>同步到此</label>';
      html += '</div></div></div>';
    }
    $('dist').innerHTML = html;
  } catch (e) {
    $('dist').innerHTML = '<span class="err">加载失败: ' + e.message + '</span>';
  }
}

async function switchAccount(uid) {
  if (!confirm('确认切换登录到该账号？\\n切换后会写入认证凭据，需重启 WorkBuddy 生效。')) return;
  appendLog('切换登录到 ' + uid.slice(0,8) + '…');
  try {
    const r = await fetch('/api/switch', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({uid}) });
    const d = await r.json();
    if (d.ok) {
      appendLog('登录已切换: ' + d.switch_detail, 'ok');
      appendLog('会话已过户: ' + d.moved_sessions + ' 条', 'ok');
      appendLog('该账号现有会话: ' + d.total_now + ' 条', 'ok');
      appendLog('请重启 WorkBuddy —— 重启后登录+会话全部到位', 'warn');
    } else {
      appendLog('失败: ' + d.error, 'err');
    }
  } catch (e) {
    appendLog('错误: ' + e.message, 'err');
  }
}

$('syncBtn').onclick = async () => {
  $('syncBtn').disabled = true;
  $('log').innerHTML = '';
  appendLog('开始同步...');
  try {
    const target = document.querySelector('input[name=target]:checked')?.value;
    const r = await fetch('/api/sync', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({target}) });
    const d = await r.json();
    if (d.ok) {
      appendLog('备份完成: ' + d.backup, 'ok');
      if (d.from_accounts.length) {
        for (const a of d.from_accounts) {
          appendLog('  从 ' + a.uid + '… 过户 ' + a.count + ' 条');
        }
      } else {
        appendLog('  无需过户（当前账号已是全部会话归属）', 'warn');
      }
      appendLog('会话过户: ' + d.moved_sessions + ' 条', 'ok');
      appendLog('自动化过户: ' + d.moved_automations + ' 条', 'ok');
      appendLog('当前账号现有会话: ' + d.total_now + ' 条', 'ok');
      appendLog('', '');
      appendLog('请重启 WorkBuddy 使变更生效', 'warn');
    } else {
      appendLog('失败: ' + d.error, 'err');
    }
  } catch (e) {
    appendLog('错误: ' + e.message, 'err');
  } finally {
    $('syncBtn').disabled = false;
    loadStatus();
  }
};

async function refreshQuota(uid) {
  const btn = document.querySelector('.refresh-btn[data-uid="'+uid+'"]');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  appendLog('刷新积分 ' + uid.slice(0,8) + '…');
  try {
    const r = await fetch('/api/refresh', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({uid}) });
    const d = await r.json();
    if (d.ok) {
      const el = document.getElementById('quota-'+uid);
      const bar = document.getElementById('bar-'+uid);
      if (el) el.textContent = d.used + '/' + d.total + ' credits';
      if (bar) { const fill = bar.querySelector('.quota-fill'); const p = d.total>0?Math.min(100,Math.round(d.used/d.total*100)):0; if(fill) fill.style.width = p+'%'; }
      appendLog('  实时: ' + d.used + '/' + d.total + ' credits (' + d.packages + '包)', 'ok');
    } else { appendLog('  刷新失败: ' + d.error, 'err'); }
  } catch(e) { appendLog('  错误: ' + e.message, 'err'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = '刷新'; } }
}

document.addEventListener('click', e => {
  const sw = e.target.closest('.switch-btn');
  if (sw && !sw.disabled) { switchAccount(sw.dataset.uid); return; }
  const rf = e.target.closest('.refresh-btn');
  if (rf && !rf.disabled) refreshQuota(rf.dataset.uid);
});
loadStatus();
setInterval(loadStatus, 5000);
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path.startswith("/?"):
            self._html()
        elif path == "/api/status":
            current = get_current_account()
            self._json({
                "current": current,
                "accounts": get_all_accounts(),
            })
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/sync":
            # 读取 body 中的 target（选中的目标账号），没有则用当前登录账号
            length = int(self.headers.get("Content-Length", 0))
            target = get_current_account()
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                    if body.get("target"):
                        target = body["target"]
                except Exception:
                    pass
            log(f"同步请求，目标账号: {target}")
            result = do_sync(target)
            log(f"同步结果: ok={result.get('ok')} moved={result.get('moved_sessions')}")
            self._json(result)
        elif path == "/api/switch":
            length = int(self.headers.get("Content-Length", 0))
            uid = None
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                    uid = body.get("uid")
                except Exception:
                    pass
            if not uid:
                self._json({"ok": False, "error": "缺少 uid"})
                return
            log(f"切换+同步请求: {uid}")
            result = do_switch_full(uid)
            log(f"切换+同步结果: ok={result.get('ok')} restarted={result.get('restarted')}")
            self._json(result)
        elif path == "/api/refresh":
            length = int(self.headers.get("Content-Length", 0))
            uid = None
            if length:
                try:
                    uid = json.loads(self.rfile.read(length)).get("uid")
                except Exception:
                    pass
            log(f"刷新积分: {uid}")
            result = refresh_quota(uid) if uid else {"ok": False, "error": "缺少 uid"}
            self._json(result)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # 静默 HTTP 访问日志


def main():
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        port = int(sys.argv[i + 1])

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    log(f"WorkBuddy 会话同步器已启动: {url}")
    log("按 Ctrl+C 退出")

    # 延迟打开浏览器
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("已退出")
        server.shutdown()


if __name__ == "__main__":
    main()
