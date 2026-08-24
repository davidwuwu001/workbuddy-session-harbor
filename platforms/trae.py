"""Trae 平台适配器：账号提取 / 切换 / 云端会话。

从 trae_sycn 项目移植核心能力，账号库与 Cockpit 完全互认
（~/.antigravity_cockpit/trae_work_accounts/，同一把 secure-account-storage.key）。
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
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

COCKPIT_DIR = os.path.expanduser("~/.antigravity_cockpit")
ACCOUNTS_DIR = os.path.join(COCKPIT_DIR, "trae_work_accounts")
KEY_PATH = os.path.join(COCKPIT_DIR, "secure-account-storage.key")
INDEX_PATH = os.path.join(COCKPIT_DIR, "trae_work_accounts.json")

APPS = {
    "solo_cn": {
        "name": "TRAE SOLO CN",
        "title": "TraeWork（AI 工作台）",
        "storage": "~/Library/Application Support/TRAE SOLO CN/User/globalStorage/storage.json",
        "process": "TRAE SOLO CN",
        "app_path": "/Applications/TRAE SOLO CN.app",
    },
    "trae_cn": {
        "name": "Trae CN",
        "title": "TraeCode（IDE）",
        "storage": "~/Library/Application Support/Trae CN/User/globalStorage/storage.json",
        "process": "Trae CN",
        "app_path": "/Applications/Trae CN.app",
    },
}

API_GATEWAYS = [
    "https://trae-api-cn.mchost.guru",
    "https://work.enterprise.trae.cn",
]
GATEWAY_CACHE = {}

KEY_AUTH = "iCubeAuthInfo://icube.cloudide"
KEY_SERVER = "iCubeServerData://icube.cloudide"
KEY_HOST = "iCubeHostInfo"
KEY_USERTAG = "iCubeAuthInfo://usertag"

PREFIX_AES = bytes([116, 99, 5, 16, 0, 0])
AES_A = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251, 124, 227,
               57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203, 84, 123, 148, 50,
               166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78, 8, 46, 161, 102, 40, 217, 36,
               178, 118, 91, 162, 73, 109, 139, 209, 37])
AES_B = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95, 96, 81, 127,
               169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239, 160, 224, 59, 77, 174,
               42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97, 23, 43, 4, 126, 186, 119, 214,
               38, 225, 105, 20, 99, 85, 33, 12, 125])
SALT_AES = bytes(a ^ b for a, b in zip(AES_A, AES_B))

BIN = {name: f"/usr/bin/{name}" for name in ("pgrep", "pkill", "osascript", "open")}

PLATFORM_ID = "trae"
PLATFORM_NAME = "Trae Work"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [trae] {msg}", flush=True)


def byte_crypto_decrypt(raw):
    """解密 storage.json 的 iCube 信封（AES-128-CBC）。"""
    if len(raw) <= 6 + 32 or raw[:6] != PREFIX_AES:
        return None
    key_material = raw[6:38]
    ciphertext = raw[38:]
    if not ciphertext or len(ciphertext) % 16 != 0:
        return None
    key_hash = hashlib.sha512(key_material).digest()
    merged = hashlib.sha512(key_hash + SALT_AES).digest()
    aes_key, iv = merged[:16], merged[16:32]
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        dec = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
    except Exception:
        return None
    pad = padded[-1] if padded else 0
    if 1 <= pad <= 16:
        padded = padded[:-pad]
    if len(padded) < 64:
        return None
    if hashlib.sha512(padded[64:]).digest() != padded[:64]:
        return None
    return padded[64:]


def decrypt_storage_value(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        plain = byte_crypto_decrypt(base64.b64decode(value))
        return json.loads(plain) if plain else None
    except Exception:
        return None


def cockpit_cipher():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(open(KEY_PATH).read().strip())
    if len(key) != 32:
        raise RuntimeError("密钥长度无效")
    return AESGCM(key)


def read_accounts():
    if not os.path.isdir(ACCOUNTS_DIR):
        return {}
    cipher = cockpit_cipher()
    result = {}
    for path in glob.glob(os.path.join(ACCOUNTS_DIR, "trae_work_*.json")):
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
    cipher = cockpit_cipher()
    now = int(time.time())
    account.setdefault("created_at", now)
    account["last_used"] = now
    account_id = account["id"]
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, json.dumps(account, ensure_ascii=False).encode(), None)
    envelope = {
        "version": 1,
        "kind": "trae_work",
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


def storage_path(app_key):
    return os.path.expanduser(APPS[app_key]["storage"])


def read_storage(app_key):
    path = storage_path(app_key)
    if not os.path.exists(path):
        return None
    try:
        content = open(path).read().strip()
        return json.loads(content) if content else {}
    except Exception:
        return None


def write_storage_atomic(app_key, data):
    path = storage_path(app_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def get_current_login(app_key):
    storage = read_storage(app_key)
    if not storage:
        return None
    auth = decrypt_storage_value(storage.get(KEY_AUTH))
    if not auth or not auth.get("token"):
        return None
    account = auth.get("account") or {}
    return {
        "user_id": str(auth.get("userId", "")),
        "username": account.get("username") or account.get("email") or auth.get("userId", ""),
        "email": account.get("email") or "",
        "token_expired_at": auth.get("expiredAt", ""),
    }


def capture(app_key="solo_cn"):
    """提取当前登录账号写入账号库（Trae 的"授权导入"方式）。"""
    app = APPS.get(app_key)
    if not app:
        raise RuntimeError(f"未知应用: {app_key}")
    storage = read_storage(app_key)
    if not storage:
        raise RuntimeError(f"{app['name']} 尚未登录（找不到 {storage_path(app_key)}）")
    auth = decrypt_storage_value(storage.get(KEY_AUTH))
    if not auth or not auth.get("token"):
        raise RuntimeError(f"{app['name']} 当前未登录或登录数据无法解密")
    user_id = str(auth.get("userId") or "")
    if not user_id:
        raise RuntimeError("登录数据缺少 userId")
    info = auth.get("account") or {}
    payload = {
        "id": f"trae_work_{hashlib.md5(user_id.encode()).hexdigest()}",
        "kind": "trae_work",
        "user_id": user_id,
        "username": info.get("username") or info.get("email") or user_id,
        "email": info.get("email") or "",
        "token": auth.get("token", ""),
        "refresh_token": auth.get("refreshToken", ""),
        "token_expired_at": auth.get("expiredAt", ""),
        "host": auth.get("host", ""),
        "storage_payload": {
            KEY_AUTH: storage.get(KEY_AUTH, ""),
            KEY_SERVER: storage.get(KEY_SERVER),
            KEY_HOST: storage.get(KEY_HOST),
            KEY_USERTAG: storage.get(KEY_USERTAG, ""),
        },
    }
    write_account(payload)
    log(f"提取账号成功: {payload['username']} ← {app['name']}")
    return {k: payload[k] for k in ("id", "user_id", "username", "email")}


def inject(app_key, account_id):
    accounts = read_accounts()
    account = accounts.get(account_id)
    if not account:
        raise RuntimeError(f"账号不存在: {account_id}")
    payload = account.get("storage_payload") or {}
    if not payload.get(KEY_AUTH):
        raise RuntimeError("账号缺少登录凭证，请重新提取")
    path = storage_path(app_key)
    if os.path.exists(path):
        backup = f"{path}.harbor-bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(path, backup)
        for old in sorted(glob.glob(f"{path}.harbor-bak-*"))[:-10]:
            try:
                os.remove(old)
            except Exception:
                pass
    storage = read_storage(app_key) or {}
    storage[KEY_AUTH] = payload[KEY_AUTH]
    for key in (KEY_SERVER, KEY_HOST, KEY_USERTAG):
        if payload.get(key) is not None:
            storage[key] = payload[key]
    write_storage_atomic(app_key, storage)
    return account.get("username", "")


def is_running(app_key):
    name = APPS[app_key]["process"]
    for args in ([BIN["pgrep"], "-f", f"/Applications/{name}.app"],
                 [BIN["pgrep"], "-x", name.replace(" ", "")]):
        try:
            if subprocess.run(args, capture_output=True).returncode == 0:
                return True
        except Exception:
            continue
    return False


def quit_app(app_key):
    name = APPS[app_key]["process"]
    if not is_running(app_key):
        return True
    subprocess.run([BIN["osascript"], "-e", f'quit app "{name}"'], capture_output=True)
    for _ in range(20):
        time.sleep(0.5)
        if not is_running(app_key):
            return True
    subprocess.run([BIN["pkill"], "-f", f"/Applications/{name}.app"], capture_output=True)
    time.sleep(1.5)
    return not is_running(app_key)


def launch(app_key):
    app_path = APPS[app_key]["app_path"]
    if not os.path.exists(app_path):
        raise RuntimeError(f"未找到应用: {app_path}")
    subprocess.Popen([BIN["open"], "-a", app_path])
    time.sleep(1)
    return True


def switch(account_id, app_key="solo_cn"):
    """完整切换：退出 → 备份+注入 → 重启 → 校验。"""
    account = read_accounts().get(account_id)
    if not account:
        return {"ok": False, "error": f"账号不存在: {account_id}"}
    target_user_id = str(account.get("user_id") or "")
    previous_storage = read_storage(app_key)

    def rollback():
        if not quit_app(app_key):
            return False
        try:
            if previous_storage is None:
                path = storage_path(app_key)
                if os.path.exists(path):
                    os.remove(path)
            else:
                write_storage_atomic(app_key, previous_storage)
            launch(app_key)
            return True
        except Exception:
            return False

    if not quit_app(app_key):
        return {"ok": False, "error": f"{APPS[app_key]['name']} 未能退出，已中止（未做任何修改）"}
    try:
        username = inject(app_key, account_id)
    except Exception as error:
        return {"ok": False, "error": f"注入失败: {error}"}
    try:
        launch(app_key)
    except Exception as error:
        rolled_back = rollback()
        return {"ok": False, "error": f"目标账号启动失败: {error}", "username": username,
                "rolled_back": rolled_back}
    time.sleep(12)
    current = get_current_login(app_key)
    verified = bool(current and str(current.get("user_id") or "") == target_user_id)
    if verified:
        log(f"切换完成并已验证: {current['username']}")
        return {"ok": True, "username": username, "verified": True, "current": current}
    rolled_back = rollback()
    return {"ok": False, "username": username, "verified": False, "current": current,
            "rolled_back": rolled_back, "error": "目标账号登录校验失败，请重新提取该账号凭证"}


def api_request(account, path, params=None):
    token = account.get("token")
    if not token:
        raise RuntimeError("账号缺少 token，请重新提取")
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    account_id = account["id"]
    gateways = [GATEWAY_CACHE[account_id]] if account_id in GATEWAY_CACHE else list(API_GATEWAYS)
    errors = []
    for gw in gateways:
        req = Request(gw + path + query, headers={
            "Authorization": f"Cloud-IDE-JWT {token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        })
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            if data.get("code") == 0:
                GATEWAY_CACHE[account_id] = gw
                return gw, data.get("data", {})
            errors.append(f"{gw}: code={data.get('code')} {data.get('message')}")
        except HTTPError as error:
            errors.append(f"{gw}: HTTP {error.code}")
        except Exception as error:
            errors.append(f"{gw}: {error}")
    raise RuntimeError("云端 API 请求失败: " + "; ".join(errors))


def list_sessions(account_id, mode="work", page_size=50):
    account = read_accounts().get(account_id)
    if not account:
        raise RuntimeError("账号不存在")
    gw, data = api_request(account, "/api/remote/v1/chat_sessions",
                           {"page_size": page_size, "mode": mode})
    items = [{
        "id": it.get("chat_session_id"),
        "title": it.get("title") or "(未命名会话)",
        "mode": it.get("mode"),
        "status": it.get("status"),
        "updated_at": it.get("updated_at"),
    } for it in data.get("items", [])]
    return {"gateway": gw, "total": data.get("total", len(items)), "items": items}


def import_accounts(content):
    """导入 trae_work 账号 JSON（数组或对象）。"""
    parsed = json.loads(content) if isinstance(content, str) else content
    values = parsed if isinstance(parsed, list) else [parsed]
    imported = []
    for index, raw in enumerate(values, 1):
        if not isinstance(raw, dict) or not raw.get("user_id"):
            raise RuntimeError(f"第 {index} 条账号缺少 user_id")
        raw = dict(raw)
        raw.setdefault("id", f"trae_work_{hashlib.md5(str(raw['user_id']).encode()).hexdigest()}")
        raw.setdefault("kind", "trae_work")
        write_account(raw)
        imported.append({"id": raw["id"], "username": raw.get("username", "")})
    return imported


def status():
    """平台状态聚合：账号列表 + 各 App 当前登录。"""
    accounts = read_accounts()
    return {
        "platform": PLATFORM_ID,
        "name": PLATFORM_NAME,
        "apps": {key: {"title": app["title"], "running": is_running(key),
                       "installed": os.path.exists(app["app_path"]),
                       "current": get_current_login(key)} for key, app in APPS.items()},
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
        "features": {"auth": "capture", "switch": True, "sessions_cloud": True, "sessions_local": False},
    }
