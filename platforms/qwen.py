"""千问办公（QwenWorkCN）平台适配器。

登录态存 ~/Library/Application Support/QwenWorkCN/auth-v2.dat（schemaVersion 2），
Electron safeStorage 加密（macOS：Keychain "QwenWorkCN Safe Storage" 密钥
→ PBKDF2-HMAC-SHA1(pw, 'saltysalt', 1003, 16) → AES-128-CBC，v10 头 3 字节，
IV=16 空格，PKCS7）。明文 JSON 含 token/refreshToken/user。

切换账号 = 退出 QwenWorkCN → 备份 auth-v2.dat → 写入目标账号明文 JSON 重新加密
→ 重启 → 校验登录。账号库存于 ~/.antigravity_cockpit/qwen_accounts/（同一把 key）。
"""

import base64
import glob
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime

PLATFORM_ID = "qwen"
PLATFORM_NAME = "千问办公"

DATA_DIR = os.path.expanduser("~/Library/Application Support/QwenWorkCN")
AUTH_V2 = os.path.join(DATA_DIR, "auth-v2.dat")
APP_PATH = "/Applications/QwenWorkCN.app"
KEYCHAIN_SERVICE = "QwenWorkCN Safe Storage"

COCKPIT_DIR = os.path.expanduser("~/.antigravity_cockpit")
ACCOUNTS_DIR = os.path.join(COCKPIT_DIR, "qwen_accounts")
STORAGE_KEY_PATH = os.path.join(COCKPIT_DIR, "secure-account-storage.key")

BIN = {name: f"/usr/bin/{name}" for name in ("pgrep", "pkill", "osascript", "open")}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [qwen] {msg}", flush=True)


def _derived_key():
    """Keychain 密钥 → Chromium PBKDF2-SHA1 派生 AES-128 key。"""
    pw = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not pw:
        raise RuntimeError(f"Keychain 未找到 {KEYCHAIN_SERVICE}（请先登录一次 QwenWorkCN）")
    return hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, 16)


def _decrypt_v10(data, key):
    """解密 Electron safeStorage v10 信封。"""
    if not data or data[:3] != b"v10":
        raise RuntimeError("不是 v10 加密信封（可能处于 degraded 明文模式）")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    padded = dec.update(data[3:]) + dec.finalize()
    pad = padded[-1] if padded else 0
    if not 1 <= pad <= 16:
        raise RuntimeError("解密结果填充非法（密钥不匹配？）")
    return padded[:-pad]


def _encrypt_v10(plain, key):
    """明文 → Electron safeStorage v10 信封（与 App 写入格式一致）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad = 16 - len(plain) % 16
    padded = plain + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + enc.update(padded) + enc.finalize()


def read_auth():
    """解密 auth-v2.dat，返回账号明文 dict；未登录返回 None。"""
    if not os.path.exists(AUTH_V2):
        return None
    plain = _decrypt_v10(open(AUTH_V2, "rb").read(), _derived_key())
    obj = json.loads(plain)
    return obj if obj.get("token") and obj.get("user", {}).get("id") else None


def write_auth(obj):
    """把账号明文 JSON 重新加密写回 auth-v2.dat（原子写）。"""
    key = _derived_key()
    data = _encrypt_v10(json.dumps(obj, ensure_ascii=False).encode(), key)
    tmp = f"{AUTH_V2}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, AUTH_V2)


# ---------------------------------------------------------------------------
# Cockpit 兼容账号库
# ---------------------------------------------------------------------------

def _cockpit_cipher():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(open(STORAGE_KEY_PATH).read().strip())
    if len(key) != 32:
        raise RuntimeError("Cockpit 密钥长度无效")
    return AESGCM(key)


def read_accounts():
    if not os.path.isdir(ACCOUNTS_DIR):
        return {}
    cipher = _cockpit_cipher()
    result = {}
    for path in glob.glob(os.path.join(ACCOUNTS_DIR, "qwen_*.json")):
        try:
            envelope = json.load(open(path))
            raw = cipher.decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                None,
            )
            account = json.loads(raw)
            if account.get("user_id"):
                result[account["id"]] = account
        except Exception:
            continue
    return result


def write_account(account):
    cipher = _cockpit_cipher()
    now = int(time.time())
    account.setdefault("created_at", now)
    account["last_used"] = now
    account_id = account["id"]
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, json.dumps(account, ensure_ascii=False).encode(), None)
    envelope = {
        "version": 1,
        "kind": "qwen",
        "algorithm": "AES-256-GCM",
        "key_id": "local-secure-account-storage-v1",
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(encrypted).decode(),
        "encrypted_at": now,
    }
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    target = os.path.join(ACCOUNTS_DIR, f"{account_id}.json")
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return account_id


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------

def is_running(app_key="qwen_work"):
    try:
        return subprocess.run([BIN["pgrep"], "-x", "QwenWorkCN"], capture_output=True).returncode == 0
    except Exception:
        return False


def quit_app(app_key="qwen_work"):
    if not is_running():
        return True
    subprocess.run([BIN["osascript"], "-e", 'quit app "QwenWorkCN"'], capture_output=True)
    for _ in range(20):
        time.sleep(0.5)
        if not is_running():
            return True
    subprocess.run([BIN["pkill"], "-x", "QwenWorkCN"], capture_output=True)
    time.sleep(1.5)
    return not is_running()


def launch(app_key="qwen_work"):
    if not os.path.exists(APP_PATH):
        raise RuntimeError(f"未找到应用: {APP_PATH}")
    subprocess.Popen([BIN["open"], "-a", APP_PATH])
    time.sleep(1)
    return True


# ---------------------------------------------------------------------------
# 适配器主接口
# ---------------------------------------------------------------------------

def get_current_login(app_key="qwen_work"):
    auth = read_auth()
    if not auth:
        return None
    user = auth.get("user") or {}
    return {
        "user_id": user.get("id", ""),
        "username": user.get("name") or user.get("username") or user.get("email") or user.get("id", ""),
        "email": user.get("email", ""),
        "token_expired_at": auth.get("expiresAt", ""),
    }


def capture(app_key="qwen_work"):
    """提取当前登录账号写入账号库。"""
    auth = read_auth()
    if not auth:
        raise RuntimeError("QwenWorkCN 未登录或 auth-v2.dat 无法解密（请先在应用内登录）")
    user = auth.get("user") or {}
    user_id = user.get("id")
    if not user_id:
        raise RuntimeError("登录数据缺少 user.id")
    payload = {
        "id": f"qwen_{hashlib.md5(user_id.encode()).hexdigest()}",
        "kind": "qwen",
        "user_id": user_id,
        "username": user.get("name") or user.get("username") or user_id,
        "email": user.get("email", ""),
        "org_name": user.get("orgName", ""),
        "plan_name": user.get("planName", ""),
        "token": auth.get("token", ""),
        "refresh_token": auth.get("refreshToken", ""),
        "token_expired_at": auth.get("expiresAt", ""),
        "auth_payload": auth,  # 完整明文，切换时原样写回
    }
    write_account(payload)
    log(f"提取账号成功: {payload['username']} ({user_id[:12]}…)")
    return {"id": payload["id"], "user_id": user_id, "username": payload["username"], "email": payload["email"]}


def inject(account_id):
    accounts = read_accounts()
    account = accounts.get(account_id)
    if not account:
        raise RuntimeError(f"账号不存在: {account_id}")
    payload = account.get("auth_payload")
    if not payload or not payload.get("token"):
        raise RuntimeError("账号缺少登录凭证，请重新提取")
    if os.path.exists(AUTH_V2):
        backup = f"{AUTH_V2}.harbor-bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(AUTH_V2, backup)
        for old in sorted(glob.glob(f"{AUTH_V2}.harbor-bak-*"))[:-10]:
            try:
                os.remove(old)
            except Exception:
                pass
    write_auth(payload)
    return account.get("username", "")


def switch(account_id, app_key="qwen_work"):
    """完整切换：退出 → 备份+注入 → 重启 → 校验。"""
    if not quit_app():
        return {"ok": False, "error": "QwenWorkCN 未能退出，已中止（未做任何修改）"}
    try:
        username = inject(account_id)
    except Exception as error:
        return {"ok": False, "error": f"注入失败: {error}"}
    try:
        launch()
    except Exception as error:
        return {"ok": False, "error": f"注入成功但启动失败: {error}（可手动启动）", "username": username}
    time.sleep(10)
    current = get_current_login()
    verified = bool(current and current.get("user_id"))
    if verified:
        log(f"切换完成并已验证: {current['username']}")
    return {"ok": True, "username": username, "verified": verified, "current": current}


def import_accounts(content):
    """导入千问账号 JSON（含 auth_payload 的账号数组/对象）。"""
    parsed = json.loads(content) if isinstance(content, str) else content
    values = parsed if isinstance(parsed, list) else [parsed]
    imported = []
    for index, raw in enumerate(values, 1):
        if not isinstance(raw, dict) or not raw.get("user_id"):
            raise RuntimeError(f"第 {index} 条账号缺少 user_id")
        if not raw.get("auth_payload") or not raw["auth_payload"].get("token"):
            raise RuntimeError(f"第 {index} 条账号缺少 auth_payload（需从会话港导出）")
        raw = dict(raw)
        raw.setdefault("id", f"qwen_{hashlib.md5(str(raw['user_id']).encode()).hexdigest()}")
        raw.setdefault("kind", "qwen")
        write_account(raw)
        imported.append({"id": raw["id"], "username": raw.get("username", "")})
    return imported


def status():
    accounts = read_accounts()
    installed = os.path.exists(APP_PATH)
    current = None
    try:
        current = get_current_login()
    except Exception:
        current = None
    return {
        "platform": PLATFORM_ID,
        "name": PLATFORM_NAME,
        "apps": {
            "qwen_work": {
                "title": "QwenWorkCN（千问办公）",
                "running": is_running(),
                "installed": installed,
                "current": current,
            }
        },
        "accounts": [
            {
                "id": acc["id"],
                "user_id": acc.get("user_id"),
                "username": acc.get("username") or acc.get("user_id"),
                "email": acc.get("email", ""),
                "has_token": bool(acc.get("token")),
                "token_expired_at": acc.get("token_expired_at", ""),
            }
            for acc in accounts.values()
        ],
        "features": {
            "auth": "capture",
            "switch": True,
            "sessions_cloud": False,
            "sessions_local": False,
            "note": "登录态破解完成（safeStorage PBKDF2-SHA1）。会话浏览待研究云端 API。",
        },
    }
