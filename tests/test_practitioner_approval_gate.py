"""Fictional-only regression: administrator approval gates both clinic access and directory."""
import base64
import io
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support.app import create_app
from server.models import Practitioner, PractitionerProfile, now


def photo():
    output=io.BytesIO()
    Image.new("RGB",(80,80),(38,115,160)).save(output,format="PNG")
    return "data:image/png;base64,"+base64.b64encode(output.getvalue()).decode()


def test_review_approval_rejection_and_owner_only_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_C_ENV","local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE","true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED","false")
    app=create_app(f"sqlite:///{tmp_path/'approval.db'}",init_database=True,seed_demo=True)
    client=TestClient(app)
    applicant={
        "name":"Synthetic Practitioner Applicant",
        "email":"synthetic-applicant@example.com",
        "password":"FictionalPassword2026!",
        "qualifications":"Fictional MSc Sport Psychology",
        "experience_years":6,
        "achievements":"Fictional professional experience for tests",
        "specialisms":"Mental skills, injury recovery",
        "consultation_fee_paise":0,"plan":"free",
        "photo_base64":photo(),"terms_accepted":True,
    }
    created=client.post("/api/public/practitioner-registration",json=applicant)
    assert created.status_code==201,created.text
    assert created.json()["status"]=="pending_approval"
    denied=client.post("/api/auth/login",json={
        "email":applicant["email"],"password":applicant["password"],"terms_accepted":True})
    assert denied.status_code==403
    assert "pending verification" in denied.text
    with app.state.DB() as db:
        person=db.scalar(select(Practitioner).where(Practitioner.email==applicant["email"]))
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==person.id))
        applicant_id=person.id
        assert profile.status=="pending_approval" and not person.active
    assert not any(x["id"]==applicant_id for x in client.get("/api/public/practitioners").json())
    assert client.get("/admin").status_code==401

    owner=client.post("/api/auth/login",json={
        "email":"practitioner@modelc.test","password":"DemoOnly!2026","terms_accepted":True})
    assert owner.status_code==200,owner.text
    csrf={"X-CSRF-Token":owner.json()["csrf"]}
    pending=client.get("/api/admin/practitioner-reviews")
    assert pending.status_code==200
    assert any(row["practitioner_id"]==applicant_id and row["status"]=="pending_approval" for row in pending.json())
    blocked=client.post(f"/api/admin/practitioners/{applicant_id}/approval",
        json={"decision":"approve","note":"Verified professional credentials"},headers=csrf)
    assert blocked.status_code==409
    with app.state.DB() as db:
        profile=db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id==applicant_id))
        profile.email_verified_at=now()
        profile.supabase_auth_uid="123e4567-e89b-12d3-a456-426614174000"
        db.commit()
    approved=client.post(f"/api/admin/practitioners/{applicant_id}/approval",
        json={"decision":"approve","note":"Email and qualifications verified independently"},headers=csrf)
    assert approved.status_code==200,approved.text
    assert approved.json()["status"]=="approved" and approved.json()["listed"]
    assert approved.json()["notification_sent"] is False

    client.post("/api/auth/logout",headers=csrf)
    auth=client.post("/api/auth/login",json={
        "email":applicant["email"],"password":applicant["password"],"terms_accepted":True})
    assert auth.status_code==200,auth.text
    assert client.get("/admin").status_code==403
    client.post("/api/auth/logout",headers={"X-CSRF-Token":auth.json()["csrf"]})
    owner=client.post("/api/auth/login",json={
        "email":"practitioner@modelc.test","password":"DemoOnly!2026","terms_accepted":True})
    csrf={"X-CSRF-Token":owner.json()["csrf"]}
    rejected=client.post(f"/api/admin/practitioners/{applicant_id}/approval",
        json={"decision":"reject","note":"Professional verification withdrawn"},headers=csrf)
    assert rejected.status_code==200
    assert rejected.json()["status"]=="rejected" and not rejected.json()["listed"]
    assert client.post("/api/auth/login",json={
        "email":applicant["email"],"password":applicant["password"],"terms_accepted":True}).status_code==403
