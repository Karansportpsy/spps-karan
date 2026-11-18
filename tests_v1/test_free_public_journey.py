"""Free SPPS public onboarding smoke test; all identities and records fictional."""
from io import BytesIO
import base64
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support.app import create_app
from server.models import Practitioner, PractitionerProfile

def test_free_registration_is_pending_and_directory_does_not_publish_it(tmp_path,monkeypatch):
    monkeypatch.setenv("MODEL_C_ENV","local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE","true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED","false")
    app=create_app(f"sqlite:///{tmp_path/'v1-free-review.db'}",
                   init_database=True,seed_demo=True)
    visitor=TestClient(app)
    buf=BytesIO()
    Image.new("RGB",(80,80),(24,110,132)).save(buf,"PNG")
    data={
        "name":"Fictional Sport Psychology Applicant",
        "email":"review-applicant-2026@example.com",
        "password":"FictionalPassword!2026",
        "qualifications":"Synthetic MSc in Sport Psychology",
        "experience_years":5,
        "achievements":"Synthetic credential claim",
        "specialisms":"Performance and injury rehabilitation",
        "consultation_fee_paise":0,
        "plan":"free",
        "photo_base64":"data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode(),
        "terms_accepted":True,
    }
    created=visitor.post("/api/public/practitioner-registration",json=data)
    assert created.status_code==201,created.text
    assert created.json()["status"]=="pending_approval"
    denied=visitor.post("/api/auth/login",json={
        "email":data["email"],"password":data["password"],"terms_accepted":True,
    })
    assert denied.status_code==403 and "pending verification" in denied.text
    with app.state.DB() as db:
        p=db.scalar(select(Practitioner).where(Practitioner.email==data["email"]))
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==p.id))
        assert not p.active and not p.is_admin and p.tier=="free"
        assert profile.status=="pending_approval"
        assert not profile.credentials_verified and not profile.public_approved
        assert profile.email_verified_at is None
        applicant_id=p.id
    public=visitor.get("/api/public/practitioners")
    assert public.status_code==200
    assert applicant_id not in {x["id"] for x in public.json()}
    owner=TestClient(app)
    owner_auth=owner.post("/api/auth/login",json={
        "email":"practitioner@modelc.test","password":"DemoOnly!2026",
        "terms_accepted":True,
    })
    assert owner_auth.status_code==200,owner_auth.text
    hdr={"X-CSRF-Token":owner_auth.json()["csrf"]}
    queue=owner.get("/api/admin/practitioner-reviews")
    assert queue.status_code==200
    assert any(x["practitioner_id"]==applicant_id for x in queue.json())
    premature=owner.post(f"/api/admin/practitioners/{applicant_id}/approval",
                         json={"decision":"approve","note":"Credential review example"},
                         headers=hdr)
    assert premature.status_code==409  # identity verification is mandatory
    # No subscription, payment or self-approval can clear the pending gate.
    assert visitor.post("/api/public/practitioner-registration",
                        json={**data,"plan":"pro","consultation_fee_paise":49900}).status_code==422
