import importlib.util
import json
import os
import sys
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
