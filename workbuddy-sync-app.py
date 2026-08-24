#!/usr/bin/env python3
"""
AI 账号坞（AI Account Dock）- 多平台账号管理独立带界面应用

用法:
  python3 workbuddy-sync-app.py            # 启动并自动打开浏览器
  python3 workbuddy-sync-app.py --port 8000 # 指定端口
  python3 workbuddy-sync-app.py --no-browser # 仅启动服务（桌面壳使用）
  WB_LAN_ACCESS_TOKEN=随机口令 python3 workbuddy-sync-app.py --lan --port 7532 --no-browser

工作流:
  1. 用 cockpit 切换到目标账号
  2. 打开本应用，点"一键同步"
  3. 所有会话过户到当前登录账号，重启 WorkBuddy 即可看到全部

账号库读取使用本机已安装的 cryptography。
"""

import base64
import glob
import hashlib
import hmac
import http.server
import json
import os
import shutil
import sqlite3
import secrets
import struct
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from platforms import get_platform, list_platforms

DB_PATH = os.path.expanduser("~/.workbuddy/workbuddy.db")
SESSIONS_JSON = os.path.expanduser("~/.workbuddy/app/sessions.json")
SETTINGS_JSON = os.path.expanduser("~/.workbuddy/settings.json")
DEFAULT_PORT = 7531
STARTED_AT = time.time()
EXPORT_GLOB = os.path.expanduser("~/Downloads/workbuddy_accounts_*.json")
AUTH_SESSION_PATH = os.path.expanduser(
    "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"
)
COCKPIT_DIR = os.path.expanduser("~/.antigravity_cockpit")
COCKPIT_ACCOUNTS_DIR = os.path.join(COCKPIT_DIR, "workbuddy_accounts")
COCKPIT_KEY_PATH = os.path.join(COCKPIT_DIR, "secure-account-storage.key")
COCKPIT_INDEX_PATH = os.path.join(COCKPIT_DIR, "workbuddy_accounts.json")
WORKBUDDY_API = "https://www.codebuddy.cn"
AUTH_TTL_SECONDS = 600
AUTH_POLL_SECONDS = 2
TOTP_STEP_SECONDS = 30
PENDING_AUTH = {}


def is_request_authorized(headers, expected_token):
    """局域网桥接只接受配对口令；本机模式保持原有无口令行为。"""
    if not expected_token:
        return True
    provided = headers.get("X-WorkBuddy-Access-Token", "")
    if not provided:
        authorization = headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            provided = authorization.removeprefix("Bearer ")
    return secrets.compare_digest(provided, expected_token)


def find_accounts_export():
    """找最新的账号导出文件（~/Downloads/workbuddy_accounts_*.json）"""
    files = sorted(glob.glob(EXPORT_GLOB), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def cockpit_cipher():
    """读取 Cockpit 自己的本地密钥；缺失时不创建、不降级为明文。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = base64.b64decode(open(COCKPIT_KEY_PATH).read().strip())
        if len(key) != 32:
            raise ValueError("密钥长度无效")
        return AESGCM(key)
    except Exception as e:
        raise RuntimeError(f"无法读取 Cockpit 账号库：{e}")


def read_cockpit_accounts():
    cipher = cockpit_cipher()
    result = {}
    for path in glob.glob(os.path.join(COCKPIT_ACCOUNTS_DIR, "workbuddy_*.json")):
        try:
            envelope = json.load(open(path))
            raw = cipher.decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                None,
            )
            account = json.loads(raw)
            uid = account.get("uid")
            if uid:
                result[uid] = account
        except Exception:
            continue
    return result


def write_cockpit_account(account):
    """以 Cockpit 的 AES-256-GCM 信封格式原子保存账号。"""
    cipher = cockpit_cipher()
    uid = account.get("uid")
    if not uid:
        raise RuntimeError("授权账号缺少 UID")
    now = int(time.time())
    account.setdefault("created_at", now)
    account.setdefault("last_used", now)
    account_id = f"workbuddy_{hashlib.md5(uid.lower().encode()).hexdigest()}"
    account["id"] = account_id
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, json.dumps(account, ensure_ascii=False).encode(), None)
    envelope = {
        "version": 1,
        "kind": "workbuddy",
        "algorithm": "AES-256-GCM",
        "key_id": "local-secure-account-storage-v1",
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(encrypted).decode(),
        "encrypted_at": int(time.time()),
    }
    os.makedirs(COCKPIT_ACCOUNTS_DIR, exist_ok=True)
    target = os.path.join(COCKPIT_ACCOUNTS_DIR, f"{account_id}.json")
    temporary = f"{target}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)

    index = json.load(open(COCKPIT_INDEX_PATH)) if os.path.exists(COCKPIT_INDEX_PATH) else {"version": "1.0", "accounts": []}
    summary = {"id": account_id, "email": account.get("email", account.get("nickname", uid)), "created_at": account["created_at"], "last_used": account["last_used"]}
    index["accounts"] = [item for item in index.get("accounts", []) if item.get("id") != account_id] + [summary]
    temp_index = f"{COCKPIT_INDEX_PATH}.{os.getpid()}.tmp"
    with open(temp_index, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_index, COCKPIT_INDEX_PATH)


def api_request(path, method="GET", payload=None, headers=None, allow_pending=False):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"{WORKBUDDY_API}{path}", data=body, method=method, headers=headers or {})
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode())
    code = data.get("code", 0)
    if code not in (0, 200):
        if allow_pending:
            return {}
        raise RuntimeError(data.get("message") or data.get("msg") or f"请求失败 ({code})")
    return data.get("data", {})


def load_accounts_export():
    """读导出文件，返回切换登录所需的完整账号会话。"""
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
        result[uid] = {
            "nickname": acc.get("nickname", uid[:8]),
            "access_token": acc.get("access_token", ""),
            "refresh_token": acc.get("refresh_token", ""),
            "token_type": acc.get("token_type", "Bearer"),
            "expires_at": acc.get("expires_at"),
            "domain": acc.get("domain", "www.codebuddy.cn"),
            "auth_raw": acc.get("auth_raw"),
            "payment_type": acc.get("payment_type", "free"),
            "export_file": os.path.basename(path),
        }
    return result


def prepare_import_accounts(value):
    """兼容 Cockpit 的对象、数组、accounts/items 包装 JSON。"""
    if isinstance(value, dict):
        values = value.get("accounts", value.get("items", [value]))
    else:
        values = value
    if not isinstance(values, list) or not values:
        raise RuntimeError("导入 JSON 必须是账号对象、账号数组或 accounts/items 包装对象")
    accounts = []
    for index, raw in enumerate(values, 1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"第 {index} 条账号不是对象")
        access_token = raw.get("access_token") or raw.get("accessToken") or raw.get("token")
        uid = raw.get("uid")
        if not access_token or not uid:
            raise RuntimeError(f"第 {index} 条账号缺少 uid 或 access_token")
        account = {
            "uid": uid,
            "nickname": raw.get("nickname") or uid[:8],
            "email": raw.get("email") or raw.get("nickname") or uid,
            "access_token": access_token,
            "refresh_token": raw.get("refresh_token") or raw.get("refreshToken"),
            "enterprise_id": raw.get("enterprise_id") or raw.get("enterpriseId"),
        }
        optional = {
            "enterprise_name": raw.get("enterprise_name") or raw.get("enterpriseName"),
            "token_type": raw.get("token_type") or raw.get("tokenType"),
            "expires_at": raw.get("expires_at") or raw.get("expiresAt"),
            "domain": raw.get("domain"),
            "auth_raw": raw.get("auth_raw"),
            "profile_raw": raw.get("profile_raw"),
            "quota_raw": raw.get("quota_raw"),
            "usage_raw": raw.get("usage_raw"),
            "payment_type": raw.get("payment_type"),
            "status": raw.get("status"),
        }
        account.update({key: item for key, item in optional.items() if item is not None})
        accounts.append(account)
    return accounts


def import_accounts_from_json(content):
    try:
        accounts = prepare_import_accounts(json.loads(content))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"JSON 格式错误：{error.msg}")
    for account in accounts:
        write_cockpit_account(account)
    return [{"uid": account["uid"], "nickname": account["nickname"]} for account in accounts]


def serialize_accounts_for_export(uids, accounts):
    if not isinstance(uids, list) or not uids:
        raise RuntimeError("请至少选择一个账号")
    payload = []
    for uid in dict.fromkeys(uids):
        account = accounts.get(uid)
        if not account or not account.get("access_token"):
            raise RuntimeError(f"账号 {uid[:8]} 没有可导出的授权信息")
        item = json.loads(json.dumps(account))
        item["uid"] = uid
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_current_account_json():
    current = get_current_account()
    return serialize_accounts_for_export([current] if current else [], load_accounts())


def quota_groups(account):
    """按 Cockpit 的官方资源分组显示配额。"""
    groups = {name: {"used": 0.0, "total": 0.0} for name in ("base", "activity", "extra", "other")}
    codes = {
        "base": {"TCACA_code_001_PqouKr6QWV", "TCACA_code_002_AkiJS3ZHF5", "TCACA_code_003_FAnt7lcmRT", "TCACA_code_006_DbXS0lrypC", "TCACA_code_008_cfWoLwvjU4"},
        "activity": {"TCACA_code_007_nzdH5h4Nl0"},
    }
    quota = account.get("quota_raw", {})
    source = quota.get("userResource") if isinstance(quota, dict) else None
    source = source if isinstance(source, dict) else account.get("usage_raw", {})
    resources = source.get("data", {}).get("Response", {}).get("Data", {}).get("Accounts", []) if isinstance(source, dict) else []
    for resource in resources:
        if resource.get("Status") not in (0, 3):
            continue
        code = resource.get("PackageCode")
        group = "base" if code in codes["base"] else "activity" if code in codes["activity"] else "extra" if code == "TCACA_code_009_0XmEQc2xOf" else "other"
        total = resource.get("CycleCapacitySizePrecise", resource.get("CycleCapacitySize", resource.get("CapacitySizePrecise", resource.get("CapacitySize", 0))))
        remain = resource.get("CycleCapacityRemainPrecise", resource.get("CycleCapacityRemain", resource.get("CapacityRemainPrecise", resource.get("CapacityRemain"))))
        used = float(total or 0) - float(remain) if remain is not None else float(resource.get("CapacityUsedPrecise", resource.get("CapacityUsed", 0)) or 0)
        groups[group]["used"] += max(0, used)
        groups[group]["total"] += float(total or 0)
    return groups


def refresh_cockpit_account(uid):
    accounts = read_cockpit_accounts()
    account = accounts.get(uid)
    if not account:
        raise RuntimeError("Cockpit 账号库中找不到该账号")
    access_token = account.get("access_token", "")
    refresh_token = account.get("refresh_token")
    headers = {"Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if refresh_token:
        try:
            token_headers = {**headers, "Authorization": f"Bearer {access_token}", "X-Refresh-Token": refresh_token}
            if account.get("domain"):
                token_headers["X-Domain"] = account["domain"]
            token_data = api_request(
                "/v2/plugin/auth/token/refresh",
                "POST",
                {},
                token_headers,
            )
            access_token = token_data.get("accessToken", token_data.get("access_token", access_token))
            refresh_token = token_data.get("refreshToken", token_data.get("refresh_token", refresh_token))
            account["access_token"] = access_token
            account["refresh_token"] = refresh_token
            account["expires_at"] = token_data.get("expiresAt", token_data.get("expires_at", account.get("expires_at")))
            account["domain"] = token_data.get("domain", account.get("domain"))
        except Exception as error:
            log(f"Cockpit token 刷新失败，继续使用已有 token 查询配额：{error}")

    resource_headers = {**headers, "Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "X-User-Id": uid}
    enterprise_id = account.get("enterprise_id")
    if enterprise_id:
        resource_headers["X-Enterprise-Id"] = enterprise_id
        resource_headers["X-Tenant-Id"] = enterprise_id
    if account.get("domain"):
        resource_headers["X-Domain"] = account["domain"]
    now = datetime.now()
    dosage = api_request("/v2/billing/meter/get-dosage-notify", "POST", None, resource_headers)
    payment = api_request("/v2/billing/meter/get-payment-type", "POST", None, resource_headers)
    user_resource = {"data": api_request(
        "/v2/billing/meter/get-user-resource",
        "POST",
        {"PageNumber": 1, "PageSize": 100, "ProductCode": "p_tcaca", "Status": [0, 3],
         "PackageEndTimeRangeBegin": now.strftime("%Y-%m-%d %H:%M:%S"),
         "PackageEndTimeRangeEnd": (now + timedelta(days=365 * 101)).strftime("%Y-%m-%d %H:%M:%S")},
        resource_headers,
    )}
    account["usage_raw"] = user_resource
    account["quota_raw"] = {"dosage": {"data": dosage}, "payment": {"data": payment}, "userResource": user_resource}
    if isinstance(dosage, dict) and dosage.get("dosageNotifyCode") is not None:
        account["dosage_notify_code"] = str(dosage["dosageNotifyCode"])
    payment_type = payment.get("paymentType") if isinstance(payment, dict) else payment
    if isinstance(payment_type, str) and payment_type:
        account["payment_type"] = payment_type
    auth_raw = account.get("auth_raw")
    if isinstance(auth_raw, dict):
        auth = auth_raw.get("auth") if isinstance(auth_raw.get("auth"), dict) else auth_raw
        auth["accessToken"] = access_token
        auth["refreshToken"] = refresh_token
        if account.get("expires_at") is not None:
            auth["expiresAt"] = account["expires_at"]
    account["last_used"] = int(time.time())
    write_cockpit_account(account)
    return account


def load_accounts():
    """Cockpit 账号库为主源；旧导出只作为迁移失败时的切换回退。"""
    try:
        accounts = read_cockpit_accounts()
        if accounts:
            return accounts
    except RuntimeError as error:
        log(str(error))
    return load_accounts_export()


def refresh_quota(target_uid):
    account = refresh_cockpit_account(target_uid)
    groups = quota_groups(account)
    used = sum(group["used"] for group in groups.values())
    total = sum(group["total"] for group in groups.values())
    return {"ok": True, "used": used, "total": total, "groups": groups, "unit": "credits"}


def start_cockpit_authorization():
    data = api_request("/v2/plugin/auth/state?platform=workbuddy", "POST", {}, {"Content-Type": "application/json"})
    state = data.get("state")
    if not state:
        raise RuntimeError("授权响应缺少 state")
    url = data.get("authUrl") or f"{WORKBUDDY_API}/login?state={state}"
    login_id = hashlib.md5(f"{state}:{time.time()}".encode()).hexdigest()
    PENDING_AUTH[login_id] = {"state": state, "url": url, "expires_at": time.time() + AUTH_TTL_SECONDS}
    return {
        "login_id": login_id,
        "url": url,
        "ttl": AUTH_TTL_SECONDS,
        "poll_interval": AUTH_POLL_SECONDS,
    }


def open_authorization_url(login_id):
    """用系统默认浏览器打开授权链接；仅允许 CodeBuddy 官方域名。"""
    pending = PENDING_AUTH.get(login_id or "")
    if not pending or time.time() > pending["expires_at"]:
        return {"ok": False, "error": "授权会话不存在或已过期，请重新生成链接"}
    url = pending.get("url", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.codebuddy.cn":
        return {"ok": False, "error": "拒绝打开非 CodeBuddy 官方域名的授权链接"}
    threading.Timer(0.1, lambda: webbrowser.open(url)).start()
    return {"ok": True, "url": url}


def poll_cockpit_authorization(login_id):
    pending = PENDING_AUTH.get(login_id)
    if not pending or time.time() > pending["expires_at"]:
        PENDING_AUTH.pop(login_id, None)
        return {"ok": False, "pending": False, "error": "授权已过期"}
    data = api_request(f"/v2/plugin/auth/token?state={pending['state']}", allow_pending=True)
    access_token = data.get("accessToken", data.get("access_token", ""))
    if not access_token:
        return {"ok": True, "pending": True}
    profile_headers = {"Authorization": f"Bearer {access_token}"}
    if data.get("domain"):
        profile_headers["X-Domain"] = data["domain"]
    profile = api_request(f"/v2/plugin/login/account?state={pending['state']}", headers=profile_headers)
    uid = profile.get("uid")
    if not uid:
        raise RuntimeError("授权响应缺少 UID")
    account = {
        "uid": uid, "nickname": profile.get("nickname") or uid[:8], "email": profile.get("email") or profile.get("nickname") or uid,
        "enterprise_id": profile.get("enterpriseId"), "enterprise_name": profile.get("enterpriseName"),
        "access_token": access_token, "refresh_token": data.get("refreshToken", data.get("refresh_token")),
        "token_type": data.get("tokenType", data.get("token_type", "Bearer")), "expires_at": data.get("expiresAt", data.get("expires_at")),
        "domain": data.get("domain", "www.codebuddy.cn"), "auth_raw": data, "profile_raw": profile,
        "created_at": int(time.time()), "last_used": int(time.time()),
    }
    write_cockpit_account(account)
    try:
        refresh_cockpit_account(uid)
    except Exception as error:
        log(f"新账号已授权，但首次额度刷新失败：{error}")
    PENDING_AUTH.pop(login_id, None)
    return {"ok": True, "pending": False, "uid": uid, "nickname": account["nickname"]}


def prepare_token_import(payload):
    """解析 Token 导入输入：支持整段账号 JSON 或纯 accessToken 文本。"""
    raw = (payload.get("raw") or "").strip()
    if not raw:
        raise RuntimeError("请粘贴 accessToken 或账号 JSON")
    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"JSON 格式错误：{error.msg}")
        values = parsed if isinstance(parsed, list) else [parsed]
        if len(values) != 1 or not isinstance(values[0], dict):
            raise RuntimeError("Token 导入一次只支持一个账号对象；批量请用 JSON 文件导入")
        merged = {**values[0]}
        if not merged.get("uid"):
            merged["uid"] = (payload.get("uid") or "").strip() or None
        accounts = prepare_import_accounts(merged)
        return accounts[0], "json"
    uid = (payload.get("uid") or "").strip()
    if not uid:
        raise RuntimeError("纯 token 导入必须同时填写账号 UID（JSON 导入则无需填写）")
    return {
        "uid": uid,
        "nickname": (payload.get("nickname") or "").strip() or uid[:8],
        "email": (payload.get("email") or "").strip() or uid,
        "access_token": raw,
        "refresh_token": (payload.get("refresh_token") or "").strip() or None,
        "domain": (payload.get("domain") or "").strip() or "www.codebuddy.cn",
    }, "token"


def import_account_from_token(payload):
    """导入前先用刷新接口校验 token 有效性，避免写入无效账号。"""
    account, source = prepare_token_import(payload)
    refreshed = False
    if account.get("refresh_token"):
        try:
            data = api_request(
                "/v2/plugin/auth/token/refresh",
                "POST",
                {},
                {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {account['access_token']}",
                    "X-Refresh-Token": account["refresh_token"],
                    **({"X-Domain": account["domain"]} if account.get("domain") else {}),
                },
            )
            account["access_token"] = data.get("accessToken", data.get("access_token", account["access_token"]))
            account["refresh_token"] = data.get("refreshToken", data.get("refresh_token", account["refresh_token"]))
            if data.get("expiresAt") or data.get("expires_at"):
                account["expires_at"] = data.get("expiresAt", data.get("expires_at"))
            if data.get("domain"):
                account["domain"] = data["domain"]
            refreshed = True
        except Exception as error:
            raise RuntimeError(f"token 校验失败（刷新接口拒绝）：{error}")
    write_cockpit_account(account)
    return {"ok": True, "uid": account["uid"], "nickname": account["nickname"], "source": source, "refreshed": refreshed}


def scan_local_accounts():
    """重新扫描本机 Cockpit 账号库（本地导入）。"""
    accounts = read_cockpit_accounts()
    return {
        "ok": True,
        "count": len(accounts),
        "accounts": [
            {"uid": uid, "email": acc.get("email", ""), "has_token": bool(acc.get("access_token"))}
            for uid, acc in sorted(accounts.items())
        ],
    }


def normalize_totp_secret(secret):
    """兼容 otpauth:// URI 与裸 Base32 密钥。"""
    from urllib.parse import parse_qs

    text = (secret or "").strip()
    if text.lower().startswith("otpauth://"):
        text = parse_qs(urlparse(text).query).get("secret", [""])[0].strip()
    text = text.replace(" ", "").upper()
    if not text:
        raise RuntimeError("未识别到 2FA 密钥")
    return text


def totp_code(secret, at=None):
    """RFC 6238 TOTP（SHA1/30s/6 位），仅用标准库实现。"""
    normalized = normalize_totp_secret(secret)
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    counter = int((at if at is not None else time.time()) // TOTP_STEP_SECONDS)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_running_account():
    """从 WorkBuddy 当前运行进程的诊断参数读取已认证账号。"""
    import re
    import subprocess as sp
    try:
        result = sp.run(["ps", "ax", "-o", "command="], capture_output=True, text=True, timeout=10)
        accounts = [
            match.group(1)
            for line in result.stdout.splitlines()
            if "WorkBuddy.app" in line and "chrome_crashpad_handler" in line
            for match in [re.search(r'"uid":"([0-9a-f-]{36})"', line)]
            if match
        ]
        return accounts[-1] if accounts else None
    except Exception:
        return None


def get_service_status():
    """返回当前本地服务进程的 RSS 内存；ps 的 rss 单位为 KiB。"""
    import subprocess as sp
    rss_kb = 0
    try:
        result = sp.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5)
        rss_kb = int(result.stdout.strip() or 0)
    except Exception:
        pass
    return {"pid": os.getpid(), "rss_mb": round(rss_kb / 1024, 1), "uptime_seconds": int(time.time() - STARTED_AT)}


def script_restart_argv():
    return [sys.executable, *sys.argv]


def restart_script():
    """用相同解释器替换当前服务进程，桌面壳无需额外守护进程。"""
    log("重启后台脚本...")
    os.execv(sys.executable, script_restart_argv())


def wait_for_running_account(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        account = get_running_account()
        if account:
            return account
        time.sleep(0.25)
    return None


def get_current_account():
    """优先返回 WorkBuddy 实际运行中的认证账号；关闭时才回退到最近会话。"""
    running = get_running_account()
    if running:
        return running
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
    """聚合 Cockpit 授权账号与本地会话数。"""
    current = get_current_account()
    export = load_accounts()
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
        quota = quota_groups(info)
        base = quota["base"]
        activity = quota["activity"]
        categories = [
            {"key": key, "label": label, **group}
            for key, label, group in (
                ("base", "基础体验包", base),
                ("activity", "活动赠送包", activity),
                ("extra", "加量包", quota["extra"]),
                ("other", "其他", quota["other"]),
            )
            if group["total"] > 0 or group["used"] > 0
        ]
        items.append({
            "user_id": uid,
            "nickname": info.get("nickname", uid[:8]),
            "sessions": info.get("sessions", 0),
            "is_current": uid == current,
            "payment_type": info.get("payment_type", "unknown"),
            "base": base,
            "gift": activity,
            "categories": categories,
            "total": {
                "used": sum(group["used"] for group in quota.values()),
                "total": sum(group["total"] for group in quota.values()),
                "unit": "credits",
            },
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


def build_auth_session(account):
    """按 Cockpit 的默认客户端格式，从授权账号生成完整 WorkBuddy 会话。"""
    uid = account.get("uid")
    access_token = account.get("access_token")
    if not uid or not access_token:
        return None
    raw = account.get("auth_raw") if isinstance(account.get("auth_raw"), dict) else {}
    profile = account.get("profile_raw") if isinstance(account.get("profile_raw"), dict) else raw.get("account", {})
    account_value = json.loads(json.dumps(profile)) if isinstance(profile, dict) else {}
    account_value.update({"uid": uid, "nickname": account.get("nickname") or uid[:8], "lastLogin": True, "pluginEnabled": True})
    account_value.setdefault("type", "personal")
    account_value.setdefault("accountType", "")
    account_value.setdefault("idp", "")
    account_value.setdefault("oneidAccountId", "")
    account_value.setdefault("areaInfoComplete", False)
    account_value.setdefault("isCurrentOneIdEnterprise", False)
    account_value.setdefault("isFirstLogin", False)
    account_value.setdefault("deployStatus", {"statusCode": 0, "statusMsg": "", "detailMsg": ""})
    account_value.setdefault("sso", {"domain": "", "domainModifiedTimes": 0})

    raw_auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else raw
    auth = json.loads(json.dumps(raw_auth)) if isinstance(raw_auth, dict) else {}
    auth.update({
        "accessToken": access_token,
        "refreshToken": account.get("refresh_token") or "",
        "tokenType": account.get("token_type") or "Bearer",
        "domain": account.get("domain") or "",
        "lastRefreshTime": int(time.time() * 1000),
    })
    expires_at = account.get("expires_at")
    if expires_at is not None:
        auth["expiresAt"] = expires_at
        auth["expiresIn"] = max(0, (int(expires_at) - int(time.time() * 1000)) // 1000)
        refresh_expires_at = auth.get("refreshExpiresAt", expires_at)
        auth["refreshExpiresAt"] = refresh_expires_at
        auth["refreshExpiresIn"] = max(0, (int(refresh_expires_at) - int(time.time() * 1000)) // 1000)
    else:
        auth.setdefault("expiresIn", 0)
        auth.setdefault("refreshExpiresIn", 0)
    auth.setdefault("scope", "openid profile offline_access email")
    return {"account": account_value, "auth": auth, "accounts": [account_value]}


def do_switch(target_uid):
    """原子替换 WorkBuddy 主进程读取的完整认证会话。"""
    account = load_accounts().get(target_uid)
    session = build_auth_session(account or {})
    if not session or session["account"].get("uid") != target_uid:
        return {"ok": False, "error": "该账号缺少可用的完整认证会话，请重新导出账号后再试"}
    if not os.path.exists(AUTH_SESSION_PATH):
        return {"ok": False, "error": f"找不到 WorkBuddy 认证文件：{AUTH_SESSION_PATH}"}
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"{AUTH_SESSION_PATH}.bak.{ts}"
    temporary = f"{AUTH_SESSION_PATH}.{os.getpid()}.tmp"
    try:
        shutil.copy2(AUTH_SESSION_PATH, backup)
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, AUTH_SESSION_PATH)
        return {"ok": True, "backup": os.path.basename(backup)}
    except Exception as e:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
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


def kill_all_workbuddy():
    """杀掉所有 WorkBuddy 进程（主 + helper），循环直到全死。
    返回 True 表示全死。"""
    import subprocess as sp
    for _ in range(10):
        sp.run(["pkill", "-9", "-f", "WorkBuddy.app"], capture_output=True, timeout=10)
        time.sleep(1)
        r = sp.run(["pgrep", "-f", "WorkBuddy.app"], capture_output=True)
        if r.returncode != 0:
            return True
    return False


def quit_workbuddy():
    """退出 WorkBuddy（释放 leveldb 锁）。先优雅 quit，再循环强杀全部进程。"""
    import subprocess as sp
    try:
        sp.run(["osascript", "-e", 'quit app "WorkBuddy"'], capture_output=True, timeout=10)
    except Exception:
        pass
    for _ in range(8):
        if not is_workbuddy_running():
            return True
        time.sleep(1)
    return kill_all_workbuddy()


def workbuddy_launch_command(hidden=False):
    return ["open", *(["-gj"] if hidden else []), "-a", "WorkBuddy"]


def start_workbuddy(hidden=False):
    import subprocess as sp
    try:
        sp.run(workbuddy_launch_command(hidden), capture_output=True, timeout=15)
        return True
    except Exception:
        return False


def do_switch_full(target_uid):
    """切换真实登录身份，确认成功后才把会话过户到同一账号。"""
    log(f"  [0/6] 按 Cockpit 规则刷新目标账号 token...")
    try:
        refresh_cockpit_account(target_uid)
    except Exception as error:
        return {"ok": False, "error": f"目标账号刷新失败：{error}"}
    log(f"  [1/6] 退出 WorkBuddy...")
    quit_workbuddy()
    log(f"  [2/6] 写入完整认证会话...")
    sw = do_switch(target_uid)
    if not sw.get("ok"):
        start_workbuddy()  # 失败也要恢复 WorkBuddy
        return sw
    log(f"  [3/6] 后台启动 WorkBuddy 并校验登录账号...")
    if not start_workbuddy(hidden=True):
        return {"ok": False, "error": "无法启动 WorkBuddy，未执行会话同步"}
    actual_uid = wait_for_running_account()
    if actual_uid != target_uid:
        return {
            "ok": False,
            "error": f"登录校验失败：实际账号为 {actual_uid or '未检测到'}，未执行会话同步",
            "auth_backup": sw.get("backup", ""),
        }

    log(f"  [4/6] 已确认登录账号，退出 WorkBuddy 以同步会话...")
    quit_workbuddy()
    log(f"  [5/6] 过户会话...")
    sy = do_sync(target_uid)
    log(f"  [6/6] 启动 WorkBuddy...")
    started = start_workbuddy()
    return {
        "ok": True,
        "switch_detail": sw.get("detail", ""),
        "moved_sessions": sy.get("moved_sessions", 0),
        "total_now": sy.get("total_now", 0),
        "backup": sy.get("backup", ""),
        "auth_backup": sw.get("backup", ""),
        "restarted": started,
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 账号坞</title>
<style>
  :root {
    --bg: #1a1a1a; --surface: #242424; --surface2: #2e2e2e;
    --border: #3a3a3a; --text: #e8e8e8; --text2: #999; --text3: #666;
    --accent: #4a9eff; --accent2: #2d7dd2; --green: #4caf50; --warn: #ff9800;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 14px; line-height: 1.6; padding: 0; margin: 0; min-width: 0; }
  .app-shell { display: flex; min-height: 100vh; }
  .sidebar { width: 196px; flex-shrink: 0; background: #1f1f23; border-right: 1px solid #2c2c31; padding: 16px 10px; display: flex; flex-direction: column; gap: 4px; position: sticky; top: 0; height: 100vh; overflow-y: auto; }
  .logo-row { display: flex; align-items: center; gap: 9px; padding: 2px 8px 16px; }
  .logo-dot { width: 22px; height: 22px; border-radius: 7px; background: var(--accent); flex-shrink: 0; }
  .logo-text { font-size: 14px; font-weight: 600; }
  .nav-section { font-size: 11px; color: var(--text3); padding: 10px 8px 4px; letter-spacing: 0.5px; }
  .sidebar .service-tools { display: flex; flex-direction: column; gap: 6px; padding: 4px 6px; align-items: stretch; }
  .sidebar .service-status { white-space: normal; }
  .main { flex: 1; min-width: 0; padding: 22px 26px 40px; overflow-y: auto; }
  .page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
  .page-head h1 { font-size: 20px; font-weight: 600; }
  .page-sub { color: var(--text2); font-size: 12px; margin-top: 3px; }
  .service-status { color: var(--green); font-size: 11px; }
  .service-btn { background: transparent; border: 1px solid var(--border); color: var(--text2); padding: 3px 7px; border-radius: 5px; font-size: 11px; cursor: pointer; }
  .service-btn:hover { border-color: var(--accent); color: var(--accent); }
  .service-btn.restart:hover { border-color: var(--warn); color: var(--warn); }
  .sub { color: var(--text2); font-size: 13px; margin-bottom: 24px; }
  .platform-tabs { display: flex; flex-direction: column; gap: 2px; }
  .platform-tab { background: transparent; border: none; border-left: 2px solid transparent; color: var(--text2); padding: 8px 10px; border-radius: 7px; font-size: 13px; cursor: pointer; text-align: left; }
  .platform-tab:hover { background: var(--surface2); color: var(--text); }
  .platform-tab.active { background: #2b3a4a; border-left-color: var(--accent); color: var(--text); }
  .platform-tab .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 7px; }
  .platform-panel { display: none; }
  .platform-panel.active { display: block; }
  .app-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .app-row:last-child { border-bottom: none; }
  .app-state { font-size: 12px; color: var(--text3); }
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
  .auth-card { padding: 14px 16px; }
  .auth-actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .auth-action { flex: 1; min-width: 150px; padding: 10px 12px; background: var(--surface2); border: 1px solid var(--border); color: var(--text); border-radius: 7px; font-size: 13px; text-align: center; cursor: pointer; }
  .auth-action:hover { border-color: var(--accent); color: var(--accent); }
  .auth-link-box { display: flex; gap: 8px; margin-top: 10px; }
  .auth-link-box input { min-width: 0; flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text2); border-radius: 6px; padding: 7px 9px; font-size: 12px; }
  .auth-hint { color: var(--text3); font-size: 11px; margin-top: 6px; }
  .modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.62); display: flex; align-items: center; justify-content: center; z-index: 50; padding: 16px; }
  .modal-mask[hidden] { display: none; }
  .modal-box { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; width: 100%; max-width: 540px; padding: 18px 20px; max-height: 88vh; overflow-y: auto; }
  .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .modal-title { font-size: 16px; font-weight: 600; }
  .modal-close { background: transparent; border: none; color: var(--text2); font-size: 20px; line-height: 1; cursor: pointer; padding: 2px 8px; border-radius: 6px; }
  .modal-close:hover { color: var(--text); background: var(--surface2); }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 14px; flex-wrap: wrap; }
  .tab { background: transparent; border: none; color: var(--text2); padding: 8px 12px; font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .tab-panel .auth-action { width: 100%; }
  .auth-open-row { margin-top: 8px; }
  .auth-status { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; color: var(--text2); }
  .totp-box { margin-top: 16px; border-top: 1px dashed var(--border); padding-top: 12px; }
  .totp-head { color: var(--text3); font-size: 12px; margin-bottom: 8px; }
  .totp-result { display: flex; align-items: baseline; gap: 10px; margin-top: 10px; }
  .totp-code { font-family: "SF Mono", Consolas, monospace; font-size: 26px; letter-spacing: 4px; color: var(--green); }
  .totp-count { color: var(--text3); font-size: 12px; }
  #tab-token textarea { width: 100%; min-height: 90px; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 8px 10px; font-size: 12px; font-family: "SF Mono", Consolas, monospace; resize: vertical; }
  .token-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 10px 0; }
  .token-grid input { min-width: 0; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 7px 9px; font-size: 12px; }
  .log { background: var(--surface2); border-radius: var(--radius); padding: 14px 16px; margin-top: 16px; font-family: "SF Mono", Consolas, monospace; font-size: 12px; color: var(--text2); white-space: pre-wrap; min-height: 20px; max-height: 240px; overflow-y: auto; }
  .log:empty::before { content: "等待操作..."; color: var(--text3); }
  .ok { color: var(--green); } .err { color: #ff5252; } .warn { color: var(--warn); }
  .tip { color: var(--text3); font-size: 12px; margin-top: 20px; line-height: 1.7; }
  .tip b { color: var(--warn); }
  .acc-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; margin-bottom: 0; }
  .acc-card.cur { border-color: var(--green); }
  .acc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .acc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .export-check { width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer; }
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
  .launch-btn { background: var(--accent); color: #fff; border: 1px solid var(--accent); font-size: 12px; padding: 5px 14px; border-radius: 6px; cursor: pointer; font-weight: 500; }
  .launch-btn:hover { background: var(--accent2); }
  .refresh-btn { background: transparent; border: 1px solid var(--border); color: var(--text2); font-size: 11px; padding: 2px 10px; border-radius: 5px; cursor: pointer; margin-left: auto; }
  .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }
  .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .spin { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--text3); border-top-color: var(--accent); border-radius: 50%; animation: sp 0.7s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes sp { to { transform: rotate(360deg); } }
  @media (max-width: 760px) {
    .app-shell { flex-direction: column; }
    .sidebar { width: 100%; height: auto; position: static; border-right: none; border-bottom: 1px solid #2c2c31; flex-direction: row; align-items: center; gap: 8px; padding: 10px 12px; flex-wrap: wrap; }
    .logo-row { padding: 0 4px; }
    .nav-section { display: none; }
    .platform-tabs { flex-direction: row; flex-wrap: wrap; }
    .platform-tab { border-left: none; border: 1px solid var(--border); padding: 5px 12px; font-size: 12px; }
    .platform-tab.active { border-color: var(--accent); }
    .sidebar .service-tools { flex-direction: row; align-items: center; }
    .main { padding: 16px 14px 32px; }
  }
  @media (max-width: 520px) {
    .card, .acc-card { padding: 14px; }
    .acc-grid { grid-template-columns: 1fr; }
    .auth-actions { display: grid; grid-template-columns: 1fr; }
    .auth-action { min-width: 0; width: 100%; }
    .auth-link-box { flex-wrap: wrap; }
    .auth-link-box .auth-action { flex: 0 0 auto; width: auto; }
    .pkg-row { align-items: flex-start; flex-direction: column; gap: 1px; }
    .acc-head .refresh-btn { margin-left: 0; }
    .acc-foot { align-items: stretch; flex-direction: column; }
    .acc-actions, .switch-btn, .launch-btn { width: 100%; }
  }
</style>
</head>
<body>
<div class="app-shell">
  <aside class="sidebar">
    <div class="logo-row"><span class="logo-dot"></span><span class="logo-text">AI 账号坞</span></div>
    <div class="nav-section">平台</div>
    <nav class="platform-tabs" id="platformTabs"></nav>
    <div class="nav-section">服务</div>
    <div class="service-tools"><span class="service-status" id="serviceStatus">后台脚本检测中…</span><button class="service-btn" id="refreshStatusBtn">刷新状态</button><button class="service-btn restart" id="restartScriptBtn">重启脚本</button></div>
  </aside>
  <main class="main">
    <div class="page-head">
      <div><h1 id="pageTitle">WorkBuddy</h1><div class="page-sub" id="pageSub">账号与授权管理 · Cockpit 兼容账号库</div></div>
    </div>
    <div class="platform-panel active" id="platform-panel-workbuddy">
    <div class="card auth-card">
      <div class="label">账号授权</div>
      <div class="auth-actions">
        <button class="auth-action" id="addAccountBtn">添加 WorkBuddy 账号</button>
        <button class="auth-action" id="exportBtn" disabled>导出所选账号 JSON</button>
      </div>
    </div>

  <div class="modal-mask" id="addAccountModal" hidden>
    <div class="modal-box" role="dialog" aria-label="添加 WorkBuddy 账号">
      <div class="modal-head"><span class="modal-title">添加 WorkBuddy 账号</span><button class="modal-close" id="closeAccountModal" aria-label="关闭">×</button></div>
      <div class="tabs">
        <button class="tab active" data-tab="oauth">OAuth 授权</button>
        <button class="tab" data-tab="token">Token</button>
        <button class="tab" data-tab="json">JSON</button>
        <button class="tab" data-tab="local">本地导入</button>
      </div>
      <div class="tab-panel active" id="tab-oauth">
        <button class="auth-action" id="authLinkBtn">生成授权链接</button>
        <div id="authLinkBox" hidden>
          <div class="auth-link-box"><input id="authLink" readonly><button class="auth-action" id="copyLinkBtn">复制</button></div>
          <div class="auth-open-row"><button class="auth-action" id="openLinkBtn">在浏览器中打开</button></div>
          <div class="auth-status"><span class="spin"></span><span id="authPollText">等待授权完成…</span></div>
          <div class="auth-hint" id="authMeta"></div>
        </div>
        <div class="totp-box">
          <div class="totp-head">2FA 验证码工具（本地计算，密钥不上传）</div>
          <div class="auth-link-box"><input id="totpSecret" placeholder="粘贴 Base32 密钥或 otpauth:// 链接"><button class="auth-action" id="totpBtn">生成</button></div>
          <div class="totp-result" id="totpResult" hidden><span class="totp-code" id="totpCode"></span><span class="totp-count" id="totpCount"></span></div>
        </div>
      </div>
      <div class="tab-panel" id="tab-token">
        <textarea id="tokenRaw" placeholder="粘贴 accessToken，或整段账号 JSON"></textarea>
        <div class="token-grid">
          <input id="tokenUid" placeholder="账号 UID（纯 token 必填）">
          <input id="tokenRefresh" placeholder="refresh_token（可选，用于校验）">
        </div>
        <button class="auth-action" id="tokenImportBtn">校验并导入</button>
        <div class="auth-hint">提供 refresh_token 时先经官方刷新接口校验，通过才写入账号库；JSON 粘贴可自动带出 uid。</div>
      </div>
      <div class="tab-panel" id="tab-json">
        <label class="auth-action" for="importFile">选择 Cockpit 账号 JSON 文件</label>
        <input id="importFile" type="file" accept="application/json,.json" hidden>
        <div class="auth-hint">支持账号对象、账号数组或 accounts/items 包装格式，可批量导入。</div>
      </div>
      <div class="tab-panel" id="tab-local">
        <button class="auth-action" id="scanLocalBtn">重新扫描本机 Cockpit 账号库</button>
        <div class="auth-hint" id="scanLocalResult">读取 ~/.antigravity_cockpit/workbuddy_accounts/ 下的加密账号文件。</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="label">当前 WorkBuddy 登录账号</div>
    <div class="current-uid" id="current">检测中...</div>
  </div>

  <div class="label" style="margin-bottom:12px">账号列表 · 每张卡一个主操作</div>
  <div id="dist" class="acc-grid"><span class="spin"></span>加载中</div>

  <div class="log" id="log"></div>

  <div class="tip">
    <b>使用方法</b><br>
    点击目标账号的“切换并同步”：工具会切换登录、确认实际账号、归并会话并重启 WorkBuddy。<br>
    当前登录账号可点击“启动 WorkBuddy”。每次会话同步自动备份 workbuddy.db。
  </div>
  </div>
  <div class="platform-panel" id="platform-panel-dynamic"><span class="spin"></span>加载平台数据…</div>
  </main>
</div>

<script>
const PLATFORM_COLORS = {workbuddy: 'var(--green)', trae: 'var(--warn)', qwen: '#a88ff2'};
const PLATFORM_ORDER = ['workbuddy', 'trae', 'qwen'];
let platformData = {}, activePlatform = localStorage.getItem('activePlatform') || 'workbuddy';

function renderPlatformTabs() {
  const tabs = PLATFORM_ORDER.map(id => {
    const info = platformData[id];
    const label = info ? (info.name || id) : id;
    const dot = info ? `<span class="dot" style="background:${PLATFORM_COLORS[id] || 'var(--text3)'}"></span>` : '';
    const cls = 'platform-tab' + (activePlatform === id ? ' active' : '');
    return `<button class="${cls}" data-platform="${id}">${dot}${label}</button>`;
  }).join('');
  $('platformTabs').innerHTML = tabs;
}

function switchPlatform(id) {
  activePlatform = id;
  localStorage.setItem('activePlatform', id);
  document.querySelectorAll('.platform-panel').forEach(p => p.classList.remove('active'));
  const panel = $('platform-panel-' + id) || $('platform-panel-dynamic');
  panel.classList.add('active');
  const info = platformData[id];
  $('pageTitle').textContent = info ? (info.name || id) : id;
  $('pageSub').textContent = id === 'workbuddy' ? '账号与授权管理 · Cockpit 兼容账号库' : (info && info.accounts ? `${info.accounts.length} 个账号 · 平台适配器` : '平台适配器');
  renderPlatformTabs();
  if (id !== 'workbuddy') renderDynamicPlatform(id);
}

async function loadPlatforms() {
  try {
    const d = await (await fetch('/api/platforms')).json();
    if (!d.ok) throw new Error(d.error);
    platformData = {};
    for (const p of d.platforms) platformData[p.id] = p;
    platformData.workbuddy = platformData.workbuddy || {id: 'workbuddy', name: 'WorkBuddy'};
    switchPlatform(activePlatform);
  } catch (e) { appendLog('平台列表加载失败: ' + e.message, 'err'); }
}

function featureBadge(f) {
  if (f === true) return '<span class="ok">✓</span>';
  if (f === 'planned' || f === 'capture') return '';
  return f ? '' : '<span class="err">—</span>';
}

function renderDynamicPlatform(id) {
  const p = platformData[id];
  if (!p) return;
  let html = `<div class="card"><div class="label">${p.name} · 应用状态</div>`;
  for (const [key, app] of Object.entries(p.apps || {})) {
    const cur = app.current && app.current.username ? app.current.username : '未登录';
    html += `<div class="app-row"><div><b>${app.title}</b><div class="app-state">${app.installed ? '已安装' : '未安装'} · ${app.running ? '运行中' : '未运行'} · 当前: ${cur}</div></div></div>`;
  }
  html += '</div>';
  if ((p.accounts || []).length) {
    html += `<div class="label" style="margin-bottom:12px">${p.name} · 账号列表</div><div class="acc-grid">`;
    for (const a of p.accounts) {
      html += `<div class="acc-card"><div class="acc-head"><span class="acc-nick">${a.username || a.user_id}</span>${a.has_token ? '<span class="badge-cur">有 token</span>' : ''}</div>`;
      html += `<div class="acc-foot"><span class="app-state">${a.email || ''}</span><div class="acc-actions">`;
      const appKey = Object.keys(p.apps || {})[0] || 'solo_cn';
      if (p.features && p.features.switch) html += `<button class="switch-btn pf-switch" data-platform="${id}" data-id="${a.id}" data-app="${appKey}">切换到该账号</button>`;
      html += '</div></div></div>';
    }
    html += '</div>';
  } else {
    html += `<div class="card"><div class="label">暂无账号</div><div class="app-state">${(p.features && p.features.note) || '在对应应用登录后，点击下方按钮提取账号。'}</div></div>`;
  }
  html += `<div class="card auth-card"><div class="label">账号操作</div><div class="auth-actions">`;
  if (p.features && (p.features.auth === 'capture')) {
    const appKeys = Object.keys(p.apps || {});
    for (const k of appKeys) html += `<button class="auth-action pf-capture" data-platform="${id}" data-app="${k}">提取 ${(p.apps[k].title || k).split('（')[0]} 当前登录</button>`;
  }
  if (p.features && p.features.note) html += `<div class="auth-hint" style="flex-basis:100%">${p.features.note}</div>`;
  html += '</div></div>';
  $('platform-panel-dynamic').innerHTML = html;
}

async function pfCapture(platform, app) {
  appendLog(`正在提取 ${platform}:${app} 登录账号…`);
  try {
    const d = await (await fetch(`/api/platform/${platform}/capture`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({app})})).json();
    if (!d.ok) throw new Error(d.error);
    appendLog(`提取成功: ${d.account.username}`, 'ok');
    loadPlatforms();
  } catch (e) { appendLog('提取失败: ' + e.message, 'err'); }
}

async function pfSwitch(platform, id, app) {
  if (!confirm(`确认切换到该账号？会退出并重启对应应用。`)) return;
  appendLog('正在切换…（退出→注入→重启→校验，约 15 秒）');
  try {
    const d = await (await fetch(`/api/platform/${platform}/switch`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({account_id: id, app})})).json();
    if (!d.ok) throw new Error(d.error);
    appendLog(`切换完成: ${d.username}${d.verified ? '（已验证登录）' : '（校验未确认，应用可能仍在启动）'}`, d.verified ? 'ok' : 'warn');
    loadPlatforms();
  } catch (e) { appendLog('切换失败: ' + e.message, 'err'); }
}

document.addEventListener('click', e => {
  const tab = e.target.closest('.platform-tab');
  if (tab) { switchPlatform(tab.dataset.platform); return; }
  const cap = e.target.closest('.pf-capture');
  if (cap) { pfCapture(cap.dataset.platform, cap.dataset.app); return; }
  const sw = e.target.closest('.pf-switch');
  if (sw && !sw.disabled) { pfSwitch(sw.dataset.platform, sw.dataset.id, sw.dataset.app); return; }
});

const $ = id => document.getElementById(id);
const selectedForExport = new Set();
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
    $('serviceStatus').textContent = '后台脚本运行中 · ' + d.service.rss_mb + ' MB · PID ' + d.service.pid;
    if (!d.current) {
      $('current').textContent = '未检测到（请先用 cockpit 登录任意账号）';
      $('current').style.color = 'var(--warn)';
      return;
    }
    $('current').textContent = d.current + (d.current_verified ? '' : '（WorkBuddy 未运行，数据库推断）');
    let html = '';
    for (const a of d.accounts) {
      html += '<div class="acc-card' + (a.is_current ? ' cur' : '') + '">';
      html += '<div class="acc-head"><input class="export-check" type="checkbox" aria-label="选择 ' + a.nickname + ' 导出" data-uid="' + a.user_id + '" ' + (selectedForExport.has(a.user_id) ? 'checked' : '') + '><span class="acc-nick">' + a.nickname + '</span>';
      html += '<span class="badge-free">' + (a.payment_type||'free').toUpperCase() + '</span>';
      if (a.is_current) html += '<span class="badge-cur">当前登录</span>';
      html += '<button class="refresh-btn" data-uid="' + a.user_id + '">刷新</button></div>';
      for (const q of a.categories || []) {
        const pct = q.total > 0 ? Math.min(100, Math.round(q.used / q.total * 100)) : 0;
        html += '<div class="pkg"><div class="pkg-row"><span class="pkg-name">' + q.label + '</span><span class="pkg-val">' + formatQuota(q.used) + ' / ' + formatQuota(q.total) + ' credits</span></div>';
        html += '<div class="quota-bar"><div class="quota-fill' + (pct >= 80 ? ' warn' : '') + '" style="width:' + pct + '%"></div></div></div>';
      }
      html += '<div class="acc-foot"><span>' + a.sessions + ' 会话</span>';
      html += '<div class="acc-actions">';
      if (a.is_current) {
        html += '<button class="launch-btn" data-uid="' + a.user_id + '">' + (d.workbuddy_running ? '打开 WorkBuddy' : '启动 WorkBuddy') + '</button>';
      } else {
        html += '<button class="switch-btn" data-uid="' + a.user_id + '" ' + (!a.has_token ? 'disabled' : '') + '>' + (a.has_token ? '切换并同步' : '无 token') + '</button>';
      }
      html += '</div></div></div>';
    }
    $('dist').innerHTML = html;
    updateExportButton();
  } catch (e) {
    $('dist').innerHTML = '<span class="err">加载失败: ' + e.message + '</span>';
  }
}

function formatQuota(value) {
  return Number(value || 0).toLocaleString('zh-CN', {maximumFractionDigits: 2});
}

async function refreshQuota(uid) {
  const btn = document.querySelector('.refresh-btn[data-uid="'+uid+'"]');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  appendLog('按 Cockpit 规则刷新额度 ' + uid.slice(0,8) + '…');
  try {
    const r = await fetch('/api/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({uid})});
    const d = await r.json();
    if (!d.ok) throw new Error(d.error);
    appendLog('额度已按 Cockpit 资源包刷新', 'ok');
    loadStatus();
  } catch (e) { appendLog('额度刷新失败: ' + e.message, 'err'); }
  finally { if (btn) { btn.disabled = false; btn.textContent = '刷新'; } }
}

let authorizationTimer, authCountdownTimer, authDeadline, currentLoginId;
function stopAuthTimers() {
  clearInterval(authorizationTimer);
  clearInterval(authCountdownTimer);
}
function openAccountModal() { $('addAccountModal').hidden = false; }
function closeAccountModal() {
  $('addAccountModal').hidden = true;
  stopAuthTimers();
  clearInterval(totpTimer);
}
async function authorizeAccount() {
  const button = $('authLinkBtn');
  button.disabled = true; button.textContent = '生成中…';
  try {
    const start = await (await fetch('/api/auth/start', {method:'POST'})).json();
    if (!start.ok) throw new Error(start.error);
    currentLoginId = start.login_id;
    $('authLink').value = start.url;
    $('authLinkBox').hidden = false;
    $('authMeta').textContent = `授权有效期 ${start.ttl}s · 轮询间隔 ${start.poll_interval}s`;
    $('authPollText').textContent = '等待授权完成…';
    appendLog('授权链接已生成，请在浏览器完成登录…');
    stopAuthTimers();
    authDeadline = Date.now() + start.ttl * 1000;
    authCountdownTimer = setInterval(() => {
      const left = Math.max(0, Math.round((authDeadline - Date.now()) / 1000));
      $('authPollText').textContent = `等待授权完成…（剩余 ${left}s）`;
      if (left <= 0) stopAuthTimers();
    }, 1000);
    authorizationTimer = setInterval(async () => {
      const result = await (await fetch('/api/auth/poll', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({login_id:start.login_id})})).json();
      if (result.pending) return;
      stopAuthTimers();
      if (!result.ok) { appendLog('授权失败: ' + result.error, 'err'); $('authPollText').textContent = '授权失败：' + result.error; }
      else { appendLog('授权成功: ' + result.nickname, 'ok'); closeAccountModal(); loadStatus(); }
      button.disabled = false; button.textContent = '生成授权链接';
    }, (start.poll_interval || 2) * 1000);
  } catch (e) { appendLog('授权启动失败: ' + e.message, 'err'); button.disabled = false; button.textContent = '生成授权链接'; }
}

async function importAccounts(file) {
  if (!file) return;
  appendLog('正在导入 Cockpit JSON…');
  try {
    const r = await fetch('/api/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:await file.text()});
    const d = await r.json();
    if (!d.ok) throw new Error(d.error);
    appendLog('已导入 ' + d.accounts.length + ' 个账号', 'ok');
    loadStatus();
  } catch (e) { appendLog('导入失败: ' + e.message, 'err'); }
  finally { $('importFile').value = ''; }
}

function updateExportButton() {
  const button = $('exportBtn');
  button.disabled = selectedForExport.size === 0;
  button.textContent = selectedForExport.size ? '导出所选账号 JSON (' + selectedForExport.size + ')' : '导出所选账号 JSON';
}

async function exportSelectedAccounts() {
  if (!selectedForExport.size) return;
  try {
    const r = await fetch('/api/export', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({uids:[...selectedForExport]})});
    if (!r.ok) { const d = await r.json(); throw new Error(d.error); }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await r.blob());
    link.download = 'workbuddy_accounts.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    appendLog('已导出 ' + selectedForExport.size + ' 个账号 JSON', 'ok');
  } catch (e) { appendLog('导出失败: ' + e.message, 'err'); }
}

async function launchWorkBuddy() {
  appendLog('正在启动 WorkBuddy…');
  try {
    const d = await (await fetch('/api/launch', {method:'POST'})).json();
    if (!d.ok) throw new Error('无法启动 WorkBuddy');
    appendLog('已发送启动请求', 'ok');
    setTimeout(loadStatus, 1000);
  } catch (e) { appendLog('启动失败: ' + e.message, 'err'); }
}

async function restartScript() {
  if (!confirm('确认重启后台脚本？服务会短暂不可用，然后自动恢复。')) return;
  const button = $('restartScriptBtn');
  button.disabled = true;
  try {
    const d = await (await fetch('/api/restart', {method:'POST'})).json();
    if (!d.ok) throw new Error(d.error);
    appendLog('后台脚本正在重启…', 'ok');
    setTimeout(loadStatus, 1200);
  } catch (e) { appendLog('重启失败: ' + e.message, 'err'); button.disabled = false; }
}

let switching = false;
async function switchAccount(uid) {
  if (switching || !confirm('确认切换并同步到该账号？\\n工具会验证实际登录账号后再迁移会话。')) return;
  switching = true;
  document.querySelectorAll('.switch-btn').forEach(btn => btn.disabled = true);
  $('log').innerHTML = '';
  appendLog('切换并同步到 ' + uid.slice(0,8) + '…');
  try {
    const r = await fetch('/api/switch', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({uid}) });
    const d = await r.json();
    if (d.ok) {
      appendLog('登录已确认: ' + d.switch_detail, 'ok');
      appendLog('会话已过户: ' + d.moved_sessions + ' 条', 'ok');
      appendLog('该账号现有会话: ' + d.total_now + ' 条', 'ok');
      appendLog('WorkBuddy 已重启，登录和会话已就绪', 'ok');
    } else {
      appendLog('失败: ' + d.error, 'err');
    }
  } catch (e) {
    appendLog('错误: ' + e.message, 'err');
  } finally {
    switching = false;
    loadStatus();
  }
}

document.addEventListener('click', e => {
  const sw = e.target.closest('.switch-btn:not(.pf-switch)');
  if (sw && !sw.disabled) { switchAccount(sw.dataset.uid); return; }
  const launch = e.target.closest('.launch-btn');
  if (launch) { launchWorkBuddy(); return; }
  const rf = e.target.closest('.refresh-btn');
  if (rf && !rf.disabled) { refreshQuota(rf.dataset.uid); return; }
});
document.addEventListener('change', e => {
  const checkbox = e.target.closest('.export-check');
  if (!checkbox) return;
  checkbox.checked ? selectedForExport.add(checkbox.dataset.uid) : selectedForExport.delete(checkbox.dataset.uid);
  updateExportButton();
});
document.getElementById('authLinkBtn').addEventListener('click', authorizeAccount);
document.getElementById('refreshStatusBtn').addEventListener('click', loadStatus);
document.getElementById('restartScriptBtn').addEventListener('click', restartScript);
document.getElementById('importFile').addEventListener('change', e => importAccounts(e.target.files[0]));
document.getElementById('exportBtn').addEventListener('click', exportSelectedAccounts);
document.getElementById('addAccountBtn').addEventListener('click', openAccountModal);
document.getElementById('closeAccountModal').addEventListener('click', closeAccountModal);
$('addAccountModal').addEventListener('click', e => { if (e.target === $('addAccountModal')) closeAccountModal(); });
document.addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + tab.dataset.tab));
});
document.getElementById('copyLinkBtn').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('authLink').value); appendLog('授权链接已复制', 'ok'); }
  catch (e) { $('authLink').select(); appendLog('请手动复制授权链接', 'warn'); }
});
document.getElementById('openLinkBtn').addEventListener('click', async () => {
  try {
    const d = await (await fetch('/api/auth/open', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({login_id: currentLoginId || ''})})).json();
    if (!d.ok) throw new Error(d.error);
    appendLog('已在默认浏览器打开授权页', 'ok');
  } catch (e) { appendLog('打开浏览器失败: ' + e.message, 'err'); }
});
async function importToken() {
  const btn = $('tokenImportBtn'); btn.disabled = true; btn.textContent = '校验中…';
  try {
    const r = await fetch('/api/import/token', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({raw: $('tokenRaw').value, uid: $('tokenUid').value, refresh_token: $('tokenRefresh').value})});
    const d = await r.json();
    if (!d.ok) throw new Error(d.error);
    appendLog('Token 导入成功: ' + d.nickname + (d.refreshed ? '（已通过官方接口校验）' : '（未提供 refresh_token，未校验）'), 'ok');
    $('tokenRaw').value = ''; $('tokenUid').value = ''; $('tokenRefresh').value = '';
    closeAccountModal(); loadStatus();
  } catch (e) { appendLog('Token 导入失败: ' + e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = '校验并导入'; }
}
document.getElementById('tokenImportBtn').addEventListener('click', importToken);
async function scanLocal() {
  const btn = $('scanLocalBtn'); btn.disabled = true; btn.textContent = '扫描中…';
  try {
    const d = await (await fetch('/api/scan-local', {method:'POST'})).json();
    if (!d.ok) throw new Error(d.error);
    const withToken = d.accounts.filter(a => a.has_token).length;
    $('scanLocalResult').textContent = d.count ? `已识别 ${d.count} 个本地账号（含 token ${withToken} 个），账号列表将自动刷新` : '本地账号库为空：请先用 Cockpit 登录过至少一个账号';
    if (d.count) loadStatus();
    appendLog('本地扫描完成：' + d.count + ' 个账号', 'ok');
  } catch (e) { $('scanLocalResult').textContent = '扫描失败: ' + e.message; }
  finally { btn.disabled = false; btn.textContent = '重新扫描本机 Cockpit 账号库'; }
}
document.getElementById('scanLocalBtn').addEventListener('click', scanLocal);
let totpTimer;
async function refreshTotp(secret) {
  try {
    const d = await (await fetch('/api/totp', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({secret})})).json();
    if (!d.ok) throw new Error(d.error);
    $('totpResult').hidden = false;
    $('totpCode').textContent = d.code;
    $('totpCount').textContent = d.remaining + 's 后刷新';
  } catch (e) {
    clearInterval(totpTimer);
    $('totpResult').hidden = true;
    appendLog('2FA 验证码生成失败: ' + e.message, 'err');
  }
}
document.getElementById('totpBtn').addEventListener('click', () => {
  const secret = $('totpSecret').value.trim();
  if (!secret) return;
  clearInterval(totpTimer);
  refreshTotp(secret);
  totpTimer = setInterval(() => {
    if ($('addAccountModal').hidden) { clearInterval(totpTimer); return; }
    refreshTotp(secret);
  }, 1000);
});
loadStatus();
setInterval(loadStatus, 5000);
loadPlatforms();
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _authorized(self):
        return is_request_authorized(self.headers, getattr(self.server, "access_token", None))

    def _require_authorization(self):
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, limit=1024 * 1024):
        """读取请求体 JSON；空体或格式错误返回 None。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if not 0 < length <= limit:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def _html(self):
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download_json(self, content, filename):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._require_authorization():
            return
        path = urlparse(self.path).path
        if path == "/" or path.startswith("/?"):
            self._html()
        elif path == "/api/status":
            runtime = get_running_account()
            current = runtime or get_current_account()
            self._json({
                "current": current,
                "current_verified": bool(runtime),
                "workbuddy_running": is_workbuddy_running(),
                "service": get_service_status(),
                "accounts": get_all_accounts(),
            })
        elif path == "/api/export-current":
            try:
                filename = f"workbuddy_accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                self._download_json(export_current_account_json(), filename)
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/platforms":
            try:
                self._json({"ok": True, "platforms": list_platforms()})
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 500)
        elif path.startswith("/api/platform/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) < 3:
                self._json({"ok": False, "error": "路径格式: /api/platform/<id>/<action>"}, 404)
                return
            _, _, platform_id, action = parts
            try:
                adapter = get_platform(platform_id)
                if action == "status":
                    self._json({"ok": True, **adapter.status()})
                elif action == "sessions":
                    qs = dict(p.split("=", 1) for p in urlparse(self.path).query.split("&") if "=" in p)
                    self._json({"ok": True, **adapter.list_sessions(qs.get("account_id", ""))})
                else:
                    self._json({"ok": False, "error": f"未知动作: {action}"}, 404)
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._require_authorization():
            return
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
            if not uid:
                self._json({"ok": False, "error": "缺少 uid"})
                return
            try:
                self._json(refresh_quota(uid))
            except Exception as error:
                self._json({"ok": False, "error": str(error)})
        elif path == "/api/auth/start":
            try:
                self._json({"ok": True, **start_cockpit_authorization()})
            except Exception as error:
                self._json({"ok": False, "error": str(error)})
        elif path == "/api/auth/open":
            payload = self._read_json()
            self._json(open_authorization_url((payload or {}).get("login_id", "")))
        elif path == "/api/import/token":
            payload = self._read_json()
            try:
                self._json(import_account_from_token(payload or {}))
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/totp":
            payload = self._read_json()
            try:
                now = time.time()
                self._json({
                    "ok": True,
                    "code": totp_code((payload or {}).get("secret", ""), at=now),
                    "remaining": TOTP_STEP_SECONDS - int(now % TOTP_STEP_SECONDS),
                })
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/scan-local":
            try:
                self._json(scan_local_accounts())
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/import":
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 4 * 1024 * 1024:
                self._json({"ok": False, "error": "导入文件必须小于 4 MB"}, 400)
                return
            try:
                imported = import_accounts_from_json(self.rfile.read(length).decode("utf-8"))
                self._json({"ok": True, "accounts": imported})
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/export":
            length = int(self.headers.get("Content-Length", 0))
            try:
                uids = json.loads(self.rfile.read(length)).get("uids", []) if length else []
                filename = f"workbuddy_accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                self._download_json(serialize_accounts_for_export(uids, load_accounts()), filename)
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        elif path == "/api/launch":
            self._json({"ok": start_workbuddy(), "running": is_workbuddy_running()})
        elif path == "/api/restart":
            self._json({"ok": True, "restarting": True})
            threading.Timer(0.25, restart_script).start()
        elif path == "/api/auth/poll":
            length = int(self.headers.get("Content-Length", 0))
            login_id = None
            if length:
                try:
                    login_id = json.loads(self.rfile.read(length)).get("login_id")
                except Exception:
                    pass
            try:
                self._json(poll_cockpit_authorization(login_id or ""))
            except Exception as error:
                self._json({"ok": False, "pending": False, "error": str(error)})
        elif path.startswith("/api/platform/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) < 3:
                self._json({"ok": False, "error": "路径格式: /api/platform/<id>/<action>"}, 404)
                return
            _, _, platform_id, action = parts
            body = self._read_json() or {}
            try:
                adapter = get_platform(platform_id)
                if action == "capture":
                    self._json({"ok": True, "account": adapter.capture(body.get("app", "solo_cn"))})
                elif action == "switch":
                    account_id = body.get("account_id")
                    if not account_id:
                        self._json({"ok": False, "error": "缺少 account_id"}, 400)
                        return
                    self._json(adapter.switch(account_id, body.get("app", "solo_cn")))
                elif action == "launch":
                    adapter.launch(body.get("app", "solo_cn"))
                    self._json({"ok": True})
                elif action == "import":
                    content = body.get("content")
                    if not content:
                        self._json({"ok": False, "error": "缺少 content"}, 400)
                        return
                    self._json({"ok": True, "imported": adapter.import_accounts(content)})
                else:
                    self._json({"ok": False, "error": f"未知动作: {action}"}, 404)
            except Exception as error:
                self._json({"ok": False, "error": str(error)}, 400)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # 静默 HTTP 访问日志


class ReusableHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True


def main():
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        port = int(sys.argv[i + 1])

    lan_mode = "--lan" in sys.argv
    access_token = os.environ.get("WB_LAN_ACCESS_TOKEN", "")
    if lan_mode and not access_token:
        raise SystemExit("--lan 必须设置 WB_LAN_ACCESS_TOKEN，拒绝在局域网裸露账号数据")

    host = "0.0.0.0" if lan_mode else "127.0.0.1"
    server = ReusableHTTPServer((host, port), Handler)
    server.access_token = access_token or None
    url = f"http://{host}:{port}"
    log(f"AI 账号坞已启动: {url}")
    log("按 Ctrl+C 退出")

    # 原生桌面壳托管服务时不额外打开浏览器。
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("已退出")
        server.shutdown()


if __name__ == "__main__":
    main()
