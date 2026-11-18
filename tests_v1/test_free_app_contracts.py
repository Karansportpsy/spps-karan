"""SPPS 1.0 free release: fail-closed source and web/Android parity contracts.

These tests are review-only and do not claim hosted production readiness.
"""
from pathlib import Path
import ast
ROOT=Path(__file__).resolve().parents[1]

def read(path):
    return (ROOT/path).read_text(encoding="utf-8")

def test_python_modules_parse():
    for path in (ROOT/"server").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"),filename=str(path))

def test_account_approval_and_free_registration_cannot_bypass_verification():
    subscription=read("server/subscription_routes.py")
    assert "pending_approval" in subscription
    assert "active=False,is_admin=False" in subscription
    assert "sbauth.verify_access_token" in subscription
    assert "register_verified_free_practitioner" in subscription
    migration=read("migrations/0021_pending_practitioner_registration.sql")
    assert "FALSE,'free',FALSE" in migration
    assert "'pending'" in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "GRANT EXECUTE" in migration

def test_owner_only_review_rejects_unverified_identity_and_self_publication():
    review=read("server/practitioner_review.py")
    assert "administrator" in review.lower() or "is_admin" in review
    assert "email_verified_at" in review
    assert "approval_decision" in review
    assert "approved" in review
    auth=read("server/supabase_practitioner_auth.py")
    # v1.0 contract: verified email + password (AAL1); no social or authenticator sign-in.
    assert "email_confirmed_at" in auth
    assert "password_required" in auth and '"password"' in auth
    assert "/auth/v1/user" in auth

def test_free_commercial_contract_and_private_reports():
    from server.free_access import free_tier_response
    features=free_tier_response()
    assert features["tier"]=="free"
    assert features["prices_inr_per_month"]=={}
    assert features["features"]["report_exports"] is True
    assert features["features"]["ai"] is False
    assert not features["payment_required"]
    assert "case_for(db,user,r.case_id)" in read("server/main.py")
    assert "410" in read("server/beta_routes.py")

def test_web_android_assets_match_current_main():
    for web,android in (
       ("web/app.js","www/assets/app.js"),
       ("web/portal.js","www/assets/portal.js"),
       ("web/styles.css","www/assets/styles.css"),
       ("web/index.html","www/index.html"),
       ("web/portal.html","www/portal.html"),
    ):
        assert (ROOT/web).read_bytes()==(ROOT/android).read_bytes()
    assert "button('Logout','logout'" in read("web/app.js")
    assert "authHelpButtons()" in read("web/app.js")
    assert "SPPS Version 1.0 - Free" in read("web/index.html")

def test_legacy_pr_registration_sql_is_not_on_active_release_path():
    # Old review migration inserted active Core users and may not be adopted.
    assert not (ROOT/"migrations/0019_clinical_free_public_access.sql").exists()
    assert not (ROOT/"migrations/0020_admin_review_owner_rls.sql").exists()
    assert "0021_pending_practitioner_registration.sql" in read("server/release_migrations.py")
