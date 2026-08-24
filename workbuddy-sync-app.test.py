import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


spec = importlib.util.spec_from_file_location("workbuddy_sync_app", Path(__file__).with_name("workbuddy-sync-app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

session = app.build_auth_session({
    "uid": "target", "nickname": "测试账号", "access_token": "test", "refresh_token": "refresh",
    "expires_at": 2_000_000_000_000, "auth_raw": {"refreshExpiresAt": 2_100_000_000_000},
    "profile_raw": {"uid": "old", "type": "personal"},
})
assert session["account"]["uid"] == "target"
assert session["auth"]["accessToken"] == "test"
assert session["accounts"] == [session["account"]]
assert app.build_auth_session({"uid": "target"}) is None

account = {
    "quota_raw": {
        "userResource": {"data": {"Response": {"Data": {"Accounts": [
            {"Status": 0, "PackageCode": "TCACA_code_008_cfWoLwvjU4", "CycleCapacitySizePrecise": "500", "CycleCapacityRemainPrecise": "477.84"},
            {"Status": 0, "PackageCode": "TCACA_code_007_nzdH5h4Nl0", "CycleCapacitySizePrecise": "1600", "CycleCapacityRemainPrecise": "1600"},
            {"Status": 3, "PackageCode": "TCACA_code_009_0XmEQc2xOf", "CycleCapacitySizePrecise": "100", "CycleCapacityRemainPrecise": "0"},
        ]}}}},
    },
    "usage_raw": {"data": {"Response": {"Data": {"Accounts": []}}}},
}
groups = app.quota_groups(account)
assert round(groups["base"]["used"], 2) == 22.16 and groups["base"]["total"] == 500.0
assert groups["activity"] == {"used": 0.0, "total": 1600.0}
assert groups["extra"] == {"used": 100.0, "total": 100.0}

imported = app.prepare_import_accounts({"accounts": [{
    "uid": "imported-uid", "nickname": "导入账号", "email": "user@example.com",
    "accessToken": "access", "refreshToken": "refresh", "enterpriseId": "enterprise",
}]})
assert {key: imported[0][key] for key in ("uid", "nickname", "email", "access_token", "refresh_token", "enterprise_id")} == {
    "uid": "imported-uid", "nickname": "导入账号", "email": "user@example.com",
    "access_token": "access", "refresh_token": "refresh", "enterprise_id": "enterprise",
}

exported = json.loads(app.serialize_accounts_for_export(["one", "two"], {
    "one": {"nickname": "一号", "access_token": "a"},
    "two": {"nickname": "二号", "access_token": "b"},
}))
assert [row["uid"] for row in exported] == ["one", "two"]

service = app.get_service_status()
assert service["pid"] == os.getpid() and service["rss_mb"] >= 0
assert app.script_restart_argv()[0] == sys.executable
assert app.workbuddy_launch_command() == ["open", "-a", "WorkBuddy"]
assert app.workbuddy_launch_command(hidden=True) == ["open", "-gj", "-a", "WorkBuddy"]
assert app.is_request_authorized({}, None)
assert app.is_request_authorized({"X-WorkBuddy-Access-Token": "pair"}, "pair")
assert app.is_request_authorized({"Authorization": "Bearer pair"}, "pair")
assert not app.is_request_authorized({"X-WorkBuddy-Access-Token": "wrong"}, "pair")

rfc_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
assert app.normalize_totp_secret(rfc_secret) == rfc_secret
assert app.normalize_totp_secret("otpauth://totp/Test?secret=" + rfc_secret + "&issuer=WB") == rfc_secret
assert app.totp_code(rfc_secret, at=59) == "287082"
assert app.totp_code(rfc_secret, at=1111111109) == "081804"

try:
    app.prepare_token_import({})
    raise AssertionError("空输入应抛错")
except RuntimeError:
    pass
try:
    app.prepare_token_import({"raw": "baretoken"})
    raise AssertionError("纯 token 缺 uid 应抛错")
except RuntimeError:
    pass
account, source = app.prepare_token_import({"raw": "tk", "uid": "u1"})
assert source == "token" and account["uid"] == "u1" and account["access_token"] == "tk"
account, source = app.prepare_token_import({"raw": '{"uid":"u2","access_token":"tk2","nickname":"N"}'})
assert source == "json" and account["uid"] == "u2" and account["access_token"] == "tk2" and account["nickname"] == "N"

saved = app.PENDING_AUTH
app.PENDING_AUTH = {"x": {"state": "s", "url": "http://evil.example/", "expires_at": 9_999_999_999}}
assert not app.open_authorization_url("x")["ok"]
app.PENDING_AUTH = {"y": {"state": "s", "url": "https://www.codebuddy.cn/login?state=s", "expires_at": 9_999_999_999}}
assert app.open_authorization_url("y")["ok"]
assert not app.open_authorization_url("missing")["ok"]
app.PENDING_AUTH = saved

from platforms import trae, qwen
from platforms import get_platform, list_platforms

assert trae.PLATFORM_ID == "trae" and qwen.PLATFORM_ID == "qwen"
assert get_platform("trae") is trae
try:
    get_platform("nope")
    raise AssertionError("未知平台应抛错")
except RuntimeError:
    pass
platforms = {p["id"]: p for p in list_platforms()}
assert set(platforms) >= {"trae", "qwen"}
assert platforms["trae"]["features"]["switch"] is True
assert trae.decrypt_storage_value("") is None
sample = {"id": "trae_work_x", "kind": "trae_work", "user_id": "u1", "username": "T1"}
original_write_account = trae.write_account
written_accounts = []
try:
    trae.write_account = lambda account: written_accounts.append(account.copy()) or account["id"]
    imported = trae.import_accounts([sample])
finally:
    trae.write_account = original_write_account
assert imported[0]["id"] == "trae_work_x"
assert written_accounts == [sample]

# Trae switches reject a wrong account, restore the prior login, and persist
# the rotated credential after a verified switch.
switch_functions = {
    name: getattr(trae, name)
    for name in ("read_accounts", "read_storage", "write_storage_atomic", "quit_app", "inject", "launch", "get_current_login", "capture")
}
original_sleep = trae.time.sleep
switch_events = []
try:
    trae.read_accounts = lambda _app="solo_cn": {"target-account": {"user_id": "target-user"}}
    trae.read_storage = lambda _app: {"original": "storage"}
    trae.write_storage_atomic = lambda app, data: switch_events.append(("restore", app, data))
    trae.quit_app = lambda app: switch_events.append(("quit", app)) or True
    trae.inject = lambda app, account: "目标账号"
    trae.launch = lambda app: switch_events.append(("launch", app)) or True
    trae.get_current_login = lambda app: {"user_id": "wrong-user", "username": "错误账号"}
    trae.capture = lambda app: switch_events.append(("capture", app))
    trae.time.sleep = lambda _seconds: None
    failed_switch = trae.switch("target-account", "solo_cn")
    failed_events = list(switch_events)

    switch_events.clear()
    trae.get_current_login = lambda app: {"user_id": "target-user", "username": "目标账号"}
    successful_switch = trae.switch("target-account", "solo_cn")
    successful_events = list(switch_events)
finally:
    for name, function in switch_functions.items():
        setattr(trae, name, function)
    trae.time.sleep = original_sleep

assert failed_switch["ok"] is False
assert failed_switch["rolled_back"] is True
assert ("restore", "solo_cn", {"original": "storage"}) in failed_events
assert failed_events.count(("launch", "solo_cn")) == 2
assert successful_switch["ok"] is True
assert ("capture", "solo_cn") in successful_events

# Cockpit OAuth accounts must be injectable without an obsolete storage.json
# snapshot. The generated iCube auth envelope must round-trip exactly.
oauth_auth = {
    "userId": "oauth-user",
    "token": "oauth-token",
    "refreshToken": "oauth-refresh",
    "platformId": "trae_solo_cn",
    "account": {"username": "OAuth 账号"},
}
oauth_account = {
    "id": "trae_oauth",
    "user_id": "oauth-user",
    "access_token": "oauth-token",
    "refresh_token": "oauth-refresh",
    "trae_auth_raw": oauth_auth,
    "trae_server_raw": {
        "commercialActivityInfo": {"activities": []},
        "entitlementInfo": {"identity": "lite"},
        "originPayStatusData": {},
        "serverTimeInfo": {"offset": 0},
        "host": "must-not-be-written",
    },
    "trae_usertag_raw": "sms",
}
oauth_storage = trae.build_oauth_storage(oauth_account, {
    trae.KEY_USERTAG: trae.encrypt_storage_value({"existing-user": "email"}),
    trae.KEY_HOST: {"apiHost": "existing"},
})
assert trae.decrypt_storage_value(oauth_storage[trae.KEY_AUTH]) == oauth_auth
assert json.loads(oauth_storage[trae.KEY_SERVER]) == {
    "commercialActivityInfo": {"activities": []},
    "entitlementInfo": {"identity": "lite"},
    "originPayStatusData": {},
    "serverTimeInfo": {"offset": 0},
}
assert trae.decrypt_storage_value(oauth_storage[trae.KEY_USERTAG]) == {
    "existing-user": "email",
    "oauth-user": "sms",
}
assert oauth_storage[trae.KEY_HOST] == {"apiHost": "existing"}

oauth_switch_events = []
original_oauth_switch = {
    name: getattr(trae, name)
    for name in ("read_accounts", "read_storage", "write_storage_atomic")
}
try:
    trae.read_accounts = lambda _app="solo_cn": {"trae_oauth": oauth_account}
    trae.read_storage = lambda _app: {trae.KEY_HOST: {"apiHost": "existing"}}
    trae.write_storage_atomic = lambda app, data: oauth_switch_events.append((app, data))
    assert trae.inject("solo_cn", "trae_oauth") == "OAuth 账号"
finally:
    for name, function in original_oauth_switch.items():
        setattr(trae, name, function)
assert trae.decrypt_storage_value(oauth_switch_events[0][1][trae.KEY_AUTH]) == oauth_auth

with tempfile.TemporaryDirectory() as oauth_dir:
    cipher = trae.cockpit_cipher()
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, json.dumps({
        **oauth_account,
        "nickname": "OAuth 账号",
    }, ensure_ascii=False).encode(), None)
    envelope = {
        "version": 1,
        "kind": "trae",
        "algorithm": "AES-256-GCM",
        "key_id": "local-secure-account-storage-v1",
        "nonce": __import__("base64").b64encode(nonce).decode(),
        "ciphertext": __import__("base64").b64encode(encrypted).decode(),
        "encrypted_at": 1,
    }
    Path(oauth_dir, "trae_oauth.json").write_text(json.dumps(envelope))
    original_oauth_dir = getattr(trae, "OAUTH_ACCOUNTS_DIR", None)
    original_accounts_dir = trae.ACCOUNTS_DIR
    try:
        trae.OAUTH_ACCOUNTS_DIR = oauth_dir
        trae.ACCOUNTS_DIR = str(Path(oauth_dir, "legacy-empty"))
        assert trae.read_accounts()["trae_oauth"]["username"] == "OAuth 账号"
    finally:
        trae.OAUTH_ACCOUNTS_DIR = original_oauth_dir
        trae.ACCOUNTS_DIR = original_accounts_dir

assert platforms["qwen"]["features"]["auth"] == "capture"
assert platforms["qwen"]["features"]["switch"] is True

swift_shell = Path(__file__).with_name("macos").joinpath("WorkBuddySyncApp.swift").read_text()
assert 'process.arguments = ["-B", script.path, "--port", "7531", "--no-browser"]' in swift_shell

web_source = Path(__file__).with_name("workbuddy-sync-app.py").read_text()
assert "closest('.switch-btn:not(.pf-switch)')" in web_source
assert "localStorage.getItem('activePlatform')" in web_source
assert "localStorage.setItem('activePlatform', id)" in web_source

print("OK")
