"""End-to-end harness: the real production code path on a THROWAWAY local PostgreSQL.

* Database: the serial SQL set (supabase/sql/00..33) is applied to a disposable database over TLS, and the
  application connects with the four restricted roles exactly as it does on Supabase.
* Supabase Auth (GoTrue) is replaced by an in-process fake that answers the app's /auth/v1/user call.
  Everything else (production startup validation, release preflight, SECURITY DEFINER functions, row-level
  security, cookies, CSRF, the approval workflow) is the unmodified application code.

SAFETY: resetting DROPS the spps_dev, spps_identity_dev and spps_ops schemas in the database it points at.
It therefore refuses anything but a local host and requires SPPS_E2E_DESTROY_SCHEMAS=yes.
"""
import base64
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = "abcdefghij0123456789"
SB_URL = f"https://{REF}.supabase.co"
ROLE_PASSWORD = os.environ.get("SPPS_E2E_ROLE_PASSWORD", "e2e-local-test-only")
LOGIN_ROLES = ("spps_dev_app", "spps_dev_auth", "spps_identity_auth", "spps_identity_portal")


def admin_url():
    url = os.environ.get("SPPS_E2E_ADMIN_URL", "")
    if not url:
        raise RuntimeError("SPPS_E2E_ADMIN_URL is not set")
    parsed = make_url(url)
    if parsed.host not in ("127.0.0.1", "localhost", "::1") or "supabase" in url:
        raise RuntimeError("E2E refuses to run against anything but a local throwaway PostgreSQL")
    if os.environ.get("SPPS_E2E_DESTROY_SCHEMAS") != "yes":
        raise RuntimeError("Set SPPS_E2E_DESTROY_SCHEMAS=yes to confirm the target database may be wiped")
    return parsed


def admin_conn():
    p = admin_url()
    return psycopg.connect(host=p.host, port=p.port or 5432, user=p.username, password=p.password,
                           dbname=p.database, sslmode=p.query.get("sslmode", "require"), autocommit=True)


def role_conn(role):
    p = admin_url()
    return psycopg.connect(host=p.host, port=p.port or 5432, user=role, password=ROLE_PASSWORD,
                           dbname=p.database, sslmode="require")


def reset_db():
    sql_dir = ROOT / "supabase" / "sql"
    files = sorted(f for f in sql_dir.glob("[0-9][0-9]_*.sql") if f.name[:2] not in ("98", "99"))
    assert [int(f.name[:2]) for f in files] == list(range(len(files))), "serial SQL set has a gap"
    with admin_conn() as c:
        for schema in ("spps_dev", "spps_identity_dev", "spps_ops"):
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        for f in files:
            try:
                c.execute(f.read_text(encoding="utf-8"))
            except Exception as exc:  # name the failing step; the files run in one transaction each
                raise RuntimeError(f"{f.name} failed: {exc}") from exc
        for role in LOGIN_ROLES:
            c.execute(f"ALTER ROLE {role} PASSWORD '{ROLE_PASSWORD}'")
    return len(files)


def prod_env(monkeypatch):
    """Apply the production environment the Render service gets, pointed at the local database."""
    from server.owner_profile import OWNER_EMAIL
    p = admin_url()
    base = f"{p.host}:{p.port or 5432}/{p.database}?sslmode=require"

    def dsn(role):
        return f"postgresql+psycopg://{role}:{ROLE_PASSWORD}@{base}"

    env = {
        "MODEL_C_ENV": "production", "SPPS_PRODUCTION_RELEASE_APPROVED": "true",
        "SPPS_HOSTED_RLS": "true", "SPPS_SINGLE_DATABASE_SCHEMAS": "true",
        "SPPS_BETA_V1_FREE": "true", "SPPS_PAYMENT_PROVIDER": "disabled",
        "SPPS_SUPABASE_AUTH_ENABLED": "true", "SPPS_OWNER_BOOTSTRAP_ENABLED": "true",
        "SPPS_PORTAL_ENABLED": "false", "SPPS_ACCOUNT_LIFECYCLE_ENABLED": os.environ.get("SPPS_E2E_LIFECYCLE", "true"), "SPPS_SUPABASE_PROJECT_REF": REF,
        "SPPS_SUPABASE_URL": SB_URL, "SPPS_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
        "SPPS_OWNER_EMAIL": OWNER_EMAIL, "SPPS_PUBLIC_BASE_URL": "https://app.example.test",
        "MODEL_C_ALLOWED_HOSTS": "testserver,app.example.test", "MODEL_C_COOKIE_SECURE": "true",
        "MODEL_C_INIT_DB": "false", "MODEL_C_SEED_DEMO": "false", "MODEL_C_PUBLIC_TENANT": "winmindperform",
        "SPPS_CLINICAL_DATABASE_NAME": p.database, "SPPS_IDENTITY_DATABASE_NAME": p.database,
        "SPPS_TRUSTED_PROXY_HOPS": "0",
        # Required in hosted mode since the v2 phases. Throwaway test values only: never real secrets or keys.
        "SPPS_FORM_TOKEN_SECRET": "e2e-throwaway-form-token-secret-0123456789",
        "SPPS_FIELD_KEYS": "e2e:" + base64.b64encode(b"\x01" * 32).decode(), "SPPS_FIELD_KEY_CURRENT": "e2e",
        "DATABASE_URL": dsn("spps_dev_app"), "SPPS_AUTH_DATABASE_URL": dsn("spps_dev_auth"),
        "SPPS_IDENTITY_AUTH_DATABASE_URL": dsn("spps_identity_auth"),
        "SPPS_IDENTITY_PORTAL_DATABASE_URL": dsn("spps_identity_portal"),
    }
    for key in list(os.environ):
        if key.startswith(("SPPS_SMTP", "MODEL_C_PAYMENT", "RAZORPAY")):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return OWNER_EMAIL


class FakeGoTrue:
    """Just enough of Supabase Auth: users, e-mail confirmation state and password-method access tokens."""

    def __init__(self):
        self.users = {}

    def signup(self, email, confirmed=True):
        uid = str(uuid.uuid4())
        self.users[uid] = {"id": uid, "email": email,
                           "email_confirmed_at": "2026-11-15T05:00:00Z" if confirmed else None,
                           "app_metadata": {"provider": "email", "providers": ["email"]}}
        return uid

    def token(self, uid, password=True, signed_at=None):
        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        amr = [{"method": "password" if password else "otp",
                "timestamp": int(time.time()) if signed_at is None else signed_at}]
        payload = {"sub": uid, "iss": SB_URL + "/auth/v1", "aud": "authenticated",
                   "aal": "aal1", "amr": amr, "role": "authenticated"}
        return b64({"alg": "HS256", "typ": "JWT"}) + "." + b64(payload) + "." + "x" * 60

    def install(self, monkeypatch):
        fake = self

        class Resp:
            def __init__(self, code, body):
                self.status_code, self._b = code, body

            def json(self):
                return self._b

        def get(url, headers=None, **kw):
            assert url == SB_URL + "/auth/v1/user", url
            tok = (headers or {}).get("Authorization", "")[7:]
            try:
                p = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
            except Exception:
                return Resp(401, {})
            user = fake.users.get(p.get("sub"))
            return Resp(200, user) if user else Resp(401, {})

        from server import supabase_practitioner_auth as sb
        monkeypatch.setattr(sb.httpx, "get", get)


def tiny_photo_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 90, 160)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Browser:
    """One visitor: cookie jar + CSRF token."""

    def __init__(self, app, gotrue):
        from fastapi.testclient import TestClient
        self.c = TestClient(app, base_url="https://testserver")
        self.g = gotrue
        self.csrf = ""

    def sign_in(self, uid):
        r = self.c.post("/api/auth/supabase-exchange",
                        json={"access_token": self.g.token(uid), "terms_accepted": True})
        if r.status_code == 200:
            self.csrf = r.json()["csrf"]
        return r

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def post(self, path, **kw):
        return self.c.post(path, **kw)

    def send(self, method, path, **kw):
        return self.c.request(method, path, headers={"X-CSRF-Token": self.csrf}, **kw)
