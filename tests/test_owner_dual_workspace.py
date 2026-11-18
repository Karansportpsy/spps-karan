"""Verified owner dual-role contract; uses fictional local records only.

No real Supabase identity is created and no clinical data is accessed.
"""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from test_support.app import create_app
from server.models import Practitioner, PractitionerProfile, now
from server.owner_profile import (
    OWNER_EMAIL, OWNER_NAME, OWNER_ROLE, OWNER_PHOTO_URL,
    OWNER_EXPERIENCE_YEARS, is_featured_owner,
)

ROOT=Path(__file__).resolve().parents[1]


def test_owner_is_pinned_only_after_all_approval_gates(tmp_path,monkeypatch):
    monkeypatch.setenv("MODEL_C_ENV","local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE","true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED","false")
    app=create_app(f"sqlite:///{tmp_path/'owner-dual-role.db'}",
                   init_database=True,seed_demo=True)
    visitor=TestClient(app)
    original=visitor.get("/api/public/practitioners")
    assert original.status_code==200
    assert original.json() and original.json()[0]["featured_owner"] is False
    with app.state.DB() as db:
        user=db.scalar(select(Practitioner).where(
            Practitioner.email=="practitioner@modelc.test"))
        assert user is not None and user.is_admin
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==user.id))
        assert profile is not None
        user.email=OWNER_EMAIL
        user.name=OWNER_NAME
        user.active=True
        user.tier="free"
        profile.status="approved"
        profile.credentials_verified=True
        profile.public_approved=True
        profile.email_verified_at=now()
        profile.supabase_auth_uid="00000000-0000-4000-8000-000000000001"
        profile.experience_years=OWNER_EXPERIENCE_YEARS
        profile.qualifications="PhD (Sports Science) · MA (Sport Psychology)"
        owner_id=user.id
        db.commit()
    response=visitor.get("/api/public/practitioners")
    assert response.status_code==200
    items=response.json()
    assert items[0]["id"]==owner_id
    assert items[0]["featured_owner"] is True
    assert items[0]["name"]==OWNER_NAME
    assert items[0]["role"]==OWNER_ROLE
    assert items[0]["photo_url"]==OWNER_PHOTO_URL
    assert items[0]["experience_years"]==12
    assert "email" not in items[0] and "supabase_auth_uid" not in items[0]
    assert sum(1 for p in items if p["featured_owner"])==1
    with app.state.DB() as db:
        user=db.get(Practitioner,owner_id)
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==owner_id))
        user.is_admin=False
        db.commit()
    assert not any(p["featured_owner"] for p in visitor.get("/api/public/practitioners").json())
    with app.state.DB() as db:
        user=db.get(Practitioner,owner_id)
        user.is_admin=True
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==owner_id))
        profile.public_approved=False
        db.commit()
    assert owner_id not in {p["id"] for p in visitor.get("/api/public/practitioners").json()}
    with app.state.DB() as db:
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==owner_id))
        profile.public_approved=True
        profile.status="rejected"
        db.commit()
    assert owner_id not in {p["id"] for p in visitor.get("/api/public/practitioners").json()}


def test_verified_owner_claim_requires_confirmed_email_and_preserves_suspension():
    auth=(ROOT/"server/main.py").read_text(encoding="utf-8")
    verifier=(ROOT/"server/supabase_practitioner_auth.py").read_text(encoding="utf-8")
    sql=(ROOT/"migrations/0027_email_password_owner_approval.sql").read_text(encoding="utf-8")
    assert "verify_access_token(payload.access_token,password_required=True)" in auth
    assert 'user.get("email_confirmed_at")' in verifier
    assert "identity['email']=='karansportpsy@gmail.com'" in auth
    assert "claim_verified_platform_owner" in auth
    assert "response.set_cookie('modelc_session'" in auth
    assert "owner.verified_email_password_claim" in sql
    assert "Existing owner claim has a security or publication hold" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert "GRANT EXECUTE" in sql
    assert "public_approved=TRUE" in sql
    assert "credentials_verified=TRUE" in sql
    assert "0027_email_password_owner_approval.sql" in (ROOT/"server/release_migrations.py").read_text()

def test_dual_dashboard_buttons_are_admin_only_and_preserve_workspace():
    script=(ROOT/"web/app.js").read_text(encoding="utf-8")
    assert "function ownerDashboardSwitcher()" in script
    assert "if(!state.user?.is_admin)return '';" in script
    assert 'data-testid="dual-dashboard-switcher"' in script
    assert "button('Practitioner Workspace','view'" in script
    assert "button('Admin Dashboard'+(awaitingCount()" in script and "'view','spps-dashboard-tab'" in script
    assert "if(nextView==='admin'&&!state.user?.is_admin)" in script
    assert (ROOT/"web/app.js").read_bytes()==(ROOT/"www/assets/app.js").read_bytes()
    assert (ROOT/"web/styles.css").read_bytes()==(ROOT/"www/assets/styles.css").read_bytes()
