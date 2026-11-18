"""Free Beta v1.0 policy contract, separate from hosted-release approval."""
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from server import free_access
from server.subscription_routes import RegistrationIn, active_membership


def registration(plan="free", amount=0):
    return {
        "name": "Fictional Reviewer",
        "email": "reviewer@example.com",
        "password": "fictional-strong-password",
        "qualifications": "Fictional MSc Sports Psychology",
        "experience_years": 5,
        "achievements": "",
        "specialisms": "Injury psychology",
        "consultation_fee_paise": amount,
        "plan": plan,
        "photo_base64": "A" * 30,
        "terms_accepted": True,
    }


def test_free_practitioner_access_does_not_need_subscription(monkeypatch):
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    assert active_membership(None, SimpleNamespace(active=True, tier="free"))
    assert not active_membership(None, SimpleNamespace(active=False, tier="free"))


def test_free_registration_requires_zero_charge(monkeypatch):
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    assert RegistrationIn.model_validate(registration()).consultation_fee_paise == 0
    with pytest.raises(ValidationError):
        RegistrationIn.model_validate(registration("pro", 49900))
    with pytest.raises(ValidationError):
        RegistrationIn.model_validate(registration("free", 10000))


def test_free_workspace_features_are_not_tier_gated(monkeypatch):
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    response = free_access.free_tier_response()
    assert response["tier"] == "free"
    assert response["amount_paise"] == 0
    assert response["subscription_required"] is False
    assert response["payment_required"] is False
    for key in ("assessments", "report_exports", "injury_psychology", "bookings", "video"):
        assert response["features"][key] is True
    assert response["features"]["ai"] is False

def test_free_signup_and_login_are_payment_free(tmp_path, monkeypatch):
    """Disposable SQLite identity proves no subscription or checkout is needed."""
    from fastapi.testclient import TestClient
    from test_support.app import create_app
    from server.models import Practitioner, PractitionerSubscription
    from sqlalchemy import select
    from PIL import Image
    import io, base64
    monkeypatch.setenv("MODEL_C_ENV", "local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    monkeypatch.setenv("MODEL_C_PAYMENT_TEST_MODE", "false")
    app = create_app(f"sqlite:///{tmp_path / 'free-v1.db'}", init_database=True, seed_demo=True)
    c = TestClient(app)
    img = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 150, 155)).save(img, format="PNG")
    payload = registration()
    payload["photo_base64"] = "data:image/png;base64," + base64.b64encode(img.getvalue()).decode()
    response = c.post("/api/public/practitioner-registration", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending_approval"
    assert response.json()["amount_paise"] == 0
    assert not response.json()["directory_visible"]
    auth = c.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"], "terms_accepted": True})
    assert auth.status_code == 403 and "pending verification" in auth.text
    with app.state.DB() as db:
        person = db.scalar(select(Practitioner).where(Practitioner.email == payload["email"]))
        assert person and person.tier == "free" and person.active is False
        from server.models import PractitionerProfile
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == person.id))
        assert profile.status == "pending_approval"
        assert not profile.public_approved and not profile.credentials_verified
        assert db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id == person.id)) is None
    assert c.post("/api/public/practitioner-registration/checkout", json={"registration_token": "X" * 40}).status_code == 410
