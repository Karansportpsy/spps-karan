"""Account lifecycle, phase A: practitioner deactivation and export (SPPS_ACCOUNT_LIFECYCLE_ENABLED).

Fictional data only. The PostgreSQL side (SECURITY DEFINER function, directory functions, startup refusal) is
proved against the real roles in tests_e2e/test_account_lifecycle_flow.py; here the API contract, the migration
text and the re-authentication rules are checked on the SQLite test application.
"""
import base64
import hashlib
import json
import re
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from server import account_lifecycle as lifecycle
from server.models import Athlete, Audit, Practitioner, PractitionerProfile
from test_support.app import create_app

ROOT = Path(__file__).resolve().parents[1]
LOGIN = {"email": "practitioner@modelc.test", "password": "DemoOnly!2026", "terms_accepted": True}


@pytest.fixture(autouse=True)
def _fresh_throttle():
    lifecycle._failures.clear()
    yield
    lifecycle._failures.clear()


def build(tmp_path, monkeypatch, flag=True):
    monkeypatch.setenv("MODEL_C_ENV", "local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED", "false")
    if flag:
        monkeypatch.setenv("SPPS_ACCOUNT_LIFECYCLE_ENABLED", "true")
    else:
        monkeypatch.delenv("SPPS_ACCOUNT_LIFECYCLE_ENABLED", raising=False)
    app = create_app(f"sqlite:///{tmp_path / 'life.db'}", init_database=True, seed_demo=True)
    client = TestClient(app)
    login = client.post("/api/auth/login", json=LOGIN)
    assert login.status_code == 200, login.text
    with app.state.DB() as db:
        me = db.scalar(select(Practitioner).where(Practitioner.email == LOGIN["email"]))
        me_id, tenant_id = me.id, me.tenant_id
    return app, client, {"X-CSRF-Token": login.json()["csrf"]}, me_id, tenant_id


def listed_ids(client):
    response = client.get("/api/public/practitioners")
    assert response.status_code == 200
    return {row["id"] for row in response.json()}


def audit_actions(app, me_id):
    with app.state.DB() as db:
        return [a.action for a in db.scalars(select(Audit).where(Audit.object_type == "practitioner",
                                                                 Audit.object_id == me_id).order_by(Audit.id))]


# ---- flag off -----------------------------------------------------------------------------------------------------

def test_routes_do_not_exist_while_the_flag_is_off(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch, flag=False)
    assert client.get("/api/account/lifecycle").status_code == 404
    assert client.post("/api/account/deactivate", json={"confirm": True}, headers=csrf).status_code in (404, 405)
    assert client.post("/api/account/export", json={"password": LOGIN["password"]}, headers=csrf).status_code in (404, 405)
    assert me_id in listed_ids(client), "the directory is untouched by the feature while it is off"


# ---- deactivate / reactivate --------------------------------------------------------------------------------------

def test_deactivation_takes_the_listing_offline_and_reactivation_restores_it(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch)
    assert me_id in listed_ids(client)
    state = client.get("/api/account/lifecycle").json()
    assert state["deactivated"] is False and state["listed"] is True and state["closure_available"] is False

    off = client.post("/api/account/deactivate", json={"confirm": True}, headers=csrf)
    assert off.status_code == 200, off.text
    assert off.json()["deactivated"] is True and off.json()["listed"] is False and off.json()["deactivated_at"]
    assert me_id not in listed_ids(client)
    assert client.get(f"/api/public/practitioners/{me_id}/photo").status_code == 404

    again = client.post("/api/account/deactivate", json={"confirm": True}, headers=csrf).json()
    assert again["deactivated_at"] == off.json()["deactivated_at"], "the first deactivation time is kept"

    on = client.post("/api/account/reactivate", json={"confirm": True}, headers=csrf)
    assert on.status_code == 200 and on.json()["deactivated"] is False and on.json()["listed"] is True
    assert me_id in listed_ids(client)
    with app.state.DB() as db:
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == me_id))
        assert profile.status == "approved" and profile.credentials_verified and profile.public_approved, \
            "deactivation never touches the review flags, so reactivation needs no new review"
    assert audit_actions(app, me_id).count("account.deactivated") == 2
    assert "account.reactivated" in audit_actions(app, me_id)


def test_state_changes_need_the_csrf_token_and_an_explicit_confirmation(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch)
    assert client.post("/api/account/deactivate", json={"confirm": True}).status_code in (401, 403)
    assert client.post("/api/account/deactivate", json={"confirm": False}, headers=csrf).status_code == 422
    assert client.post("/api/account/deactivate", json={}, headers=csrf).status_code == 422
    assert me_id in listed_ids(client)
    fresh = TestClient(app)
    assert fresh.get("/api/account/lifecycle").status_code == 401
    assert fresh.post("/api/account/export", json={"password": LOGIN["password"]}).status_code in (401, 403)


def test_a_deactivated_practitioner_keeps_working_but_cannot_be_booked_or_enquired(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch)
    service_id = client.get("/api/public/services").json()["services"][0]["id"]

    def enquire():
        return client.post("/api/public/enquiries", json={
            "service_id": service_id, "practitioner_id": me_id, "contact_name": "Synthetic Visitor",
            "contact_email": "visitor@example.com", "enquiring_for": "myself", "sport": "Archery",
            "support_topic": "Confidence", "contact_preference": "email", "consent": True, "website": ""})

    assert enquire().status_code == 201, "positive control: a listed practitioner can be enquired about"
    assert client.get(f"/api/public/practitioners/{me_id}/slots").status_code == 200
    assert client.post("/api/account/deactivate", json={"confirm": True}, headers=csrf).status_code == 200
    refused = enquire()
    assert refused.status_code == 404, refused.text
    assert client.get(f"/api/public/practitioners/{me_id}/slots").status_code == 404
    assert client.get("/api/athletes").status_code == 200, "the workspace stays usable"
    assert client.get("/api/account/lifecycle").json()["deactivated"] is True
    client.post("/api/account/reactivate", json={"confirm": True}, headers=csrf)
    assert enquire().status_code == 201


# ---- export -------------------------------------------------------------------------------------------------------

def test_export_requires_the_current_password(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch)
    for body in ({}, {"password": ""}, {"password": "not-the-password"}):
        assert client.post("/api/account/export", json=body, headers=csrf).status_code == 403
    ok = client.post("/api/account/export", json={"password": LOGIN["password"]}, headers=csrf)
    assert ok.status_code == 200, ok.text
    assert ok.headers["cache-control"] == "no-store"
    assert ok.headers["content-disposition"].startswith("attachment; filename=\"spps-export-")
    assert audit_actions(app, me_id).count("account.exported") == 1


def test_export_holds_only_this_practitioners_records_and_no_secrets(tmp_path, monkeypatch):
    app, client, csrf, me_id, tenant_id = build(tmp_path, monkeypatch)
    with app.state.DB() as db:
        other = db.scalar(select(Practitioner).where(Practitioner.id != me_id, Practitioner.tenant_id == tenant_id))
        mine = db.scalar(select(Athlete).where(Athlete.owner_practitioner_id == me_id))
        db.add(Athlete(tenant_id=tenant_id, owner_practitioner_id=other.id if other else me_id + 999,
                       code="OTHER-1", display_name="Fictional Other Client", sport="Fencing"))
        stored_hash = db.scalar(select(Practitioner.password_hash).where(Practitioner.id == me_id))
        db.commit()
        mine_code = mine.code if mine else None
    result = client.post("/api/account/export", json={"password": LOGIN["password"]}, headers=csrf)
    assert result.status_code == 200
    document = result.json()
    assert document["format"] == "spps-practitioner-export-v1" and document["warnings"] == []
    assert "confidential client information" in document["notice"]
    data = document["data"]
    assert {"profile", "athletes", "cases", "notes", "observations", "bookings", "enquiries"} <= set(data)
    codes = {a["code"] for a in data["athletes"]}
    assert "OTHER-1" not in codes and "Fictional Other Client" not in result.text
    if mine_code:
        assert mine_code in codes
    assert all(a["owner_practitioner_id"] == me_id for a in data["athletes"])
    assert all(c["owner_practitioner_id"] == me_id for c in data["cases"])
    text = result.text
    for secret in (stored_hash, "password_hash", "token_hash", "csrf_token", "avatar_bytes", "supabase_auth_uid",
                   "provider_payment_id", "provider_order_id"):
        assert secret not in text, secret
    assert document["practitioner"]["id"] == me_id and data["profile"]["practitioner_id"] == me_id


def test_five_failed_confirmations_lock_the_export_for_a_while(tmp_path, monkeypatch):
    app, client, csrf, me_id, _ = build(tmp_path, monkeypatch)
    for _ in range(5):
        assert client.post("/api/account/export", json={"password": "wrong"}, headers=csrf).status_code == 403
    locked = client.post("/api/account/export", json={"password": LOGIN["password"]}, headers=csrf)
    assert locked.status_code == 429
    lifecycle._failures.clear()
    assert client.post("/api/account/export", json={"password": LOGIN["password"]}, headers=csrf).status_code == 200


# ---- hosted re-authentication (Supabase) ---------------------------------------------------------------------------

def token(uid, signed_at, method="password"):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    claims = {"sub": uid, "amr": [{"method": method, "timestamp": signed_at}]}
    return b64({"alg": "HS256"}) + "." + b64(claims) + "." + "x" * 60


def hosted(monkeypatch, tmp_path, uid):
    app, client, csrf, me_id, tenant_id = build(tmp_path, monkeypatch)
    with app.state.DB() as db:
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == me_id))
        profile.supabase_auth_uid = uid
        db.commit()
    monkeypatch.setattr(lifecycle.sbauth, "enabled", lambda: True)
    monkeypatch.setattr(lifecycle.sbauth, "verify_access_token",
                        lambda tok, password_required=False: {"uid": uid, "email": "x@example.com"})
    return app, me_id


@pytest.mark.parametrize("age,expected", [(30, None), (-20, None), (900, 401), (-3600, 401)])
def test_hosted_reauth_accepts_only_a_recent_password_sign_in(tmp_path, monkeypatch, age, expected):
    uid = "123e4567-e89b-12d3-a456-426614174000"
    app, me_id = hosted(monkeypatch, tmp_path, uid)
    with app.state.DB() as db:
        me = db.get(Practitioner, me_id)
        call = lambda tok: lifecycle.reauthenticate(db, me, lifecycle.ReauthIn(supabase_access_token=tok))
        if expected is None:
            call(token(uid, time.time() - age))
        else:
            with pytest.raises(HTTPException) as caught:
                call(token(uid, time.time() - age))
            assert caught.value.status_code == expected


def test_hosted_reauth_rejects_missing_token_other_accounts_and_non_password_tokens(tmp_path, monkeypatch):
    uid = "123e4567-e89b-12d3-a456-426614174000"
    app, me_id = hosted(monkeypatch, tmp_path, uid)
    with app.state.DB() as db:
        me = db.get(Practitioner, me_id)
        with pytest.raises(HTTPException) as caught:
            lifecycle.reauthenticate(db, me, lifecycle.ReauthIn())
        assert caught.value.status_code == 401
        monkeypatch.setattr(lifecycle.sbauth, "verify_access_token",
                            lambda tok, password_required=False: {"uid": "00000000-0000-4000-8000-000000000000"})
        with pytest.raises(HTTPException) as caught:
            lifecycle.reauthenticate(db, me, lifecycle.ReauthIn(supabase_access_token=token(uid, time.time())))
        assert caught.value.status_code == 403
        monkeypatch.setattr(lifecycle.sbauth, "verify_access_token",
                            lambda tok, password_required=False: {"uid": uid})
        with pytest.raises(HTTPException) as caught:
            lifecycle.reauthenticate(db, me, lifecycle.ReauthIn(supabase_access_token=token(uid, time.time(), "otp")))
        assert caught.value.status_code == 401, "an e-mail link or one-time code is not a password confirmation"
        with pytest.raises(HTTPException):
            lifecycle.reauthenticate(db, me, lifecycle.ReauthIn(password=LOGIN["password"]))


# ---- migration 0032 contract ---------------------------------------------------------------------------------------

def function_body(sql, tag):
    match = re.search(rf"AS \${tag}\$(.*?)\${tag}\$;", sql, re.S)
    assert match, tag
    return match.group(1)


def test_migration_0032_adds_the_column_and_one_narrow_function_and_nothing_wider():
    name = "0032_account_lifecycle"
    raw = (ROOT / "migrations" / f"{name}.sql").read_text(encoding="utf-8")
    code = re.sub(r"(?m)^--.*$", "", raw)
    assert code.strip().startswith("BEGIN;") and code.strip().endswith("COMMIT;")
    assert "ADD COLUMN IF NOT EXISTS deactivated_at timestamptz" in code
    assert not re.search(r"(?i)\b(DROP|DELETE|TRUNCATE|DISABLE ROW|NO FORCE)\b", code)
    assert not re.search(r"(?i)GRANT\s+(UPDATE|INSERT|DELETE|ALL)\b[^;]*\bON\b", code), "no table right is widened"
    grants = re.findall(r"(?i)GRANT EXECUTE ON FUNCTION ([^\s]+) TO (\w+)", code)
    assert grants == [("spps_dev.set_own_account_deactivation(boolean)", "spps_dev_app")]
    assert "REVOKE ALL ON FUNCTION spps_dev.set_own_account_deactivation(boolean) FROM PUBLIC" in code
    definitions = re.findall(r"CREATE OR REPLACE FUNCTION (spps_dev\.\w+)", code)
    assert definitions == ["spps_dev.public_practitioner_directory", "spps_dev.public_practitioner_photo",
                           "spps_dev.set_own_account_deactivation"]
    assert code.count("SECURITY DEFINER") == 3 and code.count("SET search_path=pg_catalog,spps_dev") == 3
    deact = function_body(code, "deact")
    assert "practitioner_id()" in code and "p_practitioner_id" not in deact, "no caller-chosen target"
    assert "WHERE profile.practitioner_id = v_practitioner_id" in deact


@pytest.mark.parametrize("tag", ["dir", "photo"])
def test_public_functions_differ_from_0029_only_by_the_deactivation_filter(tag):
    old = function_body((ROOT / "migrations" / "0029_public_directory_enquiry_owner_profile.sql").read_text("utf-8"), tag)
    new = function_body((ROOT / "migrations" / "0032_account_lifecycle.sql").read_text("utf-8"), tag)
    removed = "\n".join(line for line in new.splitlines() if "deactivated_at IS NULL" not in line)
    assert removed.strip() == old.strip()
    assert new.count("deactivated_at IS NULL") == 1


def test_serial_copy_matches_the_migration_and_the_ledger_checksum():
    name = "0032_account_lifecycle"
    raw = (ROOT / "migrations" / f"{name}.sql").read_text(encoding="utf-8")
    serial = (ROOT / "supabase" / "sql" / f"33_{name}.sql").read_text(encoding="utf-8")
    inner = "\n".join(raw.splitlines()[1:-1])
    assert inner in serial
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert f"VALUES ('{name}.sql','{checksum}')" in serial
    assert serial.count("BEGIN;") == 1 and serial.rstrip().endswith("COMMIT;")


def test_startup_refuses_the_flag_until_the_migration_is_in_the_database():
    source = (ROOT / "server" / "release_preflight.py").read_text(encoding="utf-8")
    assert "account_lifecycle.enabled()" in source and "set_own_account_deactivation" in source
    assert "a.attname='deactivated_at'" in source, "the column must exist"
    assert "LIKE '%deactivated_at%') = 2" in source, "both public functions must carry the deactivation filter"
    assert "has_function_privilege('spps_dev_app'" in source and "has not been run" in source


def test_orm_does_not_map_the_column_so_old_databases_keep_working():
    models = (ROOT / "server" / "models.py").read_text(encoding="utf-8")
    assert "deactivated_at" not in models, "mapping it would break every profile query before the SQL is run"


# ---- web contract --------------------------------------------------------------------------------------------------

def test_web_ui_shows_the_section_only_when_the_server_enables_it_and_never_keeps_the_password():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "if(!l||!l.enabled)return '';" in js, "hidden unless /api/account/lifecycle answers"
    assert "state.lifecycle=await api('/api/account/lifecycle')}catch{state.lifecycle=null}" in js
    assert "${lifecycleCard()}</div>" in js
    for action in ("account-deactivate", "account-reactivate", "account-export"):
        assert f"a==='{action}'" in js, action
    assert "data-form=\"account-export\"" in js and "autocomplete=\"current-password\"" in js
    assert "accountExportDownload(raw('password'))" in js
    assert not re.search(r"state\.password\s*=", js) and "localStorage.setItem('password" not in js
    assert "state.lifecycle.password" not in js
    assert "lifecycle:undefined" in js, "signing out forgets the account state"


def test_www_copy_is_byte_identical_to_web():
    for source, staged in (("app.js", "assets/app.js"), ("styles.css", "assets/styles.css")):
        assert (ROOT / "web" / source).read_bytes() == (ROOT / "www" / staged).read_bytes(), source
