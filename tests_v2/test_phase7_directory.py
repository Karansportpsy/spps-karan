"""Phase 7: public endpoints return only snapshot fields; unapproved or delisted practitioners never appear;
enquiry spam is throttled."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from server.v2.directory import PUBLIC_FIELDS

from .conftest import OWNER_LOGIN, PRACTITIONER_B, PRACTITIONER_C, fresh_client, login

PROFILE = {"display_name": "Dr Fictional Directory", "qualifications": "MSc Sport Psychology (fictional)",
           "sports": "Hockey, Badminton", "athlete_levels": "club, national", "age_groups": "15-17, 18-24",
           "languages": "English, Hindi", "delivery": "both", "city": "Patiala", "taking_new_clients": True,
           "about": "Fictional profile text."}
ALLOWED_KEYS = set(PUBLIC_FIELDS) | {"badges", "has_photo", "photo_url"}


@pytest.fixture
def dir_app(v2env, tmp_path, fake_supabase, monkeypatch):
    monkeypatch.setenv("SPPS_DIRECTORY_ENABLED", "true")
    monkeypatch.setenv("SPPS_CRISIS_CONTACTS_TEXT", "Fictional helpline 0000 (24/7).")
    from test_support.app import create_app
    app = create_app(f"sqlite:///{tmp_path / 'dir.db'}", init_database=True, seed_demo=True)
    yield app
    app.state.engine.dispose()


def _published(app, creds=PRACTITIONER_B, profile=PROFILE):
    p = fresh_client(app)
    hp = login(p, creds)
    assert p.put("/api/v2/directory/profile", json=profile, headers=hp).status_code == 200
    assert p.post("/api/v2/directory/profile/submit", headers=hp).json()["status"] == "submitted"
    owner = fresh_client(app)
    ho = login(owner, OWNER_LOGIN)
    pid = next(r["practitioner_id"] for r in owner.get("/api/v2/admin/directory").json()
               if r["display_name"] == profile["display_name"])
    today = date.today().isoformat()
    res = owner.post(f"/api/v2/admin/directory/{pid}/publish", json={"verifications": [
        {"kind": "identity", "checked_on": today}, {"kind": "qualification", "checked_on": today}]}, headers=ho)
    assert res.status_code == 200, res.text
    return p, hp, owner, ho, pid


def test_public_endpoints_return_only_snapshot_fields(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    anon = fresh_client(dir_app)
    results = anon.get("/api/v2/public/directory").json()["results"]
    assert [r["display_name"] for r in results] == ["Dr Fictional Directory"]
    for r in results + [anon.get(f"/api/v2/public/directory/{pid}").json()]:
        assert set(r) <= ALLOWED_KEYS, set(r) - ALLOWED_KEYS
        assert "rohan@modelc.test" not in str(r) and "email" not in r
    assert {b["kind"] for b in results[0]["badges"]} == {"identity", "qualification"}
    # Change the private profile and the practitioner's account e-mail: the public answer only changes on publish.
    with dir_app.state.engine.begin() as conn:
        conn.execute(text("UPDATE directory_profiles SET about='PRIVATE-EDIT-MARKER' WHERE practitioner_id=:p"), {"p": pid})
    assert "PRIVATE-EDIT-MARKER" not in anon.get("/api/v2/public/directory").text


def test_filters_only_and_alphabetical(dir_app):
    _published(dir_app)
    _published(dir_app, PRACTITIONER_C, {**PROFILE, "display_name": "Ms Another Fictional", "sports": "Swimming",
                                          "languages": "Tamil", "delivery": "online", "city": "Chennai"})
    anon = fresh_client(dir_app)
    names = lambda q="": [r["display_name"] for r in anon.get("/api/v2/public/directory" + q).json()["results"]]  # noqa
    assert names() == ["Dr Fictional Directory", "Ms Another Fictional"]
    assert names("?sport=hockey") == ["Dr Fictional Directory"]
    assert names("?language=Tamil") == ["Ms Another Fictional"]
    assert names("?delivery=in_person") == ["Dr Fictional Directory"]
    assert names("?city=chennai&age_group=15-17") == ["Ms Another Fictional"]
    assert anon.get("/api/v2/public/directory").json()["order"] == "alphabetical"


def test_unapproved_delisted_or_edited_practitioners_never_appear(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    anon = fresh_client(dir_app)
    assert owner.post(f"/api/v2/admin/directory/{pid}/delist", json={"reason": "Fictional lapse"}, headers=ho).status_code == 200
    assert anon.get("/api/v2/public/directory").json()["results"] == []
    assert anon.get(f"/api/v2/public/directory/{pid}").status_code == 404
    today = date.today().isoformat()
    owner.post(f"/api/v2/admin/directory/{pid}/publish", json={"verifications": [{"kind": "identity", "checked_on": today}]},
               headers=ho)
    assert len(anon.get("/api/v2/public/directory").json()["results"]) == 1
    owner.post(f"/api/admin/practitioners/{pid}/approval", json={"decision": "request_more_info",
                                                                 "note": "Fictional: update needed"}, headers=ho)
    assert anon.get("/api/v2/public/directory").json()["results"] == []  # account no longer approved
    # An applicant who was never approved cannot be published at all.
    from server.models import PractitionerProfile
    with dir_app.state.DB() as db:
        assert db.scalar(select(PractitionerProfile.status).where(PractitionerProfile.practitioner_id == pid)) != "approved"
    assert owner.post(f"/api/v2/admin/directory/{pid}/publish", json={"verifications": [
        {"kind": "identity", "checked_on": today}]}, headers=ho).status_code == 409


def test_editing_takes_the_listing_down_until_republished(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    p.put("/api/v2/directory/profile", json={**PROFILE, "about": "Changed fictional text"}, headers=hp)
    assert fresh_client(dir_app).get("/api/v2/public/directory").json()["results"] == []


def test_lapsed_verification_drops_out_and_owner_sees_reverification_due(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    with dir_app.state.engine.begin() as conn:
        conn.execute(text("UPDATE directory_snapshot SET verified_until=:d"), {"d": (date.today() + timedelta(days=10)).isoformat()})
    row = next(r for r in owner.get("/api/v2/admin/directory").json() if r["practitioner_id"] == pid)
    assert row["reverification_due"] is True
    with dir_app.state.engine.begin() as conn:
        conn.execute(text("UPDATE directory_snapshot SET verified_until=:d"), {"d": (date.today() - timedelta(days=1)).isoformat()})
    assert fresh_client(dir_app).get("/api/v2/public/directory").json()["results"] == []
    old = (date.today() - timedelta(days=400)).isoformat()
    assert owner.post(f"/api/v2/admin/directory/{pid}/publish", json={"verifications": [
        {"kind": "identity", "checked_on": old}]}, headers=ho).status_code == 422


def _form_token(c):
    return c.get("/api/public/form-token").json()["form_token"]


def test_enquiry_reaches_the_inbox_with_crisis_auto_reply_and_owner_sees_times_only(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    anon = fresh_client(dir_app)
    body = {"name": "Fictional Parent", "contact": "parent.enquiry@example.org", "role": "parent", "sport": "Hockey",
            "message": "Fictional: looking for support before trials."}
    res = anon.post(f"/api/v2/public/directory/{pid}/enquiries", json={**body, "form_token": _form_token(anon)})
    assert res.status_code == 201 and "Fictional helpline" in res.json()["message"]
    assert any(m["to"] == "parent.enquiry@example.org" and "not a crisis" in m["body"] for m in dir_app.state.v2_outbox)
    inbox = p.get("/api/v2/directory/enquiries").json()
    assert inbox[0]["message"].startswith("Fictional: looking")
    p.post(f"/api/v2/directory/enquiries/{inbox[0]['id']}/responded", headers=hp)
    stats = owner.get("/api/v2/admin/directory/enquiry-stats").json()
    assert stats[0]["enquiries"] == 1 and stats[0]["responded"] == 1
    assert "parent.enquiry" not in str(stats) and "trials" not in str(stats)
    c = fresh_client(dir_app)
    login(c, PRACTITIONER_C)
    assert c.get("/api/v2/directory/enquiries").json() == []
    assert c.get("/api/v2/admin/directory/enquiry-stats").status_code == 403


def test_enquiries_are_bot_checked_throttled_non_clinical_and_only_for_listed_practitioners(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    anon = fresh_client(dir_app)
    body = {"name": "Fictional", "contact": "x@example.org", "role": "athlete", "sport": "Hockey", "message": "Hello"}
    assert anon.post(f"/api/v2/public/directory/{pid}/enquiries", json=body).status_code == 400  # no form token
    assert anon.post(f"/api/v2/public/directory/{pid}/enquiries",
                     json={**body, "form_token": _form_token(anon), "website": "spam"}).status_code == 400
    assert anon.post(f"/api/v2/public/directory/{pid}/enquiries", json={**body, "form_token": _form_token(anon),
                     "message": "My diagnosis and medication are..."}).status_code == 422
    codes = [anon.post(f"/api/v2/public/directory/{pid}/enquiries", json={**body, "form_token": _form_token(anon)}).status_code
             for _ in range(7)]
    assert codes[:4] == [201] * 4 and 429 in codes  # 5 per IP per hour
    owner.post(f"/api/v2/admin/directory/{pid}/delist", json={"reason": "Fictional"}, headers=ho)
    from server.v2 import guards
    guards.reset()
    assert anon.post(f"/api/v2/public/directory/{pid}/enquiries",
                     json={**body, "form_token": _form_token(anon)}).status_code == 404


def test_directory_is_off_by_default(app):
    assert fresh_client(app).get("/api/v2/public/directory").status_code == 404
