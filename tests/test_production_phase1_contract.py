"""Phase 1 production launch: practitioners, owner approval, directory and workspace.

The athlete/guardian portal is optional; Supabase Auth sends verification mail, so
the SPPS SMTP settings are optional but must never be half-configured.
"""

import pytest

from server import hosted_profile as hp

HOST = "aws-0-ap-southeast-1.pooler.supabase.com"
REF = "abcdefghijklmnopqrst"


def _owner() -> str:
    from server.owner_profile import OWNER_EMAIL
    return OWNER_EMAIL


def _url(role: str) -> str:
    return f"postgresql+psycopg://{role}.{REF}:not-a-real-secret@{HOST}:5432/postgres?sslmode=require"


def _env(**changes):
    env = {
        "MODEL_C_ENV": "production",
        # v2: hosted startup also requires the bot-check secret (server/v2/preflight.py); fictional value.
        "SPPS_FORM_TOKEN_SECRET": "fictional-form-token-secret-for-tests-0001",
        "SPPS_FIELD_KEYS": "k1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "SPPS_FIELD_KEY_CURRENT": "k1",
        "SPPS_PRODUCTION_RELEASE_APPROVED": "true",
        "SPPS_HOSTED_RLS": "true",
        "SPPS_BETA_V1_FREE": "true",
        "SPPS_PAYMENT_PROVIDER": "disabled",
        "SPPS_SUPABASE_AUTH_ENABLED": "true",
        "SPPS_OWNER_BOOTSTRAP_ENABLED": "true",
        "SPPS_PORTAL_ENABLED": "false",
        "SPPS_OWNER_EMAIL": _owner(),
        "SPPS_PUBLIC_BASE_URL": "https://app.winmindperform.com",
        "MODEL_C_PUBLIC_TENANT": "winmindperform",
        "SPPS_SINGLE_DATABASE_SCHEMAS": "true",
        "SPPS_SUPABASE_PROJECT_REF": REF,
        "SPPS_CLINICAL_DATABASE_NAME": "postgres",
        "SPPS_IDENTITY_DATABASE_NAME": "postgres",
        "DATABASE_URL": _url("spps_dev_app"),
        "SPPS_AUTH_DATABASE_URL": _url("spps_dev_auth"),
        "SPPS_IDENTITY_AUTH_DATABASE_URL": _url("spps_identity_auth"),
        "SPPS_IDENTITY_PORTAL_DATABASE_URL": _url("spps_identity_portal"),
    }
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_production_starts_without_portal_or_spps_smtp():
    urls = hp.validate_startup(_env())
    assert set(urls) == {"clinical_app", "clinical_auth", "identity_auth", "identity_portal"}


def test_partial_smtp_configuration_is_refused():
    with pytest.raises(hp.HostedProfileError):
        hp.validate_startup(_env(SPPS_SMTP_HOST="smtp.example.test"))


def test_enabling_the_portal_still_requires_smtp():
    with pytest.raises(hp.HostedProfileError):
        hp.validate_startup(_env(SPPS_PORTAL_ENABLED="true"))


@pytest.mark.parametrize("key", ["SPPS_PRODUCTION_RELEASE_APPROVED", "SPPS_HOSTED_RLS",
                                 "SPPS_SUPABASE_AUTH_ENABLED", "SPPS_OWNER_EMAIL"])
def test_core_release_gates_remain_mandatory(key):
    with pytest.raises(hp.HostedProfileError):
        hp.validate_startup(_env(**{key: None}))


@pytest.mark.parametrize("tenant", [None, "", "model-c-demo", "someone-else"])
def test_production_requires_the_seeded_public_tenant(tenant):
    # The serial SQL seeds only the winmindperform tenant; any other slug would list nobody and accept no enquiries.
    with pytest.raises(hp.HostedProfileError, match="MODEL_C_PUBLIC_TENANT"):
        hp.validate_startup(_env(MODEL_C_PUBLIC_TENANT=tenant))


def test_shared_or_wrong_role_credentials_are_refused():
    with pytest.raises(hp.HostedProfileError):
        hp.validate_startup(_env(SPPS_AUTH_DATABASE_URL=_url("spps_dev_app")))


def test_production_tenant_migration_is_registered_and_narrow():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sql = (root / "migrations/0028_production_public_tenant.sql").read_text(encoding="utf-8")
    assert "0028_production_public_tenant.sql" in (root / "server/release_migrations.py").read_text()
    assert "FOR SELECT TO spps_dev_app" in sql and "USING (true)" not in sql
    assert "MODEL_C_PUBLIC_TENANT\n        value: winmindperform" in (root / "render.yaml").read_text()
