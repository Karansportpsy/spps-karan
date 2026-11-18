"""A session created by the email confirmation link must be upgraded with the password.

The server accepts only Supabase sessions whose authentication methods include "password"
(server/supabase_practitioner_auth.py). The browser must therefore detect a link session and
sign in with the password before the owner exchange, instead of sending a token the server rejects.
"""
import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
AUTH = (ROOT / "server" / "supabase_practitioner_auth.py").read_text(encoding="utf-8")


def _token(claims: dict) -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64(claims)}.signature"


def _helper_source() -> str:
    match = re.search(r"function sessionUsedPassword\(session\)\{.*?\n\}\n", APP, re.S)
    assert match, "sessionUsedPassword helper is missing from web/app.js"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("amr,expected", [
    ([{"method": "password", "timestamp": 1}], True),
    ([{"method": "otp", "timestamp": 1}], False),
    ([{"method": "email/signup", "timestamp": 1}], False),
    ([{"method": "otp"}, {"method": "password"}], True),
    ([], False),
    (None, False),
    ("password", False),
])
def test_helper_reads_the_authentication_methods(amr, expected):
    claims = {"sub": "u", "aal": "aal1"}
    if amr is not None:
        claims["amr"] = amr
    script = _helper_source() + f"\nconsole.log(sessionUsedPassword({{access_token:{json.dumps(_token(claims))}}}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
    assert out == str(expected).lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.!!!.c"])
def test_helper_treats_unreadable_tokens_as_not_password(token):
    script = _helper_source() + f"\nconsole.log(sessionUsedPassword({{access_token:{json.dumps(token)}}}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "false"


def test_the_server_rule_the_browser_mirrors_is_still_in_place():
    assert 'item.get("method") == "password"' in AUTH
    assert "password_required=True" in (ROOT / "server" / "main.py").read_text(encoding="utf-8")


def test_login_checks_the_session_before_calling_the_server():
    body = APP.split("async function handleSupabaseLogin(){", 1)[1].split("async function showApplicationStatus", 1)[0]
    assert body.index("sessionUsedPassword(") < body.index("/api/auth/supabase-exchange")


def test_registration_signs_in_with_the_password_when_returning_from_the_link():
    branch = APP.split("if(type==='practitioner-register'){", 1)[1].split("if(type==='login'){", 1)[0]
    assert "sessionUsedPassword(session)" in branch
    assert "signInWithPassword({email,password:raw('password')})" in branch
    # The token sent to the server is the upgraded session's, not the link session's.
    assert "supabase_access_token=session.access_token" in branch
    assert "sessionData.session.access_token" not in branch


def test_the_confirmation_dialog_does_not_ask_for_a_code():
    assert "verifyOtp" not in APP
    assert "Email verification code" not in APP


def test_the_served_copy_is_identical():
    assert (ROOT / "www" / "assets" / "app.js").read_bytes() == (ROOT / "web" / "app.js").read_bytes()
