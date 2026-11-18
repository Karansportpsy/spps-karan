"""Stage 13A.5b: athlete/guardian portal authentication over real PostgreSQL.

Covers permission-matrix negative cases 1, 2, 15, 18, 19 plus enumeration,
throttling, CSRF, cookie isolation, reset and revocation. Fictional data only.
"""
import itertools
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

PASSWORD = "Fictional-Portal-Pass-2026"
_ip = itertools.count(10)


def client(app):
    # A distinct fictional client address per client keeps per-IP throttles realistic.
    return TestClient(app, client=(f"198.51.100.{next(_ip) % 250}", 50000))


def email():
    return f"portal-{uuid4().hex[:12]}@example.invalid"


def outbox(env, kind, to):
    path = Path(env["SPPS_PORTAL_DEV_OUTBOX"])
    if not path.is_file():
        return []
    return [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if r["kind"] == kind and r["to"] == to]


def register(c, address, role="athlete", **overrides):
    body = {"email": address, "password": PASSWORD, "display_name": "Fictional Portal User",
            "role": role, "adult_attested": True, "terms_accepted": True, **overrides}
    return c.post("/api/portal/auth/register", json=body)


def verified_login(app, env, role="athlete"):
    c = client(app)
    address = email()
    assert register(c, address, role).status_code == 202
    token = outbox(env, "verify-email", address)[-1]["token"]
    assert c.post("/api/portal/auth/verify-email", json={"token": token}).status_code == 200
    r = c.post("/api/portal/auth/login", json={"email": address, "password": PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c, address


def test_register_verify_login_me_and_profile(app, integration_env):
    with client(app) as c:
        address = email()
        r = register(c, address)
        assert r.status_code == 202
        accepted = r.json()
        # Unverified account cannot sign in (matrix case 1).
        assert c.post("/api/portal/auth/login", json={"email": address, "password": PASSWORD}).status_code == 401
        token = outbox(integration_env, "verify-email", address)[-1]["token"]
        assert c.post("/api/portal/auth/verify-email", json={"token": "x" * 40}).status_code == 400
        assert c.post("/api/portal/auth/verify-email", json={"token": token}).status_code == 200
        # Replay of a consumed verification token (matrix case 2).
        assert c.post("/api/portal/auth/verify-email", json={"token": token}).status_code == 400
        r = c.post("/api/portal/auth/login", json={"email": address.upper(), "password": PASSWORD})
        assert r.status_code == 200
        body = r.json()
        assert body["roles"] == ["athlete"] and body["email"] == address
        assert "password" not in json.dumps(body).lower() and "token" not in body
        cookie = r.headers["set-cookie"]
        assert "spps_portal_session=" in cookie and "HttpOnly" in cookie and "Path=/api/portal" in cookie
        assert "samesite=strict" in cookie.lower()
        me = c.get("/api/portal/auth/me").json()
        assert me["account_id"] == body["account_id"] and me["csrf"] == body["csrf"]
        profile = c.get("/api/portal/me").json()
        assert profile["account"]["status"] == "VERIFIED" and profile["roles"] == ["athlete"]
        assert profile["guardian_authorities"] == [] and profile["practitioner_links"] == []
        assert accepted == register(c, email()).json()  # identical acceptance shape


def test_duplicate_registration_is_indistinguishable(app, integration_env):
    with client(app) as c:
        address = email()
        first = register(c, address)
        second = register(c, address, role="guardian", display_name="Someone Else")
        assert first.status_code == second.status_code == 202 and first.json() == second.json()
        assert len(outbox(integration_env, "verify-email", address)) == 1
        assert len(outbox(integration_env, "existing-account-notice", address)) == 1


@pytest.mark.parametrize("overrides,status", [
    ({"adult_attested": False}, 422),           # minors cannot self-register / self-attest
    ({"terms_accepted": False}, 422),
    ({"password": "short"}, 422),
    ({"password": "password1234"}, 422),
    ({"role": "practitioner"}, 422),
    ({"role": "admin"}, 422),
    ({"email": "not-an-email"}, 422),
])
def test_invalid_registrations_rejected(app, overrides, status):
    with client(app) as c:
        address = overrides.pop("email", None) or email()
        if overrides.get("password") == "nameinpassword":
            overrides["password"] = address.split("@")[0] + "-Extra-123"
        assert register(c, address, **overrides).status_code == status


def test_password_containing_email_name_rejected(app):
    with client(app) as c:
        address = email()
        assert register(c, address, password=address.split("@")[0] + "-Extra-123").status_code == 422


def test_login_errors_do_not_reveal_account_existence(app, integration_env):
    c, address = verified_login(app, integration_env)
    with client(app) as other:
        wrong = other.post("/api/portal/auth/login", json={"email": address, "password": "Wrong-password-123"})
        unknown = other.post("/api/portal/auth/login", json={"email": email(), "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
    c.close()


def test_login_throttled_per_email(app, integration_env):
    c, address = verified_login(app, integration_env)
    codes = []
    for _ in range(9):
        with client(app) as attacker:  # different IPs: per-account limit still applies
            codes.append(attacker.post("/api/portal/auth/login",
                                       json={"email": address, "password": "Wrong-password-123"}).status_code)
    assert codes[-1] == 429 and 401 in codes
    c.close()


def test_csrf_required_and_logout_revokes(app, integration_env):
    c, _ = verified_login(app, integration_env)
    csrf = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/portal/auth/logout").status_code == 403
    assert c.post("/api/portal/auth/logout", headers={"X-CSRF-Token": "f" * 64}).status_code == 403
    saved = dict(c.cookies)
    assert c.post("/api/portal/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    c.cookies.update(saved)
    # Revoked session cannot reuse its previously accepted CSRF token (matrix case 19).
    assert c.get("/api/portal/auth/me").status_code == 401
    assert c.post("/api/portal/auth/logout-all", headers={"X-CSRF-Token": csrf}).status_code == 401
    c.close()


def test_logout_all_revokes_every_device(app, integration_env):
    c, address = verified_login(app, integration_env)
    with client(app) as phone:
        r = phone.post("/api/portal/auth/login", json={"email": address, "password": PASSWORD})
        assert r.status_code == 200
        assert c.post("/api/portal/auth/logout-all").json()["revoked_sessions"] == 2
        assert phone.get("/api/portal/auth/me").status_code == 401
    c.close()


def test_portal_and_practitioner_sessions_are_not_interchangeable(app, integration_env):
    c, _ = verified_login(app, integration_env)
    portal_token = c.cookies.get("spps_portal_session")
    with client(app) as practitioner:
        r = practitioner.post("/api/auth/login", json={"email": "alice@spps.test",
                              "password": integration_env["SPPS_DEV_ALICE_PASSWORD"], "terms_accepted": True})
        assert r.status_code == 200
        practitioner_token = practitioner.cookies.get("modelc_session")
    with client(app) as probe:
        probe.cookies.set("modelc_session", portal_token)
        assert probe.get("/api/athletes").status_code == 401
        assert probe.get("/api/auth/me").status_code == 401
    with client(app) as probe:
        probe.cookies.set("spps_portal_session", practitioner_token, path="/api/portal")
        assert probe.get("/api/portal/auth/me").status_code == 401
        assert probe.get("/api/portal/me").status_code == 401
    c.close()


def test_password_reset_flow_enumeration_safe_single_use(app, integration_env):
    c, address = verified_login(app, integration_env)
    with client(app) as anon:
        unknown = anon.post("/api/portal/auth/password-reset/request", json={"email": email()})
        known = anon.post("/api/portal/auth/password-reset/request", json={"email": address})
        assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
        token = outbox(integration_env, "password-reset", address)[-1]["token"]
        assert anon.post("/api/portal/auth/password-reset/confirm",
                         json={"token": token, "new_password": "weak"}).status_code == 422
        new_password = "Brand-New-Fictional-77"
        assert anon.post("/api/portal/auth/password-reset/confirm",
                         json={"token": token, "new_password": new_password}).status_code == 200
        assert anon.post("/api/portal/auth/password-reset/confirm",
                         json={"token": token, "new_password": new_password}).status_code == 400
        assert c.get("/api/portal/auth/me").status_code == 401  # old session revoked
        assert anon.post("/api/portal/auth/login", json={"email": address, "password": PASSWORD}).status_code == 401
        assert anon.post("/api/portal/auth/login", json={"email": address, "password": new_password}).status_code == 200
    c.close()


def test_password_change_requires_current_and_keeps_only_this_session(app, integration_env):
    c, address = verified_login(app, integration_env)
    with client(app) as other:
        assert other.post("/api/portal/auth/login", json={"email": address, "password": PASSWORD}).status_code == 200
        assert c.post("/api/portal/auth/password/change", json={"current_password": "Wrong-password-1",
                      "new_password": "Changed-Fictional-55"}).status_code == 403
        assert c.post("/api/portal/auth/password/change", json={"current_password": PASSWORD,
                      "new_password": "Changed-Fictional-55"}).status_code == 200
        assert c.get("/api/portal/auth/me").status_code == 200
        assert other.get("/api/portal/auth/me").status_code == 401
    c.close()


def test_guardian_role_registration(app, integration_env):
    c, _ = verified_login(app, integration_env, role="guardian")
    assert c.get("/api/portal/auth/me").json()["roles"] == ["guardian"]
    c.close()


def test_secrets_never_serialized(app, integration_env):
    c, address = verified_login(app, integration_env)
    blob = json.dumps([c.get("/api/portal/auth/me").json(), c.get("/api/portal/me").json()])
    for forbidden in ("pbkdf2", "password_hash", "token_hash", "csrf_hash", c.cookies.get("spps_portal_session")):
        assert forbidden not in blob
    c.close()


def test_portal_routes_closed_when_flag_off(integration_env, monkeypatch):
    monkeypatch.setenv("SPPS_PORTAL_ENABLED", "false")
    from test_support.app import create_app
    application = create_app(init_database=False, seed_demo=False)
    try:
        with client(application) as c:
            assert c.post("/api/portal/auth/login", json={"email": email(), "password": PASSWORD}).status_code == 503
            assert c.get("/api/portal/auth/me").status_code == 503
    finally:
        application.state.engine.dispose()
        application.state.auth_engine.dispose()
