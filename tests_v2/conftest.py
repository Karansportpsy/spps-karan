"""v2 API tests on a disposable SQLite file. Fictional data only.

Supabase Auth is replaced by ``FakeSupabase``: tests register a fake access token for a fictional account and the
server's GoTrue check (``server.supabase_practitioner_auth.verify_access_token``) answers from that table. The real
check is covered by the v1 tests; these tests exercise what the server does with a verified identity.
"""
from __future__ import annotations

import base64
import io
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

OWNER_LOGIN = {"email": "practitioner@modelc.test", "password": "DemoOnly!2026", "terms_accepted": True}
PRACTITIONER_B = {"email": "rohan@modelc.test", "password": "DemoOnly!2026", "terms_accepted": True}
PRACTITIONER_C = {"email": "kavya@modelc.test", "password": "DemoOnly!2026", "terms_accepted": True}


class FakeSupabase:
    def __init__(self):
        self.accounts: dict[str, dict] = {}

    def account(self, email: str, *, confirmed=True, metadata=None, password=True) -> str:
        token = "h." + uuid.uuid4().hex * 3 + ".s"
        self.accounts[token] = {"uid": str(uuid.uuid4()), "email": email.lower(), "confirmed": confirmed,
                                "metadata": metadata or {}, "password": password}
        return token

    def same_account_new_session(self, token: str, *, password=False) -> str:
        """The same Supabase account seen from another browser (a new access token, same subject)."""
        new = "h." + uuid.uuid4().hex * 3 + ".s"
        self.accounts[new] = {**self.accounts[token], "password": password}
        return new

    def confirm(self, token: str) -> None:
        for acc in self.accounts.values():
            if acc["uid"] == self.accounts[token]["uid"]:
                acc["confirmed"] = True

    def verify(self, token: str, *, password_required: bool = False) -> dict:
        acc = self.accounts.get(token)
        if not acc:
            raise HTTPException(401, "Supabase authentication failed")
        if not acc["confirmed"]:
            raise HTTPException(403, "Verify your email before requesting practitioner approval")
        if password_required and not acc["password"]:
            raise HTTPException(403, "Sign in with email and password")
        return {"uid": acc["uid"], "email": acc["email"], "email_confirmed_at": "2026-11-17T00:00:00Z",
                "aal": "aal1", "provider": "email", "user_metadata": acc["metadata"]}


@pytest.fixture
def fake_supabase(monkeypatch):
    fake = FakeSupabase()
    import server.supabase_practitioner_auth as sbauth
    import server.practitioner_review as review
    monkeypatch.setattr(sbauth, "verify_access_token", fake.verify)
    monkeypatch.setattr(review, "verify_access_token", fake.verify)
    return fake


@pytest.fixture
def v2env(monkeypatch):
    monkeypatch.setenv("MODEL_C_ENV", "local")
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    monkeypatch.setenv("SPPS_SUPABASE_AUTH_ENABLED", "false")
    monkeypatch.setenv("SPPS_BOT_MIN_FILL_SECONDS", "0")
    # Fictional field-encryption key so every v2 test runs with notes and formulations encrypted at rest.
    monkeypatch.setenv("SPPS_FIELD_KEYS", "k1:" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=")
    monkeypatch.setenv("SPPS_FIELD_KEY_CURRENT", "k1")
    monkeypatch.setenv("SPPS_ACCOUNT_LIFECYCLE_ENABLED", "false")
    for key in ("SPPS_SMTP_HOST", "SPPS_SMTP_USERNAME", "SPPS_SMTP_PASSWORD", "SPPS_SMTP_FROM",
                "SPPS_AI_FEATURES_ENABLED", "SPPS_ATHLETE_LOOP_ENABLED", "SPPS_DIRECTORY_ENABLED",
                "SPPS_PORTAL_ENABLED", "SPPS_SELF_SERVE_ENABLED", "SPPS_REPORT_LINKS_ENABLED",
                "SPPS_PRACTITIONER_CLOSURE_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    from server.v2 import guards
    guards.reset()
    yield monkeypatch
    guards.reset()


@pytest.fixture
def app(v2env, tmp_path, fake_supabase):
    from test_support.app import create_app
    application = create_app(f"sqlite:///{tmp_path / 'v2.db'}", init_database=True, seed_demo=True)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app)


def login(client: TestClient, creds: dict) -> dict:
    res = client.post("/api/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf"]}


def fresh_client(app) -> TestClient:
    """A separate browser: its own cookie jar."""
    return TestClient(app)


def photo_b64() -> str:
    from PIL import Image
    img = Image.new("RGB", (64, 64), (40, 90, 140))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def form_token(client: TestClient) -> str:
    return client.get("/api/public/form-token").json()["form_token"]


APPLICATION = {"name": "Dr Fictional Applicant", "qualifications": "MSc Sport Psychology (fictional)",
               "experience_years": 5, "achievements": "Fictional academy work", "specialisms": "Confidence",
               "terms_accepted": True}


def make_athlete(client, headers, code="FIC-1", name="Fictional Athlete One", sport="Badminton") -> int:
    res = client.post("/api/athletes", json={"code": code, "display_name": name, "sport": sport,
                                             "service_consent_recorded": True}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def make_case(client, headers, athlete_id, title="Fictional performance case") -> int:
    res = client.post("/api/cases", json={"athlete_id": athlete_id, "title": title}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def make_note(client, headers, case_id, content="Fictional consultation note text for tests.") -> int:
    res = client.post("/api/notes", json={"case_id": case_id, "note_date": "2026-11-17", "content": content},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.fixture(autouse=True)
def _capture_v2_mail(monkeypatch):
    """Tests never send e-mail: every v2 message is kept on app.state.v2_outbox for the assertions."""
    from server.v2 import athlete_loop

    def capture(app, to, subject, body):
        if getattr(app.state, "v2_outbox", None) is None:
            app.state.v2_outbox = []
        app.state.v2_outbox.append({"to": to, "subject": subject, "body": body})
        return True
    monkeypatch.setattr(athlete_loop, "_deliver", capture)
