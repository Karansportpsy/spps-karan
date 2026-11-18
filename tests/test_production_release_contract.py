"""Release gates are explicit; staging marker removal never disables protection."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_production_profile_removes_only_obsolete_staging_marker():
    hosted=(ROOT/"server/hosted_profile.py").read_text()
    assert "SPPS_STAGING_DATA_CLASS" not in hosted
    assert '"SPPS_PRODUCTION_RELEASE_APPROVED": "true"' in hosted
    assert '"SPPS_HOSTED_RLS": "true"' in hosted
    assert '"SPPS_SUPABASE_AUTH_ENABLED": "true"' in hosted
    assert "sslmode" in hosted and "Clinical and identity connections must use distinct" in hosted

def test_registration_defaults_and_sql_are_pending_and_private():
    model=(ROOT/"server/models.py").read_text()
    sql=(ROOT/"migrations/0026_pending_approval_free_launch.sql").read_text()
    code=(ROOT/"server/subscription_routes.py").read_text()
    assert "default='pending_approval'" in model
    assert "status='pending_approval'" in sql and "'pending_approval',p_auth_uid::text" in sql
    assert "FALSE,'free',FALSE" in sql
    assert "GRANT EXECUTE" in sql and "REVOKE ALL" in sql
    assert "if free_access.enabled() and os.getenv('MODEL_C_ENV','local').lower() in ('staging','production')" in code

def test_owner_claim_requires_verified_email_not_google_or_mfa():
    src=(ROOT/"server/main.py").read_text()
    verifier=(ROOT/"server/supabase_practitioner_auth.py").read_text()
    assert "verify_access_token(payload.access_token,password_required=True)" in src
    assert 'user.get("email_confirmed_at")' in verifier
    assert "claim_verified_platform_owner" in src
    assert "if identity.get('aal')!='aal2':" not in src
    assert "verify_production(app)" in src
