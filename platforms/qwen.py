"""千问办公（QwenWorkCN）平台适配器 · Phase 2 占位。

调研结论（2026-08-23）：
- 登录态存 ~/Library/Application Support/QwenWorkCN/auth.dat 与 auth-v2.dat，v10 信封头。
- Keychain 有 "QwenWorkCN Safe Storage"（16B raw 密钥），但标准 Electron safeStorage
  派生（PBKDF2 saltysalt/1003、raw 直接做 key、固定 IV）均解不开 → 自定义派生方案。
- 破解需反汇编 app.asar 找加密实现；破解前本适配器仅提供状态探测。

若解密成功，账号将存入 ~/.antigravity_cockpit/qwen_accounts/（同一把 key）。
"""

import os

PLATFORM_ID = "qwen"
PLATFORM_NAME = "千问办公"

DATA_DIR = os.path.expanduser("~/Library/Application Support/QwenWorkCN")
AUTH_FILES = ("auth.dat", "auth-v2.dat")
APP_PATH = "/Applications/QwenWorkCN.app"


def _installed():
    return os.path.exists(APP_PATH)


def _logged_in():
    return any(os.path.exists(os.path.join(DATA_DIR, name)) for name in AUTH_FILES)


def status():
    return {
        "platform": PLATFORM_ID,
        "name": PLATFORM_NAME,
        "apps": {
            "qwen_work": {
                "title": "QwenWorkCN（千问办公）",
                "running": False,  # Phase 2 再接进程探测
                "installed": _installed(),
                "current": get_current_login(),
            }
        },
        "accounts": [],
        "features": {
            "auth": "planned",
            "switch": False,
            "sessions_cloud": False,
            "sessions_local": False,
            "note": "auth.dat 为自定义 v10 加密，标准 Electron safeStorage 派生已排除，"
                    "需反汇编 app.asar；破解后补齐 capture/switch。",
        },
    }


def get_current_login(app_key="qwen_work"):
    """Phase 2：解密 auth.dat 后返回账号摘要；现仅探测登录态存在性。"""
    if not _logged_in():
        return None
    return {"user_id": "", "username": "（已登录，解密待 Phase 2）", "email": "", "encrypted": True}


def capture(app_key="qwen_work"):
    raise RuntimeError("千问办公账号提取尚未实现（Phase 2）：auth.dat 加密方案待逆向")


def switch(account_id, app_key="qwen_work"):
    raise RuntimeError("千问办公账号切换尚未实现（Phase 2）")
