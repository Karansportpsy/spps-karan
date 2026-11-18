"""Hosted-staging RLS profile against a FRESH, TLS-enabled disposable PostgreSQL only.

The runner (`python -m tools.spps_check --tier hosted`) starts `tools.ephemeral_db up-empty`
and passes its admin file in SPPS_HOSTED_TEST_ADMIN_ENV. Provisioning uses a NON-superuser
role with only CREATEROLE + CREATEDB that owns the clinical database — the privilege shape of
a Render managed PostgreSQL default user. Fictional data only.
"""
import os
import secrets
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from tools import ephemeral_guard as guard

PUBLIC_HOST = "spps-staging.example.test"
RENDER_LIKE_ADMIN = "spps_render_like_admin"


@pytest.fixture(scope="session")
def superuser_url():
    raw = os.getenv("SPPS_HOSTED_TEST_ADMIN_ENV", "")
    if not raw or not Path(raw).is_file():
        pytest.skip("SPPS_HOSTED_TEST_ADMIN_ENV not set; run python -m tools.spps_check --tier hosted")
    password = dotenv_values(raw).get("SPPS_EPHEMERAL_PASSWORD")
    url = URL.create("postgresql+psycopg", username="postgres", password=password, host="127.0.0.1",
                     port=guard.PORT, database=guard.CLINICAL_DATABASE)
    try:
        return guard.verify_ephemeral_target(url.render_as_string(hide_password=False),
                                             database=guard.CLINICAL_DATABASE)
    except guard.EphemeralGuardError as exc:
        pytest.exit(str(exc), 2)


@pytest.fixture(scope="session")
def render_like_admin(superuser_url):
    password = secrets.token_urlsafe(24)
    engine = create_engine(superuser_url, isolation_level="AUTOCOMMIT", connect_args={"sslmode": "require"})
    try:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'spps!_%' ESCAPE '!'")):
                pytest.exit("STOP: hosted tests need a fresh cluster (tools.ephemeral_db up-empty)", 2)
            from psycopg import sql
            conn.connection.driver_connection.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} CREATEROLE CREATEDB NOSUPERUSER NOBYPASSRLS NOREPLICATION"
            ).format(sql.Identifier(RENDER_LIKE_ADMIN), sql.Literal(password)))
            # PostgreSQL 15+: `public` belongs to pg_database_owner, i.e. follows the database owner.
            conn.execute(text(f"ALTER DATABASE {guard.CLINICAL_DATABASE} OWNER TO {RENDER_LIKE_ADMIN}"))
    finally:
        engine.dispose()
    return superuser_url.set(username=RENDER_LIKE_ADMIN, password=password, query={"sslmode": "require"})


@pytest.fixture(scope="session")
def admin(render_like_admin):
    from tools.hosted_db import admin_target
    return admin_target(render_like_admin.render_as_string(hide_password=False), confirm_host="127.0.0.1")


@pytest.fixture(scope="session")
def provisioned(admin):
    from tools.hosted_db import provision
    return provision(admin, identity_database=guard.IDENTITY_DATABASE, public_host=PUBLIC_HOST)


STAGING_KEYS = ("MODEL_C_INTEGRATION_DEV", "SPPS_DEV_CLINICAL_PARITY", "SPPS_PORTAL_ENABLED", "MODEL_C_DB_SCHEMA",
                "SPPS_PORTAL_DEV_OUTBOX", "SPPS_PAYMENT_PROVIDER", "SPPS_NOTIFY_PROVIDER",
                "MODEL_C_HOSTED_PILOT_UNLOCKED", "SPPS_STAGING_DATA_CLASS")


@pytest.fixture
def hosted_env(provisioned, monkeypatch):
    def apply(*, unlocked=False, portal=False, **extra):
        for key in STAGING_KEYS:
            monkeypatch.delenv(key, raising=False)
        values = {
            "MODEL_C_ENV": "staging", "SPPS_HOSTED_RLS": "true", "MODEL_C_COOKIE_SECURE": "true",
            "MODEL_C_INIT_DB": "false", "MODEL_C_SEED_DEMO": "false",
            **{k: v for k, v in provisioned.items() if not k.startswith("FICTIONAL_")},
        }
        if unlocked:
            values.update(MODEL_C_HOSTED_PILOT_UNLOCKED="true", SPPS_STAGING_DATA_CLASS="fictional")
        if portal:
            values.update(SPPS_PORTAL_ENABLED="true", SPPS_SMTP_HOST="smtp.invalid", SPPS_SMTP_SENDER="no-reply@spps.test",
                          RAZORPAY_KEY_ID="rzp_test_fictional", RAZORPAY_KEY_SECRET="fictional-secret",
                          RAZORPAY_WEBHOOK_SECRET="fictional-webhook")
        values.update(extra)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return values
    return apply


@pytest.fixture
def make_app(hosted_env):
    created = []

    def build(**kw):
        hosted_env(**kw)
        from test_support.app import create_app
        application = create_app(init_database=False, seed_demo=False)
        created.append(application)
        return application
    yield build
    for application in created:
        for name in ("engine", "auth_engine", "portal_auth_engine", "portal_engine"):
            engine = getattr(application.state, name, None)
            if engine is not None:
                engine.dispose()
