"""Phase 6 acceptance and negative tests (API). PostgreSQL row-level security is in tests_v2_pg/test_phase6_rls.py.

The v1 portal itself needs PostgreSQL, so here the portal switch is satisfied by patching ``flags.portal_enabled``;
the athlete-loop switch is the real environment flag. The PostgreSQL tier runs with both real flags.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from .conftest import OWNER_LOGIN, PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete
from .test_phase4_assessments import CUSTOM


@pytest.fixture
def loop_app(v2env, tmp_path, fake_supabase, monkeypatch):
    from server.v2 import flags
    monkeypatch.setenv("SPPS_ATHLETE_LOOP_ENABLED", "true")
    monkeypatch.setattr(flags, "portal_enabled", lambda: True)
    from test_support.app import create_app
    app = create_app(f"sqlite:///{tmp_path / 'loop.db'}", init_database=True, seed_demo=True)
    yield app
    app.state.engine.dispose()


def token_from(app, to, kind):
    for m in reversed(app.state.v2_outbox):
        if m["to"] == to:
            found = re.search(kind + r"=([A-Za-z0-9_-]+)", m["body"])
            if found:
                return found.group(1)
    raise AssertionError(f"no {kind} mail to {to}")


def portal_user(app, email, role, name="Fictional Person"):
    c = fresh_client(app)  # a separate browser, e.g. Chrome opened from the e-mail on Android
    assert c.post("/api/v2/portal/auth/register", json={"email": email, "password": "Fictional-pass-2026",
                                                        "display_name": name, "role": role,
                                                        "terms_accepted": True}).status_code == 202
    assert c.post("/api/v2/portal/auth/verify", json={"token": token_from(app, email, "verify")}).status_code == 200
    res = c.post("/api/v2/portal/auth/login", json={"email": email, "password": "Fictional-pass-2026"})
    assert res.status_code == 200, res.text
    return c, {"X-CSRF-Token": res.json()["csrf"]}


def adult_link(app, email="athlete.one@example.org"):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb, code="LOOP-1", name="Fictional Loop Athlete")
    inv = b.post(f"/api/v2/loop/athletes/{aid}/invite", json={"email": email}, headers=hb)
    assert inv.status_code == 201, inv.text
    token = token_from(app, email, "invite")
    a, ha = portal_user(app, email, "athlete")
    preview = a.get(f"/api/v2/portal/invites/{token}").json()
    assert preview["data"]["practitioner_name"] and preview["data"]["scope"]
    acc = a.post(f"/api/v2/portal/invites/{token}/accept", json={"consent": True}, headers=ha)
    assert acc.status_code == 200 and acc.json()["data"]["status"] == "active", acc.text
    return b, hb, aid, inv.json()["link_id"], a, ha


def test_invite_consent_assign_complete_and_practitioner_sees_it_on_the_timeline(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    inst = b.post("/api/v2/instruments", json=CUSTOM, headers=hb).json()
    assert b.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "instrument", "title": "Readiness",
                                                                     "instrument_id": inst["id"]}, headers=hb).status_code == 201
    assert b.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "programme", "title": "Breathing week",
                                                                     "tasks": ["Box breathing 5 min daily"],
                                                                     "schedule": "Daily"}, headers=hb).status_code == 201
    tasks = a.get(f"/api/v2/portal/links/{link_id}/assignments").json()["data"]
    instrument = next(t for t in tasks if t["kind"] == "instrument")
    assert instrument["content"]["items"][0]["text"].startswith("Fictional item")
    ids = [i["id"] for i in instrument["content"]["items"]]
    res = a.post(f"/api/v2/portal/assignments/{instrument['id']}/submit",
                 json={"answers": {str(ids[0]): 5, str(ids[1]): 2, str(ids[2]): 4, str(ids[3]): 5}}, headers=ha)
    assert res.status_code == 201, res.text
    act = b.get(f"/api/v2/loop/links/{link_id}/activity").json()
    done = next(x for x in act["assignments"] if x["kind"] == "instrument")
    assert done["status"] == "completed" and done["submissions"][0]["scores"]["focus"]["score"] == 9.0
    tl = b.get(f"/api/v2/athletes/{aid}/timeline")
    assert tl.status_code == 200, tl.text
    tl = tl.json()
    assert any(x["kind"] == "assignment" and "Readiness" in x["title"] for x in tl)


def test_licensed_instruments_cannot_be_assigned(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    csai = next(i for i in b.get("/api/v2/instruments").json() if i["short_name"] == "CSAI-2")
    assert b.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "instrument", "title": "Licensed test",
                                                                     "instrument_id": csai["id"]}, headers=hb).status_code == 409


def test_checkins_missing_marked_rules_and_urgent_always_alerts(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    start = (date.today() - timedelta(days=14)).isoformat()
    assert b.put(f"/api/v2/loop/links/{link_id}/checkin-plan", json={"frequency": "weekly", "starts_on": start,
                 "items": ["stress", "confidence", "mood"]}, headers=hb).status_code == 200
    assert b.post(f"/api/v2/loop/links/{link_id}/alert-rules", json={"item": "stress", "op": ">=", "threshold": 8},
                  headers=hb).status_code == 201
    due = a.get(f"/api/v2/portal/links/{link_id}/checkins").json()["data"]["due"]
    assert len(due) == 3 and all(d["status"] == "missing" for d in due)
    assert a.post(f"/api/v2/portal/links/{link_id}/checkins", json={"answers": {"stress": 9, "confidence": 2}},
                  headers=ha).status_code == 422  # every item answered
    ok = a.post(f"/api/v2/portal/links/{link_id}/checkins", json={"answers": {"stress": 9, "confidence": 3, "mood": 4}},
                headers=ha)
    assert ok.status_code == 201
    assert a.post(f"/api/v2/portal/links/{link_id}/checkins", json={"answers": {"stress": 1, "confidence": 9, "mood": 9}},
                  headers=ha).status_code == 409  # one per period
    act = b.get(f"/api/v2/loop/links/{link_id}/activity").json()
    assert [c["status"] for c in act["checkins"]] == ["missing", "missing", "submitted"]
    assert act["alerts"][0]["reason"].startswith("Stress 9") and not act["alerts"][0]["urgent"]
    assert any(m["subject"] == "SPPS check-in alert" for m in loop_app.state.v2_outbox)


def test_urgent_answer_forces_urgent_review_regardless_of_scores(loop_app):
    from server.v2.athlete_loop import evaluate
    assert evaluate([], {"stress": 0, "mood": 10}, True) == [("The athlete asked for urgent contact", True)]
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    b.put(f"/api/v2/loop/links/{link_id}/checkin-plan", json={"frequency": "daily", "items": ["stress", "mood", "motivation"]},
          headers=hb)
    a.post(f"/api/v2/portal/links/{link_id}/checkins", json={"answers": {"stress": 0, "mood": 10, "motivation": 10},
                                                              "urgent": True}, headers=ha)
    alerts = b.get(f"/api/v2/loop/links/{link_id}/activity").json()["alerts"]
    assert alerts and alerts[0]["urgent"]
    assert any(m["subject"].startswith("URGENT") for m in loop_app.state.v2_outbox)
    assert b.get("/api/v2/loop/links").json()[0]["open_alerts"] == 1
    b.post(f"/api/v2/loop/alerts/{alerts[0]['id']}/acknowledge", headers=hb)
    assert b.get("/api/v2/loop/links").json()[0]["open_alerts"] == 0


def test_under_18_guardian_first_and_guardian_sees_participation_only(loop_app):
    b = fresh_client(loop_app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb, code="LOOP-M", name="Fictional Minor")
    b.put(f"/api/v2/athletes/{aid}/profile", json={"age_group": "15-17", "guardian_name": "Fictional Parent",
                                                   "guardian_email": "parent@example.org", "consent_status": "recorded"},
          headers=hb)
    inv = b.post(f"/api/v2/loop/athletes/{aid}/invite", json={}, headers=hb).json()
    assert inv["kind"] == "guardian"
    gtoken = token_from(loop_app, "parent@example.org", "invite")
    kid, hk = portal_user(loop_app, "minor@example.org", "athlete")
    assert kid.post(f"/api/v2/portal/invites/{gtoken}/accept", json={"consent": True}, headers=hk).status_code == 403
    g, hg = portal_user(loop_app, "parent@example.org", "guardian")
    assert g.post(f"/api/v2/portal/invites/{gtoken}/accept", json={"consent": True}, headers=hg).status_code == 422
    res = g.post(f"/api/v2/portal/invites/{gtoken}/accept", json={"consent": True, "guardian_declaration": True, "guardian_evidence": "Fictional Parent",
                                                                   "athlete_email": "minor@example.org"}, headers=hg)
    assert res.json()["data"]["status"] == "guardian_consented"
    atoken = token_from(loop_app, "minor@example.org", "invite")
    assert kid.post(f"/api/v2/portal/invites/{atoken}/accept", json={"consent": True}, headers=hk).json()["data"]["status"] == "active"
    link_id = inv["link_id"]
    b.put(f"/api/v2/loop/links/{link_id}/checkin-plan", json={"frequency": "daily", "items": ["stress", "mood", "anxiety"]},
          headers=hb)
    kid.post(f"/api/v2/portal/links/{link_id}/checkins", json={"answers": {"stress": 7, "mood": 3, "anxiety": 6}}, headers=hk)
    b.post(f"/api/v2/loop/links/{link_id}/summaries", json={"title": "Fictional progress", "body": "Going well."}, headers=hb)
    view = g.get(f"/api/v2/portal/links/{link_id}/guardian-view").json()["data"]
    assert view["participation"]["checkins_submitted"] == 1 and view["summaries"][0]["title"] == "Fictional progress"
    assert "checkins" not in view and not view["detail_enabled"]
    assert g.get(f"/api/v2/portal/links/{link_id}/checkins").status_code == 404  # raw answers: athlete only
    b.post(f"/api/v2/loop/links/{link_id}/guardian-detail", json={"enabled": True}, headers=hb)
    assert "checkins" not in g.get(f"/api/v2/portal/links/{link_id}/guardian-view").json()["data"]  # athlete not yet
    kid.post(f"/api/v2/portal/links/{link_id}/guardian-detail", json={"enabled": True}, headers=hk)
    assert g.get(f"/api/v2/portal/links/{link_id}/guardian-view").json()["data"]["checkins"][0]["answers"]["stress"] == 7


def test_ending_the_link_removes_access_immediately_from_either_side(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    assert a.get(f"/api/v2/portal/links/{link_id}/assignments").status_code == 200
    assert b.post(f"/api/v2/loop/links/{link_id}/end", headers=hb).json()["status"] == "ended"
    for path in (f"/api/v2/portal/links/{link_id}/assignments", f"/api/v2/portal/links/{link_id}/checkins"):
        assert a.get(path).status_code == 404
    assert b.post(f"/api/v2/loop/links/{link_id}/summaries", json={"title": "xx", "body": "yy"}, headers=hb).status_code == 409
    exported = a.get("/api/v2/portal/export").json()  # the athlete keeps and can export their own data
    assert exported["links"][0]["status"] == "ended"


def test_athlete_can_end_the_link(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    assert a.post(f"/api/v2/portal/links/{link_id}/end", headers=ha).status_code == 200
    assert b.get("/api/v2/loop/links").json()[0]["ended_by"] == "athlete"
    assert b.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "programme", "title": "After ending",
                                                                     "tasks": ["y"]}, headers=hb).status_code == 409


def test_isolation_between_athletes_and_practitioners(loop_app):
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    other, ho = portal_user(loop_app, "someone.else@example.org", "athlete")
    for path in (f"/api/v2/portal/links/{link_id}/assignments", f"/api/v2/portal/links/{link_id}/checkins",
                 f"/api/v2/portal/links/{link_id}/guardian-view"):
        assert other.get(path).status_code == 404
    assert other.get("/api/v2/portal/links").json()["data"] == []
    c = fresh_client(loop_app)
    hc = login(c, PRACTITIONER_C)
    assert c.get("/api/v2/loop/links").json() == []
    assert c.get(f"/api/v2/loop/links/{link_id}/activity").status_code == 404
    assert c.post(f"/api/v2/loop/athletes/{aid}/invite", json={"email": "x@example.org"}, headers=hc).status_code == 404


def test_invite_tokens_are_single_use_and_bound_to_the_invited_account(loop_app):
    b = fresh_client(loop_app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb, code="LOOP-T")
    b.post(f"/api/v2/loop/athletes/{aid}/invite", json={"email": "right@example.org"}, headers=hb)
    token = token_from(loop_app, "right@example.org", "invite")
    wrong, hw = portal_user(loop_app, "wrong@example.org", "athlete")
    assert wrong.post(f"/api/v2/portal/invites/{token}/accept", json={"consent": True}, headers=hw).status_code == 403
    right, hr = portal_user(loop_app, "right@example.org", "athlete")
    assert right.post(f"/api/v2/portal/invites/{token}/accept", json={"consent": True}, headers=hr).status_code == 200
    assert right.post(f"/api/v2/portal/invites/{token}/accept", json={"consent": True}, headers=hr).status_code == 404
    assert fresh_client(loop_app).get(f"/api/v2/portal/invites/{token}").status_code == 404


def test_every_portal_screen_states_not_a_crisis_service_and_shows_owner_contacts(loop_app):
    owner = fresh_client(loop_app)
    ho = login(owner, OWNER_LOGIN)
    assert owner.put("/api/v2/admin/crisis-contacts", json={"contacts": [{"name": "Fictional Helpline",
                                                                          "phone": "0000-000-000", "hours": "24/7"}]},
                     headers=ho).status_code == 200
    b, hb, aid, link_id, a, ha = adult_link(loop_app)
    for res in (a.get("/api/v2/portal/me"), a.get("/api/v2/portal/links"),
                a.get(f"/api/v2/portal/links/{link_id}/assignments"), a.get(f"/api/v2/portal/links/{link_id}/checkins"),
                fresh_client(loop_app).get("/api/v2/portal/crisis-contacts")):
        body = res.json()
        assert "not a crisis or emergency service" in body["notice"]
        assert body["crisis_contacts"][0]["name"] == "Fictional Helpline"
    assert fresh_client(loop_app).get("/api/v2/portal/crisis-contacts").status_code == 200
    c = fresh_client(loop_app)
    hc = login(c, PRACTITIONER_C)
    assert c.put("/api/v2/admin/crisis-contacts", json={"contacts": [{"name": "x", "phone": "1"}]},
                 headers=hc).status_code == 403


def test_loop_routes_do_not_exist_while_the_flags_are_off(app):
    c = fresh_client(app)
    assert c.get("/api/v2/portal/crisis-contacts").status_code == 404
    login(c, PRACTITIONER_B)
    assert c.get("/api/v2/loop/links").status_code == 404
