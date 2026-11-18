"""Scoped clinical parity against newly provisioned fictional PostgreSQL only.

A true physical Android or hosted Supabase acceptance test is still required.
"""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text, select
from sqlalchemy.exc import DBAPIError

from server.identity import bind_identity
from server.models import Practitioner


def sign_in(client, env, who="alice"):
    response = client.post("/api/auth/login", json={
        "email": f"{who}@spps.test",
        "password": env[f"SPPS_DEV_{who.upper()}_PASSWORD"],
        "terms_accepted": True,
    })
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def create_case(client):
    athlete = client.post("/api/athletes", json={
        "code": uuid4().hex[:20], "display_name": "Fictional clinical parity athlete",
        "sport": "Badminton", "service_consent_recorded": True,
    })
    assert athlete.status_code == 201, athlete.text
    aid = athlete.json()["id"]
    case = client.post("/api/cases", json={
        "athlete_id": aid, "title": "Fictional performance routine",
    })
    assert case.status_code == 201, case.text
    return aid, case.json()["id"]


def test_notes_interventions_reports_injury_roundtrip_and_isolation(app, integration_env):
    assert integration_env["SPPS_DEV_CLINICAL_PARITY"] == "true"
    with TestClient(app) as windows, TestClient(app) as android, TestClient(app) as bob:
        sign_in(windows, integration_env)
        sign_in(android, integration_env)
        sign_in(bob, integration_env, "bob")
        aid, cid = create_case(windows)

        # Existing frontend/backend code: no parallel Android-only implementation.
        note = android.post("/api/notes", json={
            "case_id": cid, "note_date": "2026-11-09",
            "content": "Fictional consultation: attention reset cue tested.",
        })
        assert note.status_code == 201, note.text
        nid = note.json()["id"]
        assert any(row["id"] == nid for row in windows.get(f"/api/cases/{cid}").json()["notes"])
        finished = windows.post(f"/api/notes/{nid}/finalize", json={"expected_revision": 1})
        assert finished.status_code == 200, finished.text
        assert finished.json()["state"] == "final"
        assert android.post(f"/api/notes/{nid}/finalize", json={"expected_revision": 1}).status_code == 409

        interventions = windows.get("/api/interventions")
        assert interventions.status_code == 200
        assert interventions.json(), "Local fictional intervention seed missing"
        iid = interventions.json()[0]["id"]
        assignment = windows.post("/api/assignments", json={
            "athlete_id": aid, "case_id": cid, "intervention_id": iid,
            "dosage": "Fictional only: two practice repetitions",
        })
        assert assignment.status_code == 201, assignment.text
        assert any(row["id"] == assignment.json()["id"]
                   for row in android.get(f"/api/cases/{cid}").json()["assignments"])

        draft = android.post("/api/reports", json={
            "athlete_id": aid, "case_id": cid, "title": "Fictional progress review",
            "audience": "practitioner", "body": "A fictional practitioner-only draft for test.",
        })
        assert draft.status_code == 201, draft.text
        rid = draft.json()["id"]
        assert any(r["id"] == rid for r in windows.get(f"/api/athletes/{aid}/reports").json())
        # Exports remain closed and Pro claims are not broadened by this test.
        assert windows.get(f"/api/reports/{rid}/export?format=pdf").status_code == 503

        injury = windows.post("/api/injury/episodes", json={
            "athlete_id": aid, "case_id": cid, "injury_label": "Fictional ankle rehab",
            "start_date": "2026-11-01", "stage": "early rehabilitation",
        })
        assert injury.status_code == 201, injury.text
        eid = injury.json()["id"]
        checkin = android.post(f"/api/injury/episodes/{eid}/checkins", json={
            "observed_on": "2026-11-09", "pain": 2, "fear_of_reinjury": 4,
            "confidence": 6, "coping": 7, "note": "Fictional monitoring",
        })
        assert checkin.status_code == 201, checkin.text
        assert len(windows.get(f"/api/athletes/{aid}/injury").json()[0]["checkins"]) == 1

        # Same tenant is insufficient: practitioner Bob must never see Alice's rows.
        for suffix in ("", "/reports", "/injury", "/observations", "/assessments/offline"):
            assert bob.get(f"/api/athletes/{aid}{suffix}").status_code == 404
        assert bob.get(f"/api/cases/{cid}").status_code == 404
        assert bob.post("/api/notes", json={
            "case_id": cid, "note_date": "2026-11-09",
            "content": "Invalid cross-practitioner clinical note",
        }).status_code == 404
        assert bob.post(f"/api/notes/{nid}/finalize", json={"expected_revision": 1}).status_code == 404
        assert bob.post("/api/assignments", json={
            "athlete_id": aid, "case_id": cid, "intervention_id": iid,
            "dosage": "Cross-user attempt",
        }).status_code == 404
        assert bob.post("/api/reports", json={
            "athlete_id": aid, "case_id": cid, "title": "Cross-user attempt",
            "body": "Invalid cross-practitioner report draft",
        }).status_code == 404
        assert bob.post("/api/injury/episodes", json={
            "athlete_id": aid, "case_id": cid, "injury_label": "Cross-user attempt",
            "start_date": "2026-11-01", "stage": "early rehabilitation",
        }).status_code == 404
        assert bob.post(f"/api/injury/episodes/{eid}/checkins", json={
            "observed_on": "2026-11-09", "pain": 1,
            "fear_of_reinjury": 1, "confidence": 1, "coping": 1,
        }).status_code == 404
        assert bob.post("/api/public/practitioner-registration", json={}).status_code == 503
        assert bob.post("/api/payment/razorpay/webhook", json={}).status_code == 503

    # Process/connection restart must not lose a clinical record in PostgreSQL.
    app.state.engine.dispose()
    app.state.auth_engine.dispose()
    from test_support.app import create_app
    fresh_app = create_app(init_database=False, seed_demo=False)
    try:
        with TestClient(fresh_app) as fresh:
            sign_in(fresh, integration_env)
            assert any(n["id"] == nid and n["state"] == "final"
                       for n in fresh.get(f"/api/cases/{cid}").json()["notes"])
            assert any(r["id"] == rid for r in fresh.get(f"/api/athletes/{aid}/reports").json())
            assert len(fresh.get(f"/api/athletes/{aid}/injury").json()[0]["checkins"]) == 1
    finally:
        fresh_app.state.engine.dispose()
        fresh_app.state.auth_engine.dispose()


def test_database_rejects_foreign_clinical_inserts_even_with_forged_ids(app, integration_env):
    with TestClient(app) as alice, TestClient(app) as bob:
        sign_in(alice, integration_env)
        sign_in(bob, integration_env, "bob")
        own_aid, own_cid = create_case(alice)
        other_aid, other_cid = create_case(bob)
        iid = alice.get("/api/interventions").json()[0]["id"]
        other_episode = bob.post("/api/injury/episodes", json={
            "athlete_id": other_aid, "case_id": other_cid,
            "injury_label": "Fictional foreign episode",
            "start_date": "2026-11-01", "stage": "early rehabilitation",
        }).json()["id"]

    with app.state.AuthDB() as auth:
        user = auth.scalar(select(Practitioner).where(Practitioner.email == "alice@spps.test"))
        pid, tid = user.id, user.tenant_id

    commands = [
        ("INSERT INTO notes (tenant_id,case_id,author_id,note_date,content,state,revision,created_at)"
         " VALUES (:t,:foreign,:p,'2026-11-09','forged','draft',1,now())",
         {"t": tid, "foreign": other_cid, "p": pid}),
        ("INSERT INTO assignments (tenant_id,athlete_id,case_id,intervention_id,dosage,status,assigned_at)"
         " VALUES (:t,:a,:c,:i,'forged','assigned',now())",
         {"t": tid, "a": other_aid, "c": other_cid, "i": iid}),
        ("INSERT INTO reports (tenant_id,athlete_id,case_id,title,audience,body,status,created_at)"
         " VALUES (:t,:a,:c,'forged','practitioner','forged report','draft',now())",
         {"t": tid, "a": other_aid, "c": other_cid}),
        ("INSERT INTO injury_checkins (episode_id,observed_on,pain,fear_of_reinjury,confidence,coping,note)"
         " VALUES (:e,'2026-11-09',1,1,1,1,'forged')",
         {"e": other_episode}),
    ]
    for query, params in commands:
        with app.state.DB() as db:
            db.info["identity"] = (pid, tid)
            with __import__("pytest").raises(DBAPIError) as failure:
                db.execute(text(query), params)
            assert failure.value.orig.sqlstate == "42501"
