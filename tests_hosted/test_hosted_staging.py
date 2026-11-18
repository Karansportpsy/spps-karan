"""Acceptance + negative tests: hosted staging RLS profile on a fresh TLS PostgreSQL (fictional data only)."""
import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from starlette.websockets import WebSocketDisconnect

from server import hosted_profile as hp
from tools import ephemeral_guard as guard
from tests_hosted.conftest import PUBLIC_HOST

BASE = f"https://{PUBLIC_HOST}"


def client(app):
    return TestClient(app, base_url=BASE)


def login(c, provisioned, name="alice"):
    r = c.post("/api/auth/login", json={"email": f"{name}@spps.test", "terms_accepted": True,
                                        "password": provisioned[f"FICTIONAL_{name.upper()}_PASSWORD"]})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "modelc_session=" in cookie and "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r.json()["id"]


def create_athlete(c):
    r = c.post("/api/athletes", json={"code": uuid4().hex[:20], "display_name": "Fictional Hosted Athlete",
                                      "sport": "Badminton", "service_consent_recorded": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def role_engine(provisioned, key, database=None):
    target = hp.normalize(provisioned[key])
    if database:
        target = target.set(database=database)
    return create_engine(target.set(query={}), connect_args={"sslmode": "require"})


# ------------------------------------------------------------------ provisioning

def test_provisioned_instance_verifies_and_has_no_pending_migrations(admin, provisioned):
    from tools.hosted_db import pending, verify
    assert verify(admin, identity_database=guard.IDENTITY_DATABASE) == []
    assert pending(admin, identity_database=guard.IDENTITY_DATABASE) == {
        guard.CLINICAL_DATABASE: [], guard.IDENTITY_DATABASE: []}
    for key in ("DATABASE_URL", "SPPS_AUTH_DATABASE_URL", "SPPS_IDENTITY_AUTH_DATABASE_URL",
                "SPPS_IDENTITY_PORTAL_DATABASE_URL"):
        assert "sslmode=require" in provisioned[key]
    assert provisioned["SPPS_PUBLIC_BASE_URL"] == BASE


def test_provisioning_refuses_a_non_fresh_instance(admin, provisioned):
    from tools.hosted_db import HostedDbError, provision
    with pytest.raises(HostedDbError, match="not fresh"):
        provision(admin, identity_database=guard.IDENTITY_DATABASE, public_host=PUBLIC_HOST)


def test_admin_holds_no_lasting_identity_owner_privilege(admin, provisioned):
    from tools.hosted_db import _engine
    engine = _engine(admin, guard.IDENTITY_DATABASE)
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT pg_has_role(current_user, 'spps_identity_owner', 'SET')")) is False
            with pytest.raises(ProgrammingError):
                conn.execute(text("SET ROLE spps_identity_owner"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("key,foreign_database", [
    ("DATABASE_URL", guard.IDENTITY_DATABASE),
    ("SPPS_AUTH_DATABASE_URL", guard.IDENTITY_DATABASE),
    ("SPPS_IDENTITY_AUTH_DATABASE_URL", guard.CLINICAL_DATABASE),
    ("SPPS_IDENTITY_PORTAL_DATABASE_URL", guard.CLINICAL_DATABASE),
])
def test_each_login_role_can_connect_to_one_database_only(provisioned, key, foreign_database):
    engine = role_engine(provisioned, key, foreign_database)
    try:
        with pytest.raises(OperationalError, match="permission denied"):
            with engine.connect():
                pass
    finally:
        engine.dispose()


def test_identity_roles_cannot_read_identity_base_tables_and_clinical_rls_holds(provisioned):
    auth = role_engine(provisioned, "SPPS_IDENTITY_AUTH_DATABASE_URL")
    app = role_engine(provisioned, "DATABASE_URL")
    try:
        with auth.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("SELECT count(*) FROM spps_identity_dev.platform_accounts"))
        with app.connect() as conn:
            # Practitioner credentials are reachable only through the separate auth role.
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("SELECT count(*) FROM spps_dev.practitioners"))
        with app.connect() as conn:
            # No transaction-local practitioner identity: FORCE RLS returns nothing.
            assert conn.scalar(text("SELECT count(*) FROM spps_dev.athletes")) == 0
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("DELETE FROM spps_dev.audit_events"))
    finally:
        auth.dispose()
        app.dispose()


def test_forward_only_migrate_requires_backup_and_is_idempotent(admin, provisioned, tmp_path):
    from tools.hosted_db import HostedDbError, migrate
    with pytest.raises(HostedDbError, match="backup"):
        migrate(admin, identity_database=guard.IDENTITY_DATABASE, backup_manifest=tmp_path / "none.sha256")
    dump = tmp_path / "staging.dump"
    dump.write_bytes(b"fictional verified backup stand-in")
    manifest = tmp_path / "staging.dump.sha256"
    manifest.write_text(hashlib.sha256(dump.read_bytes()).hexdigest() + "  staging.dump\n", encoding="utf-8")
    assert migrate(admin, identity_database=guard.IDENTITY_DATABASE, backup_manifest=manifest) == {
        guard.CLINICAL_DATABASE: [], guard.IDENTITY_DATABASE: []}


# ------------------------------------------------------------------ application profile

def test_hosted_connections_use_tls_and_restricted_roles(make_app):
    app = make_app()
    for engine, role in ((app.state.engine, "spps_dev_app"), (app.state.auth_engine, "spps_dev_auth")):
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")) is True
            assert conn.scalar(text("SELECT current_user")) == role
            assert conn.scalar(text("SHOW search_path")) == "spps_dev"


def test_hosted_staging_is_locked_by_default(make_app):
    app = make_app(portal=True)
    with client(app) as c:
        assert c.get("/health/live").status_code == 200
        assert c.get("/health/ready").json()["status"] == "ready"
        for method, path in (("get", "/api/athletes"), ("post", "/api/auth/login"), ("get", "/api/runtime"),
                             ("post", "/api/portal/auth/register"), ("get", "/api/video/1/status")):
            r = getattr(c, method)(path)
            assert r.status_code == 503 and r.json()["status"] == "locked", path
        assert c.get("/").headers["X-Robots-Tag"] == "noindex, nofollow"
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/video/1?ticket=x") as ws:
                ws.receive_json()


def test_staging_unlock_has_no_legacy_fictional_marker_dependency(hosted_env):
    hosted_env(MODEL_C_HOSTED_PILOT_UNLOCKED="true")
    from server.hosted_profile import validate_startup
    assert set(validate_startup()) >= {"clinical_app", "clinical_auth"}


def test_unlocked_practitioner_isolation_and_default_deny(make_app, provisioned):
    app = make_app(unlocked=True)
    with client(app) as alice, client(app) as bob:
        runtime = alice.get("/api/runtime").json()
        assert runtime["profile"] == "hosted-staging-rls" and runtime["data_class"] == "staging"
        assert runtime["production_ready"] is False and runtime["integration_development"] is True
        login(alice, provisioned)
        login(bob, provisioned, "bob")
        aid = create_athlete(alice)
        assert alice.get(f"/api/athletes/{aid}").status_code == 200
        assert bob.get(f"/api/athletes/{aid}").status_code == 404
        assert aid not in [a["id"] for a in bob.get("/api/athletes").json()]
        case = alice.post("/api/cases", json={"athlete_id": aid, "title": "Fictional hosted case"})
        assert case.status_code == 201, case.text
        note = alice.post("/api/notes", json={"case_id": case.json()["id"], "note_date": "2026-11-13",
                                              "content": "Fictional hosted staging note"})
        assert note.status_code == 201, note.text
        assert bob.post("/api/notes", json={"case_id": case.json()["id"], "note_date": "2026-11-13",
                                            "content": "Cross-practitioner write attempt"}).status_code == 404
        # Routes without restricted-role grants stay closed (default deny), as in the local milestone.
        for path in ("/api/public/services", "/api/public/practitioners", "/api/rooms/1/reviews"):
            assert alice.get(path).status_code == 503, path
        assert alice.post("/api/admin/erase-test-data").status_code == 503


def test_hosted_portal_identity_flow_crosses_databases_through_definer_functions(make_app, provisioned):
    app = make_app(unlocked=True, portal=True)
    with client(app) as alice, client(app) as bob, client(app) as anonymous:
        login(alice, provisioned)
        login(bob, provisioned, "bob")
        aid = create_athlete(alice)
        invite = alice.post(f"/api/athletes/{aid}/portal-invitations")
        assert invite.status_code == 201, invite.text
        assert len(invite.json()["code"]) >= 8
        assert bob.post(f"/api/athletes/{aid}/portal-invitations").status_code == 404
        assert alice.get(f"/api/athletes/{aid}/portal-links").status_code == 200
        assert anonymous.get("/api/portal/me").status_code == 401
        assert anonymous.get("/api/video/1/status?ticket=" + "x" * 43).status_code == 403
        assert anonymous.post("/api/portal/bookings/1/sandbox-checkout", json={"order_id": "x"}).status_code in (401, 404)
        assert app.state.payment_providers.keys() == {"razorpay"}
    with app.state.portal_engine.connect() as conn:
        assert conn.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")) is True


@pytest.mark.parametrize("overrides,message", [
    ({"SPPS_PORTAL_DEV_OUTBOX": "output/x.jsonl"}, "local-only"),
    ({"SPPS_PUBLIC_BASE_URL": f"http://{PUBLIC_HOST}"}, "https origin"),
    ({"RAZORPAY_KEY_ID": "rzp_live_x"}, "TEST keys"),
    ({"DATABASE_URL": None}, "PostgreSQL"),
])
def test_hosted_portal_startup_refusals(hosted_env, monkeypatch, overrides, message):
    hosted_env(unlocked=True, portal=True)
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    from test_support.app import create_app
    with pytest.raises(Exception, match=message):
        create_app(init_database=False, seed_demo=False)


def test_superuser_or_wrong_role_url_is_refused_before_serving(hosted_env, monkeypatch, provisioned, superuser_url):
    hosted_env(unlocked=True)
    monkeypatch.setenv("DATABASE_URL", provisioned["SPPS_AUTH_DATABASE_URL"])
    from test_support.app import create_app
    with pytest.raises(RuntimeError, match="spps_dev_app"):
        create_app(init_database=False, seed_demo=False)
