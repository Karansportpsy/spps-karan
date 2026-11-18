"""PostgreSQL row-level-security tests against the THROWAWAY database from tools/v2_pg_harness.py only.

    python tools/v2_pg_harness.py up && python -m pytest tests_v2_pg -q && python tools/v2_pg_harness.py down

Every URL must point at 127.0.0.1:55437 and a spps_dev_v2* database; anything else stops the run. The tests connect
as the real restricted roles (spps_dev_app, spps_dev_auth, spps_identity_portal ...) with the transaction-local
identity the server sets, so the database policies themselves are what is being tested.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

ENV_FILE = Path(os.getenv("SPPS_V2_PG_ENV", "/tmp/spps-v2-pg/env"))
TENANT = "winmindperform"  # created by supabase/sql/29_0028_production_public_tenant.sql


def _load() -> dict:
    if not ENV_FILE.is_file():
        pytest.skip("throwaway PostgreSQL not running (python tools/v2_pg_harness.py up)")
    values = dict(line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if "=" in line)
    for key, url in values.items():
        u = make_url(url)
        if u.host != "127.0.0.1" or u.port != 55437 or not ((u.database or "").startswith("spps_dev_v2")
                                                             or u.database == "spps_dev_identity"):
            pytest.exit(f"STOP: {key} is not the throwaway database", 2)
    return values


@pytest.fixture(scope="session")
def pgenv():
    return _load()


def dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def superuser(pgenv):
    conn = psycopg.connect(dsn(pgenv["SPPS_V2_PG_ADMIN_URL"]), autocommit=True)
    yield conn
    conn.close()


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        try:
            return cur.fetchall()
        except psycopg.ProgrammingError:
            return None


@pytest.fixture(scope="session")
def tenant_id(superuser):
    return q(superuser, "SELECT id FROM spps_dev.tenants WHERE slug=%(s)s", {"s": TENANT})[0][0]


def make_practitioner(superuser, tenant_id, *, admin=False, email=None, password="FictionalPass-2026!"):
    from server.security import make_password
    email = email or f"fixture-{uuid.uuid4().hex[:10]}@example.org"
    found = q(superuser, "SELECT id FROM spps_dev.practitioners WHERE email=%(e)s", {"e": email})
    if found:  # the single owner row survives between runs on the same throwaway database
        q(superuser, "UPDATE spps_dev.practitioners SET password_hash=%(h)s, active=true WHERE id=%(p)s",
          {"h": make_password(password), "p": found[0][0]})
        return {"id": found[0][0], "email": email, "password": password, "tenant_id": tenant_id}
    pid = q(superuser, """INSERT INTO spps_dev.practitioners(tenant_id,email,name,password_hash,active,tier,is_admin)
                          VALUES (%(t)s,%(e)s,'Fixture Practitioner',%(h)s,true,'free',%(a)s) RETURNING id""",
            {"t": tenant_id, "e": email, "h": make_password(password), "a": admin})[0][0]
    q(superuser, """INSERT INTO spps_dev.practitioner_profiles(practitioner_id,qualifications,experience_years,
                     achievements,specialisms,public_approved,avatar_key,consultation_fee_paise,credentials_verified,
                     status,supabase_auth_uid,email_verified_at)
                    VALUES (%(p)s,'Fictional MSc',3,'','',false,'portrait_01',0,true,'approved',%(u)s,now())""",
      {"p": pid, "u": str(uuid.uuid4())})
    return {"id": pid, "email": email, "password": password, "tenant_id": tenant_id}


@pytest.fixture(scope="session")
def people(superuser, tenant_id):
    from server.owner_profile import OWNER_EMAIL  # the database allows only the verified owner address as admin
    return {"owner": make_practitioner(superuser, tenant_id, admin=True, email=OWNER_EMAIL),
            "a": make_practitioner(superuser, tenant_id),
            "b": make_practitioner(superuser, tenant_id)}


@contextlib.contextmanager
def as_role(pgenv, role: str, practitioner: dict | None = None, extra: dict | None = None):
    """A transaction as a restricted login role with the server-set identity; always rolled back."""
    conn = psycopg.connect(dsn(pgenv[f"SPPS_V2_PG_{role.upper()}_URL"]))
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path = spps_dev")
            if practitioner:
                cur.execute("SELECT set_config('spps.practitioner_id',%s,true), set_config('spps.tenant_id',%s,true)",
                            (str(practitioner["id"]), str(practitioner["tenant_id"])))
            for k, v in (extra or {}).items():
                cur.execute("SELECT set_config(%s,%s,true)", (k, str(v)))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def denied(conn, sql, params=None) -> bool:
    """True when PostgreSQL refuses the statement (privilege or policy)."""
    try:
        with conn.transaction():
            q(conn, sql, params)
        return False
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation):
        return True


# ------------------------------------------------------------------ the real application on PostgreSQL
@pytest.fixture
def pg_app(pgenv, monkeypatch):
    """server.main.create_app in the isolated PostgreSQL profile: restricted roles, FORCE RLS, default-deny routes."""
    monkeypatch.setenv("MODEL_C_ENV", "local")
    monkeypatch.setenv("MODEL_C_INTEGRATION_DEV", "true")
    monkeypatch.setenv("SPPS_DEV_CLINICAL_PARITY", "true")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED", "false")
    monkeypatch.setenv("SPPS_BOT_MIN_FILL_SECONDS", "0")
    monkeypatch.setenv("MODEL_C_PUBLIC_TENANT", TENANT)
    monkeypatch.setenv("SPPS_FIELD_KEYS", "k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=")
    monkeypatch.setenv("SPPS_FIELD_KEY_CURRENT", "k1")
    monkeypatch.setenv("DATABASE_URL", pgenv["SPPS_V2_PG_SPPS_DEV_APP_URL"])
    monkeypatch.setenv("SPPS_AUTH_DATABASE_URL", pgenv["SPPS_V2_PG_SPPS_DEV_AUTH_URL"])
    from server.v2 import guards
    guards.reset()
    import server.main as main
    app = main.create_app(None, init_database=False)
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()


@pytest.fixture
def fake_supabase(monkeypatch):
    from tests_v2.conftest import FakeSupabase
    fake = FakeSupabase()
    import server.supabase_practitioner_auth as sbauth
    import server.practitioner_review as review
    monkeypatch.setattr(sbauth, "verify_access_token", fake.verify)
    monkeypatch.setattr(review, "verify_access_token", fake.verify)
    return fake


def pg_login(client, person) -> dict:
    res = client.post("/api/auth/login", json={"email": person["email"], "password": person["password"],
                                               "terms_accepted": True})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf"]}


def seed_athlete(superuser, person, *, name="Fictional PG Athlete", note="Fictional PG note text."):
    """Athlete + case + note owned by ``person``, written as superuser (fixtures only)."""
    import uuid as _u
    code = "PG-" + _u.uuid4().hex[:8]
    aid = q(superuser, """INSERT INTO spps_dev.athletes(tenant_id,owner_practitioner_id,code,display_name,sport,
                          athlete_group,service_consent_recorded_at,active,created_at)
                          VALUES (%(t)s,%(p)s,%(c)s,%(n)s,'Hockey','not specified',now(),true,now()) RETURNING id""",
            {"t": person["tenant_id"], "p": person["id"], "c": code, "n": name})[0][0]
    cid = q(superuser, """INSERT INTO spps_dev.cases(tenant_id,athlete_id,owner_practitioner_id,title,category,goal,
                          status,created_at) VALUES (%(t)s,%(a)s,%(p)s,'Fictional case','Performance psychology','',
                          'active',now()) RETURNING id""", {"t": person["tenant_id"], "a": aid, "p": person["id"]})[0][0]
    nid = q(superuser, """INSERT INTO spps_dev.notes(tenant_id,case_id,author_id,note_date,content,state,revision,created_at)
                          VALUES (%(t)s,%(c)s,%(p)s,'2026-11-17',%(x)s,'draft',1,now()) RETURNING id""",
            {"t": person["tenant_id"], "c": cid, "p": person["id"], "x": note})[0][0]
    return {"athlete_id": aid, "case_id": cid, "note_id": nid, "code": code}


@pytest.fixture(scope="session")
def id_superuser(pgenv):
    conn = psycopg.connect(dsn(pgenv["SPPS_V2_PG_IDENTITY_ADMIN_URL"]), autocommit=True)
    yield conn
    conn.close()


@contextlib.contextmanager
def as_identity(pgenv, role: str, *, account=None, practitioner=None):
    conn = psycopg.connect(dsn(pgenv[f"SPPS_V2_PG_{role.upper()}_URL"]))
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path = spps_identity_dev")
            cur.execute("SELECT set_config('spps.loop_account_id',%s,true), set_config('spps.practitioner_id',%s,true),"
                        " set_config('spps.tenant_id',%s,true)",
                        (str(account or ""), str(practitioner["id"]) if practitioner else "",
                         str(practitioner["tenant_id"]) if practitioner else ""))
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def loop_pg_app(pgenv, monkeypatch, tmp_path):
    """The real app on PostgreSQL with BOTH real flags on (v1 portal + v2 athlete loop)."""
    for k, v in {"MODEL_C_ENV": "local", "MODEL_C_INTEGRATION_DEV": "true", "SPPS_DEV_CLINICAL_PARITY": "true",
                 "SPPS_BETA_V1_FREE": "true", "SPPS_SUPABASE_AUTH_ENABLED": "false", "SPPS_BOT_MIN_FILL_SECONDS": "0",
                 "MODEL_C_PUBLIC_TENANT": TENANT, "SPPS_PORTAL_ENABLED": "true", "SPPS_ATHLETE_LOOP_ENABLED": "true",
                 "SPPS_PORTAL_DEV_OUTBOX": str(tmp_path / "outbox.jsonl"),
                 "SPPS_FIELD_KEYS": "k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=", "SPPS_FIELD_KEY_CURRENT": "k1",
                 "DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_APP_URL"],
                 "SPPS_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_AUTH_URL"],
                 "SPPS_IDENTITY_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_AUTH_URL"],
                 "SPPS_IDENTITY_PORTAL_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_PORTAL_URL"]}.items():
        monkeypatch.setenv(k, v)
    from server.v2 import guards
    guards.reset()
    import server.main as main
    app = main.create_app(None, init_database=False)
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()


@pytest.fixture(autouse=True)
def _capture_v2_mail(monkeypatch):
    """Tests never send e-mail: every v2 message is kept on app.state.v2_outbox for the assertions."""
    from server.v2 import athlete_loop

    def capture(app, to, subject, body):
        if getattr(app.state, "v2_outbox", None) is None:
            app.state.v2_outbox = []
        app.state.v2_outbox.append({"to": to, "subject": subject, "body": body})
        return True
    monkeypatch.setattr(athlete_loop, "_deliver", capture)
