#!/usr/bin/env python3
"""
WorkBuddy 跨账号会话同步脚本（方案 B：合并 + 守护进程）

用法:
  # 1. 预演（只看会改什么，不动数据）
  python3 workbuddy-session-sync.py --dry-run

  # 2. 一次性合并所有账号会话到主账号
  python3 workbuddy-session-sync.py --merge-once

  # 3. 守护模式：常驻轮询，新会话自动归并（建议配合 launchd/pm2 常驻）
  python3 workbuddy-session-sync.py --watch

  # 4. 指定主账号（默认自动选会话最多的账号）
  python3 workbuddy-session-sync.py --merge-once --primary bb7bd6e3-xxxx

  # 5. 识别当前 cockpit 登录的账号
  python3 workbuddy-session-sync.py --whoami

  # 6. 列出某账号的会话（默认主账号）
  python3 workbuddy-session-sync.py --list

  # 7. 克隆一条会话到当前账号（测试同步）
  python3 workbuddy-session-sync.py --clone --from <会话id>
  python3 workbuddy-session-sync.py --clone --from <会话id> --to 41e15573-xxxx

依赖: 仅 Python 标准库 sqlite3/json/uuid，零安装。
注意: workbuddy.db 是 WAL 模式；合并时建议先退出 WorkBuddy 以避免写锁冲突。
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime

DB_PATH = os.path.expanduser("~/.workbuddy/workbuddy.db")
SESSIONS_JSON = os.path.expanduser("~/.workbuddy/app/sessions.json")
PROJECTS_DIR = os.path.expanduser("~/.workbuddy/projects")
TASKS_DIR = os.path.expanduser("~/.workbuddy/tasks")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def list_accounts(conn):
    """返回 [(user_id, 会话数)] 按会话数降序"""
    cur = conn.execute(
        "SELECT user_id, COUNT(*) FROM sessions "
        "WHERE deleted_at IS NULL GROUP BY user_id ORDER BY COUNT(*) DESC"
    )
    return cur.fetchall()


def pick_primary(conn):
    accounts = list_accounts(conn)
    if not accounts:
        sys.exit("错误: sessions 表为空")
    return accounts[0][0], accounts


def backup_db():
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak = f"{DB_PATH}.bak.{ts}"
    shutil.copy2(DB_PATH, bak)
    log(f"已备份 -> {bak}")


def whoami():
    """从 app/sessions.json 读最新 resumedAt 的 userId，即当前 cockpit 登录账号"""
    if not os.path.exists(SESSIONS_JSON):
        sys.exit(f"找不到 {SESSIONS_JSON}")
    data = json.load(open(SESSIONS_JSON))
    sessions = data.get("sessions", [])
    if not sessions:
        sys.exit("sessions.json 无活跃会话记录")
    latest = max(sessions, key=lambda s: s.get("resumedAt", ""))
    uid = latest.get("userId", "")
    conv = latest.get("conversationId", "")
    cwd = latest.get("workDir", "")
    log(f"当前登录账号: {uid}")
    log(f"  最新会话: {conv}")
    log(f"  工作目录: {cwd}")
    return uid


def list_sessions(conn, uid=None, limit=20):
    """列出某账号的会话；uid 为空则列全部"""
    if uid:
        cur = conn.execute(
            "SELECT substr(id,1,8), substr(title,1,40), status, "
            "datetime(updated_at/1000,'unixepoch','localtime') "
            "FROM sessions WHERE user_id=? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (uid, limit),
        )
        log(f"=== 账号 {uid[:8]}… 的会话 ===")
    else:
        cur = conn.execute(
            "SELECT substr(id,1,8), user_id, substr(title,1,36), status, "
            "datetime(updated_at/1000,'unixepoch','localtime') "
            "FROM sessions WHERE deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        log("=== 全部会话（最近）===")
    for row in cur.fetchall():
        print("  " + "  ".join(str(x) for x in row))


def clone_one(conn, src_id, target_uid, dry_run=False):
    """克隆一条会话到目标账号：复制 session 记录 + jsonl 对话文件 + tasks 目录"""
    row = conn.execute(
        "SELECT id, cwd, user_id, title FROM sessions WHERE id=?", (src_id,)
    ).fetchone()
    if not row:
        sys.exit(f"找不到源会话: {src_id}")
    src_id, cwd, src_uid, title = row
    dst_id = str(uuid.uuid4())
    now = int(time.time() * 1000)

    log(f"克隆: {src_id[:8]}… ({src_uid[:8]}…) -> {dst_id[:8]}… ({target_uid[:8]}…)")
    log(f"  标题: {title}")

    if not dry_run:
        backup_db()
        conn.execute(
            "INSERT INTO sessions (id, cwd, user_id, title, custom_title, status, "
            "created_at, updated_at, deleted_at, is_playground, source_mode, "
            "is_background_automation, mode, model, expert_id, expert_locale, "
            "expert_runtime_identity, expert_marketplace, permission_mode, "
            "last_activity_at, use_sandbox_cli, project_id) "
            "SELECT ?, cwd, ?, '[同步] '||title, custom_title, status, created_at, ?, "
            "deleted_at, is_playground, source_mode, is_background_automation, mode, "
            "model, expert_id, expert_locale, expert_runtime_identity, expert_marketplace, "
            "permission_mode, ?, use_sandbox_cli, project_id FROM sessions WHERE id=?",
            (dst_id, target_uid, now, now, src_id),
        )
        conn.commit()

        # 复制对话内容 jsonl
        projdir = cwd.lstrip("/").replace("/", "-")
        src_jsonl = os.path.join(PROJECTS_DIR, projdir, f"{src_id}.jsonl")
        dst_jsonl = os.path.join(PROJECTS_DIR, projdir, f"{dst_id}.jsonl")
        if os.path.exists(src_jsonl):
            shutil.copy2(src_jsonl, dst_jsonl)
            log(f"  已复制对话文件 ({os.path.getsize(dst_jsonl)} bytes)")
        else:
            log(f"  警告: 源 jsonl 不存在")

        # 复制任务目录（如有）
        src_tasks = os.path.join(TASKS_DIR, src_id)
        dst_tasks = os.path.join(TASKS_DIR, dst_id)
        if os.path.isdir(src_tasks):
            shutil.copytree(src_tasks, dst_tasks)
            log(f"  已复制任务目录")
    else:
        log("  (dry-run，未写入)")

    # 验证
    cnt = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id=? AND deleted_at IS NULL",
        (target_uid,),
    ).fetchone()[0]
    log(f"  目标账号 {target_uid[:8]}… 现可见会话数: {cnt}")
    return dst_id


def merge(conn, primary, dry_run=False):
    """把非主账号的 sessions 和 automations 过户到主账号"""
    if not dry_run:
        backup_db()

    # sessions
    cur = conn.execute(
        "SELECT user_id, COUNT(*) FROM sessions "
        "WHERE user_id != ? AND deleted_at IS NULL GROUP BY user_id",
        (primary,),
    )
    others = cur.fetchall()
    total = sum(n for _, n in others) if others else 0
    log(f"待过户会话: {total} 条（来自 {len(others)} 个其他账号）")
    for uid, n in (others or []):
        log(f"  {uid[:8]}…  {n} 条")

    if not dry_run and total:
        conn.execute(
            "UPDATE sessions SET user_id = ? "
            "WHERE user_id != ? AND deleted_at IS NULL",
            (primary, primary),
        )
        # automations
        conn.execute(
            "UPDATE automations SET owner_user_id = ?, owner_status = 'confirmed' "
            "WHERE owner_user_id != ? AND deleted_at IS NULL",
            (primary, primary),
        )
        conn.commit()
        log(f"已完成合并 -> 主账号 {primary[:8]}…")
    elif dry_run:
        log("(dry-run，未写入)")


def watch(conn, primary, interval=10):
    """守护模式：轮询 sessions，发现 user_id != 主账号 的新会话即过户"""
    log(f"守护模式启动，主账号 {primary[:8]}…，轮询间隔 {interval}s")
    log("按 Ctrl+C 退出")
    while True:
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM sessions "
                "WHERE user_id != ? AND deleted_at IS NULL",
                (primary,),
            )
            n = cur.fetchone()[0]
            if n:
                log(f"发现 {n} 条非主账号会话，过户中…")
                conn.execute(
                    "UPDATE sessions SET user_id = ? WHERE user_id != ? AND deleted_at IS NULL",
                    (primary, primary),
                )
                conn.execute(
                    "UPDATE automations SET owner_user_id = ?, owner_status='confirmed' "
                    "WHERE owner_user_id != ? AND deleted_at IS NULL",
                    (primary, primary),
                )
                conn.commit()
                log(f"已过户 {n} 条")
        except sqlite3.OperationalError as e:
            log(f"DB 忙，跳过本轮: {e}")
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 跨账号会话同步")
    ap.add_argument("--dry-run", action="store_true", help="只预演不写入")
    ap.add_argument("--merge-once", action="store_true", help="一次性合并")
    ap.add_argument("--watch", action="store_true", help="守护轮询模式")
    ap.add_argument("--primary", help="指定主账号 user_id（默认自动选）")
    ap.add_argument("--interval", type=int, default=10, help="轮询间隔秒数")
    ap.add_argument("--whoami", action="store_true", help="识别当前 cockpit 登录账号")
    ap.add_argument("--list", action="store_true", help="列出会话（默认主账号，--all 列全部）")
    ap.add_argument("--all", action="store_true", help="配合 --list 列出全部账号会话")
    ap.add_argument("--clone", action="store_true", help="克隆一条会话到目标账号")
    ap.add_argument("--from", dest="from_id", help="克隆源会话 id")
    ap.add_argument("--to", dest="to_uid", help="克隆目标账号 user_id（默认当前登录账号）")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"找不到数据库: {DB_PATH}")

    # --whoami 不需要连 DB
    if args.whoami:
        whoami()
        return

    conn = connect()

    if args.primary:
        primary = args.primary
        accounts = list_accounts(conn)
    else:
        primary, accounts = pick_primary(conn)

    if args.list:
        list_sessions(conn, None if args.all else primary)
        conn.close()
        return

    if args.clone:
        if not args.from_id:
            sys.exit("克隆需要 --from <会话id>，先用 --list 查看")
        target = args.to_uid or whoami()
        clone_one(conn, args.from_id, target, dry_run=args.dry_run)
        conn.close()
        return

    log("=== 账号分布 ===")
    for uid, n in accounts:
        mark = " ← 主账号" if uid == primary else ""
        log(f"  {uid[:8]}…  {n} 会话{mark}")

    if args.dry_run:
        merge(conn, primary, dry_run=True)
    elif args.merge_once:
        merge(conn, primary, dry_run=False)
    elif args.watch:
        # 守护前先合并一次
        merge(conn, primary, dry_run=False)
        watch(conn, primary, args.interval)
    else:
        ap.print_help()
        log("\n提示: 先用 --dry-run 看看会改什么，再用 --merge-once 合并。")

    conn.close()


if __name__ == "__main__":
    main()
