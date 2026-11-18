"""Directory inquiry must route to selected verified practitioner without exposing contacts."""
import json
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_support.app import create_app
from server.models import Inquiry, Practitioner, PractitionerProfile, Service


def test_free_practitioner_inquiry_is_private_and_targeted(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_C_ENV", "local")
    monkeypatch.setenv("MODEL_C_INTEGRATION_DEV", "false")
    monkeypatch.setenv("SPPS_HOSTED_RLS", "false")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    app = create_app(f"sqlite:///{tmp_path / 'free-inquiry.db'}", init_database=True, seed_demo=True)
    client = TestClient(app)
    with app.state.DB() as db:
        practitioner = db.scalar(select(Practitioner).where(Practitioner.email == "practitioner@modelc.test"))
        profile = db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id == practitioner.id))
        profile.public_approved = True
        profile.credentials_verified = True
        service = db.scalar(select(Service).where(Service.active == True))
        pid, sid = practitioner.id, service.id
        db.commit()
    directory = client.get("/api/public/practitioners")
    assert directory.status_code == 200
    assert any(row["id"] == pid for row in directory.json())
    request = {
        "service_id": sid, "practitioner_id": pid,
        "contact_name": "Fictional Client",
        "contact_email": "fictional.client@example.com",
        "enquiring_for": "myself", "sport": "Football",
        "support_topic": "Confidence", "contact_preference": "email",
        "scheduling_note": "Next week, please.", "consent": True,
    }
    sent = client.post("/api/public/enquiries", json=request)
    assert sent.status_code == 201, sent.text
    assert sent.json()["accepted"]
    with app.state.DB() as db:
        q = db.scalar(select(Inquiry).where(Inquiry.contact_email == request["contact_email"]))
        assert q is not None and q.practitioner_id == pid
    assert request["contact_email"] not in json.dumps(directory.json())
    with app.state.DB() as db:
        profile = db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id == pid))
        profile.public_approved = False
        db.commit()
    rejected = client.post("/api/public/enquiries", json=request)
    assert rejected.status_code == 404, rejected.text
    app.state.engine.dispose()
