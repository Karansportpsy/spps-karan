"""Hosted staging RLS profile: startup refusals and URL validation (no database connection)."""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from server import hosted_profile as hp
from server.db import Base
from server import models  # noqa: F401
from test_support.app import create_app

HOST = "spps-staging.example.test"
DB = "dpg-abcdefghij1234567890-a.singapore-postgres.render.com"


def url(role, database="spps_staging", query="?sslmode=require", password="pw-fictional"):
    return f"postgresql://{role}:{password}@{DB}:5432/{database}{query}"


def base_env(**overrides):
    env = {
        "MODEL_C_ENV": "staging", "SPPS_HOSTED_RLS": "true", "MODEL_C_ALLOWED_HOSTS": HOST,
        "MODEL_C_COOKIE_SECURE": "true", "SPPS_CLINICAL_DATABASE_NAME": "spps_staging",
        "SPPS_IDENTITY_DATABASE_NAME": "spps_staging_identity",
        "DATABASE_URL": url("spps_dev_app"), "SPPS_AUTH_DATABASE_URL": url("spps_dev_auth"),
        # v2: hosted startup also requires the bot-check secret (server/v2/preflight.py); fictional value.
        "SPPS_FORM_TOKEN_SECRET": "fictional-form-token-secret-for-tests-0001",
        "SPPS_FIELD_KEYS": "k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "SPPS_FIELD_KEY_CURRENT": "k1",
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def portal_env(**overrides):
    env = base_env(SPPS_PORTAL_ENABLED="true", SPPS_SMTP_HOST="smtp.example.test",
                   SPPS_SMTP_SENDER="no-reply@example.test", SPPS_PUBLIC_BASE_URL=f"https://{HOST}",
                   SPPS_IDENTITY_AUTH_DATABASE_URL=url("spps_identity_auth", "spps_staging_identity"),
                   SPPS_IDENTITY_PORTAL_DATABASE_URL=url("spps_identity_portal", "spps_staging_identity"))
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def test_valid_hosted_configuration_is_accepted():
    urls = hp.validate_startup(portal_env())
    assert set(urls) == {"clinical_app", "clinical_auth", "identity_auth", "identity_portal"}
    assert hp.connect_args(urls["clinical_app"], schema="spps_dev") == {
        "sslmode": "require", "options": "-c search_path=spps_dev"}


@pytest.mark.parametrize("raw,message", [
    (url("spps_dev_app", query="?sslmode=disable"), "TLS"),
    (url("spps_dev_app", query="?sslmode=prefer"), "TLS"),
    (url("spps_dev_app", query="?sslmode=allow"), "TLS"),
    (url("spps_dev_app", query="?sslmode=require&options=-csearch_path%3Dpublic"), "restricted"),
    (url("spps_dev_app", query="?sslmode=require&target_session_attrs=any"), "restricted"),
    (url("postgres"), "authenticate as spps_dev_app"),
    (url("spps_dev_auth"), "authenticate as spps_dev_app"),
    (url("spps_dev_app", database="postgres"), "unexpected database"),
    (url("spps_dev_app", password=""), "password"),
    ("sqlite:///./x.db", "PostgreSQL"),
    ("", "PostgreSQL"),
])
def test_hosted_url_refusals(raw, message):
    with pytest.raises(hp.HostedProfileError, match=message):
        hp.require_hosted_database(raw, role="spps_dev_app", database="spps_staging")


def test_tls_can_only_be_strengthened():
    target = hp.require_hosted_database(url("spps_dev_app", query="?sslmode=verify-full&sslrootcert=/etc/ca.pem"),
                                        role="spps_dev_app", database="spps_staging")
    assert hp.connect_args(target)["sslmode"] == "verify-full"
    assert hp.connect_args(hp.normalize(url("spps_dev_app", query="")))["sslmode"] == "require"


@pytest.mark.parametrize("overrides,message", [
    ({"MODEL_C_ENV": "production"}, "Production release prerequisites"),
    ({"MODEL_C_ENV": "local"}, "requires staging or production"),
    ({"MODEL_C_INTEGRATION_DEV": "true"}, "mutually exclusive"),
    ({"SPPS_DEV_CLINICAL_PARITY": "true"}, "local-only"),
    ({"MODEL_C_DB_SCHEMA": "public"}, "spps_dev only"),
    ({"MODEL_C_DB_SCHEMA": "spps_pilot"}, "spps_dev only"),
    ({"SPPS_IDENTITY_DATABASE_NAME": "spps_staging"}, "separate databases"),
    ({"SPPS_CLINICAL_DATABASE_NAME": "postgres"}, "Invalid clinical database"),
    ({"SPPS_AUTH_DATABASE_URL": url("spps_dev_auth").replace(DB, "other-host.example.test")}, "same database"),
    ({"DATABASE_URL": None}, "PostgreSQL"),
])
def test_hosted_startup_refusals(overrides, message):
    with pytest.raises(hp.HostedProfileError, match=message):
        hp.validate_startup(base_env(**overrides))


def test_hosted_staging_unlock_does_not_depend_on_legacy_data_class():
    urls = hp.validate_startup(base_env(MODEL_C_HOSTED_PILOT_UNLOCKED="true"))
    assert set(urls) == {"clinical_app", "clinical_auth"}
    assert "SPPS_STAGING_DATA_CLASS" not in hp.validate_startup.__code__.co_names

@pytest.mark.parametrize("overrides,message", [
    ({"SPPS_PORTAL_DEV_OUTBOX": "/tmp/outbox.jsonl"}, "local-only"),
    ({"SPPS_NOTIFY_PROVIDER": "file"}, "SMTP provider"),
    ({"SPPS_SMTP_HOST": None}, "SPPS_SMTP_HOST"),
    ({"SPPS_SMTP_SENDER": ""}, "SPPS_SMTP_SENDER"),
    ({"SPPS_PUBLIC_BASE_URL": f"http://{HOST}"}, "https origin"),
    ({"SPPS_PUBLIC_BASE_URL": "https://elsewhere.example.test"}, "MODEL_C_ALLOWED_HOSTS"),
    ({"SPPS_PUBLIC_BASE_URL": f"https://{HOST}/portal"}, "https origin"),
    ({"SPPS_PAYMENT_PROVIDER": "sandbox"}, "Razorpay TEST"),
    ({"SPPS_IDENTITY_PORTAL_DATABASE_URL": url("spps_identity_portal", "spps_staging")}, "unexpected database"),
    ({"SPPS_IDENTITY_AUTH_DATABASE_URL": url("spps_identity_portal", "spps_staging_identity")}, "spps_identity_auth"),
])
def test_hosted_portal_refusals(overrides, message):
    with pytest.raises(hp.HostedProfileError, match=message):
        hp.validate_startup(portal_env(**overrides))


def _legacy_staging(monkeypatch, **extra):
    for key in ("SPPS_HOSTED_RLS", "MODEL_C_INTEGRATION_DEV", "SPPS_PORTAL_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    values = {"MODEL_C_ENV": "staging", "MODEL_C_ALLOWED_HOSTS": "testserver", "MODEL_C_COOKIE_SECURE": "true",
              "MODEL_C_HOSTED_PILOT_UNLOCKED": "false"}
    values.update(extra)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_legacy_staging_profile_cannot_be_unlocked(monkeypatch):
    _legacy_staging(monkeypatch, MODEL_C_HOSTED_PILOT_UNLOCKED="true")
    with pytest.raises(RuntimeError, match="protected RLS"):
        create_app(database_url="postgresql+psycopg://x:y@somewhere/postgres", init_database=False, seed_demo=False)


def test_legacy_staging_profile_refuses_portal(monkeypatch, tmp_path):
    _legacy_staging(monkeypatch, SPPS_PORTAL_ENABLED="true")
    sqlite = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(sqlite)
    try:
        with patch("server.main.make_engine", return_value=sqlite):
            with pytest.raises(RuntimeError, match="hosted RLS profile"):
                create_app(database_url="postgresql+psycopg://x:y@somewhere/postgres", init_database=False,
                           seed_demo=False)
    finally:
        sqlite.dispose()


def test_hosted_profile_reads_environment_only(monkeypatch):
    for key, value in base_env().items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="environment only"):
        create_app(database_url=url("spps_dev_app"), init_database=False, seed_demo=False)


def test_hosted_profile_refuses_startup_migration_and_seed(monkeypatch):
    for key, value in base_env().items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="initialize or migrate"):
        create_app(init_database=True, seed_demo=False)
    with pytest.raises(RuntimeError, match="demo seed"):
        create_app(init_database=False, seed_demo=True)


# ------------------------------------------------------------------ provisioning target safety

@pytest.mark.parametrize("admin,confirm,message", [
    ("postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres?sslmode=require",
     "db.abcdefghijklmnop.supabase.co", "Supabase"),
    ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
     "aws-0-ap-south-1.pooler.supabase.com", "Supabase"),
    (f"postgresql://admin:pw@{DB}:5432/spps_staging", "dpg-other1234567890-a", "does not match"),
    ("postgresql://admin:pw@db.example.com:5432/spps_staging", "db.example.com", "Render PostgreSQL"),
    (f"postgresql://admin:pw@{DB}:5432/spps_staging?sslmode=disable", DB, "TLS"),
    ("postgresql://postgres:pw@127.0.0.1:55432/spps_dev", "127.0.0.1", "PROTECTED"),
    ("postgresql://postgres:pw@127.0.0.1:54322/postgres", "127.0.0.1", "PROTECTED"),
    ("postgresql://postgres:pw@127.0.0.1:55434/spps_dev_identity", "127.0.0.1", "PROTECTED"),
])
def test_provisioning_target_refusals(admin, confirm, message):
    from tools.hosted_db import HostedDbError, admin_target
    with pytest.raises((HostedDbError, hp.HostedProfileError), match=message):
        admin_target(admin, confirm_host=confirm)


def test_render_target_accepted_without_connecting():
    from tools.hosted_db import admin_target
    target = admin_target(f"postgresql://admin:pw@{DB}:5432/spps_staging", confirm_host=DB)
    assert target.host == DB and target.database == "spps_staging"


def test_migrate_requires_matching_backup(tmp_path):
    from tools.hosted_db import HostedDbError, _verified_backup
    dump = tmp_path / "clinical.dump"
    dump.write_bytes(b"fictional dump")
    manifest = tmp_path / "clinical.dump.sha256"
    with pytest.raises(HostedDbError, match="existing"):
        _verified_backup(manifest)
    manifest.write_text("0" * 64 + "  clinical.dump\n", encoding="utf-8")
    with pytest.raises(HostedDbError, match="mismatch"):
        _verified_backup(manifest)
    import hashlib
    manifest.write_text(hashlib.sha256(b"fictional dump").hexdigest() + "  clinical.dump\n", encoding="utf-8")
    _verified_backup(manifest)


# ------------------------------------------------------------------ proxy-aware throttle key

class _Req:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("C", (), {"host": peer})()


@pytest.mark.parametrize("hops,xff,expected", [
    ("0", "203.0.113.7", "10.0.0.9"),                       # default: socket peer, header ignored
    ("1", "203.0.113.7", "203.0.113.7"),
    ("1", "6.6.6.6, 203.0.113.7", "203.0.113.7"),           # spoofed leftmost value is ignored
    ("2", "6.6.6.6, 203.0.113.7, 10.1.1.1", "203.0.113.7"),
    ("1", "", "10.0.0.9"),
    ("2", "203.0.113.7", "10.0.0.9"),                       # chain shorter than configured hops
    ("1", "not-an-ip<script>", "10.0.0.9"),
])
def test_client_ip_uses_rightmost_trusted_hop(monkeypatch, hops, xff, expected):
    monkeypatch.setenv("SPPS_TRUSTED_PROXY_HOPS", hops)
    assert hp.client_ip(_Req(xff)) == expected


def test_proxy_hops_validated():
    with pytest.raises(hp.HostedProfileError, match="0-3"):
        hp.validate_startup(base_env(SPPS_TRUSTED_PROXY_HOPS="9"))

# Verified Supabase session-pooler mode uses one physical DB, but unique RLS roles.
def test_single_database_supabase_pooler_is_schema_isolated():
    project="abcdefghijklmnopqrst"
    host="aws-0-ap-southeast-1.pooler.supabase.com"
    def pooled(role):
        return f"postgresql://{role}.{project}:fictional-password@{host}:5432/postgres?sslmode=require"
    data=portal_env(
        SPPS_SINGLE_DATABASE_SCHEMAS="true",
        SPPS_SUPABASE_PROJECT_REF=project,
        SPPS_CLINICAL_DATABASE_NAME="postgres",
        SPPS_IDENTITY_DATABASE_NAME="postgres",
        DATABASE_URL=pooled("spps_dev_app"),
        SPPS_AUTH_DATABASE_URL=pooled("spps_dev_auth"),
        SPPS_IDENTITY_AUTH_DATABASE_URL=pooled("spps_identity_auth"),
        SPPS_IDENTITY_PORTAL_DATABASE_URL=pooled("spps_identity_portal"),
    )
    validated=hp.validate_startup(data)
    assert len(validated)==4
    assert {v.database for v in validated.values()}=={"postgres"}
    assert len({v.username for v in validated.values()})==4
    assert hp.connect_args(validated["identity_portal"],schema="spps_identity_dev")["options"]=="-c search_path=spps_identity_dev"
    with pytest.raises(hp.HostedProfileError,match="SPPS_SUPABASE_PROJECT_REF"):
        hp.validate_startup(dict(data,SPPS_SUPABASE_PROJECT_REF="different"))
    with pytest.raises(hp.HostedProfileError,match="authenticate as spps_identity_portal"):
        # The untrusted host loses the pooler-qualified role exception first.
        hp.validate_startup(dict(data,SPPS_IDENTITY_PORTAL_DATABASE_URL=pooled("spps_identity_portal").replace(host,"db.untrusted.example")))
