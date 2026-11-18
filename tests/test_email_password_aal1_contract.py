"""Synthetic email/password AAL1 regression tests (no live credentials)."""
import base64
import json
from pathlib import Path
from uuid import UUID
import pytest
from fastapi import HTTPException
from server import supabase_practitioner_auth as auth

UID = str(UUID("11111111-2222-4333-8444-555555555555"))
BASE = "https://fictional-project.supabase.co"

def token():
    claims={"sub": UID, "iss": BASE+"/auth/v1", "aal": "aal1", "amr":[{"method":"password"}]}
    part=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "header."+part+".synthetic"

@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED","true")
    monkeypatch.setenv("SPPS_SUPABASE_URL",BASE)
    monkeypatch.setenv("SPPS_SUPABASE_PUBLISHABLE_KEY","sb_publishable_synthetic")
    class Resp:
        status_code=200
        def json(self):
            return {"id":UID,"email":"fictional@example.invalid",
                    "email_confirmed_at":"2026-11-01T00:00:00Z",
                    "is_anonymous":False,
                    "app_metadata":{"provider":"email","providers":["email"]}}
    monkeypatch.setattr(auth.httpx,"get",lambda *_a,**_kw:Resp())

def test_email_confirmed_aal1_allowed():
    user=auth.verify_access_token(token(),password_required=True)
    assert user["uid"]==UID and user["aal"]=="aal1"

def test_email_unverified_blocked(monkeypatch):
    class Resp:
        status_code=200
        def json(self):
            return {"id":UID,"email":"fictional@example.invalid","email_confirmed_at":None,
                    "app_metadata":{"provider":"email","providers":["email"]}}
    monkeypatch.setattr(auth.httpx,"get",lambda *_a,**_kw:Resp())
    with pytest.raises(HTTPException) as exc:
        auth.verify_access_token(token())
    assert exc.value.status_code==403

def test_google_only_identity_blocked(monkeypatch):
    class Resp:
        status_code=200
        def json(self):
            return {"id":UID,"email":"fictional@example.invalid",
                    "email_confirmed_at":"2026-11-01T00:00:00Z",
                    "app_metadata":{"provider":"google","providers":["google"]}}
    monkeypatch.setattr(auth.httpx,"get",lambda *_a,**_kw:Resp())
    with pytest.raises(HTTPException) as exc:
        auth.verify_access_token(token())
    assert exc.value.status_code==403

def test_email_only_web_and_admin_approval_contract():
    root=Path(__file__).resolve().parents[1]
    for path in ("web/app.js","www/assets/app.js"):
        js=(root/path).read_text()
        assert "signInWithPassword" in js and "auth.signUp" in js
        assert "emailRedirectTo:window.location.origin+'/register'" in js
        assert "Continue with Google" not in js and "auth.mfa." not in js
        assert "Admin Dashboard" in js
    main=(root/"server/main.py").read_text()
    assert "claim_verified_platform_owner" in main
    assert "profile.status!='approved'" in main
    sql=(root/"migrations/0027_email_password_owner_approval.sql").read_text()
    assert "ck_spps_only_verified_owner_admin" in sql
    assert "pending_approval" in sql

def test_passwordless_session_not_accepted_for_workspace():
    raw={"sub": UID,"iss": BASE+"/auth/v1","aal":"aal1",
         "amr":[{"method":"otp"}]}
    part=base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as exc:
        auth.verify_access_token("header."+part+".fictional",password_required=True)
    assert exc.value.status_code==403
    assert "password" in exc.value.detail
    # The registration email-verification callback can still submit a profile.
    user=auth.verify_access_token("header."+part+".fictional")
    assert user["uid"]==UID
