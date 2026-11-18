"""Stage 13C: clinical participation — contract §C acceptance.

Correct tasks and progression per session; revision conflicts handled; submissions
authenticated, case-linked, immutable; practitioner readback and review consistent;
revoked links block submission; note/report privacy intact. Fictional data only.
"""
import json

import pytest
from sqlalchemy import create_engine, text

from tests_integration.test_portal_auth import verified_login
from tests_integration.test_portal_guardian import verified_authority
from tests_integration.test_portal_links import athlete_record, invite, linked, practitioner, practitioner_id
from tools.identity_review_db import EPHEMERAL_TARGET


def consent(portal, person, pid, purpose="PROGRAMME_PARTICIPATION", decision="GRANTED"):
    assert portal.post("/api/portal/consents", json={"subject_person_id": person, "purpose": purpose,
                       "practitioner_id": pid, "decision": decision}).status_code == 201


def programme(alice, record, title="Fictional pre-competition routine"):
    r = alice.post(f"/api/athletes/{record}/programmes", json={"title": title, "goal": "Fictional goal"})
    assert r.status_code == 201, r.text
    pid = r.json()["programme_id"]
    t = alice.post(f"/api/programmes/{pid}/tasks", json={"title": "Breathing reset", "instructions": "4-6 breathing, 3 min",
                                                          "frequency": "daily"})
    assert t.status_code == 201, t.text
    return pid, t.json()["task_id"]


def assigned(app, env):
    alice, record, a, person, link = linked(app, env)
    prog, task = programme(alice, record)
    assert alice.post(f"/api/programmes/{prog}/status", json={"status": "ASSIGNED"}).json()["status"] == "ASSIGNED"
    return alice, record, a, person, link, prog, task


def accepted(app, env):
    alice, record, a, person, link, prog, task = assigned(app, env)
    consent(a, person, practitioner_id(alice))
    assert a.post(f"/api/portal/programmes/{prog}/respond", json={"accept": True}).json()["status"] == "ACCEPTED"
    return alice, record, a, person, link, prog, task


def portal_view(a):
    return a.get("/api/portal/programmes").json()


def test_draft_is_invisible_then_assigned_accept_submit_review(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    prog, task = programme(alice, record)
    assert portal_view(a)["programmes"] == []  # drafts are private to the practitioner
    alice.post(f"/api/programmes/{prog}/status", json={"status": "ASSIGNED"})
    view = portal_view(a)["programmes"][0]
    assert view["status"] == "ASSIGNED" and view["tasks"][0]["title"] == "Breathing reset" and view["can_act"]
    # Accepting requires programme-participation consent.
    assert a.post(f"/api/portal/programmes/{prog}/respond", json={"accept": True}).status_code == 409
    assert a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 409  # not accepted
    consent(a, person, practitioner_id(alice))
    assert a.post(f"/api/portal/programmes/{prog}/respond", json={"accept": True}).json()["status"] == "ACCEPTED"
    sub = a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True, "self_rating": 7,
                 "reflection": "Fictional: felt calmer before serve"})
    assert sub.status_code == 201
    sid = sub.json()["submission_id"]
    detail = alice.get(f"/api/athletes/{record}/participation").json()
    s = detail["programmes"][0]["tasks"][0]["submissions"][0]
    assert (s["id"], s["source"], s["self_rating"], s["completed"]) == (sid, "ATHLETE_SELF_REPORT", 7, True)
    assert alice.post(f"/api/task-submissions/{sid}/review", json={"feedback": "Fictional: good consistency"}).status_code == 201
    mine = portal_view(a)["programmes"][0]["tasks"][0]["submissions"][0]
    assert mine["feedback"] == ["Fictional: good consistency"]
    kinds = {n["kind"] for n in a.get("/api/portal/notifications").json()}
    assert {"programme.assigned", "programme.feedback"} <= kinds
    assert {"programme.accepted", "programme.submission"} <= {n["kind"] for n in alice.get("/api/practitioner/notifications").json()}


def test_programme_requires_active_link_and_tasks(app, integration_env):
    alice = practitioner(app, integration_env)
    unlinked = athlete_record(alice)
    assert alice.post(f"/api/athletes/{unlinked}/programmes", json={"title": "No link programme"}).status_code == 409
    alice2, record, a, person, link = linked(app, integration_env)
    r = alice2.post(f"/api/athletes/{record}/programmes", json={"title": "Empty programme"}).json()["programme_id"]
    assert alice2.post(f"/api/programmes/{r}/status", json={"status": "ASSIGNED"}).status_code == 409


def test_decline_and_lifecycle_transitions(app, integration_env):
    alice, record, a, person, link, prog, task = assigned(app, integration_env)
    assert a.post(f"/api/portal/programmes/{prog}/respond", json={"accept": False, "reason": "Fictional timing"}).json()["status"] == "DECLINED"
    assert alice.post(f"/api/programmes/{prog}/status", json={"status": "PAUSED"}).status_code == 409
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    assert alice.post(f"/api/programmes/{prog}/status", json={"status": "PAUSED"}).json()["status"] == "PAUSED"
    assert a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 409
    assert alice.post(f"/api/programmes/{prog}/status", json={"status": "ACCEPTED"}).json()["status"] == "ACCEPTED"
    assert alice.post(f"/api/programmes/{prog}/status", json={"status": "COMPLETED"}).json()["status"] == "COMPLETED"
    assert alice.post(f"/api/programmes/{prog}/tasks", json={"title": "Late task"}).status_code == 409


def test_task_revision_conflicts_and_discontinuation(app, integration_env):
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    body = {"title": "Breathing reset v2", "instructions": "5 min", "frequency": "daily", "expected_revision": 1}
    assert alice.post(f"/api/programme-tasks/{task}/update", json=body).json()["revision"] == 2
    assert alice.post(f"/api/programme-tasks/{task}/update", json=body).status_code == 409  # stale revision
    s = a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).json()["submission_id"]
    sub = alice.get(f"/api/athletes/{record}/participation").json()["programmes"][0]["tasks"][0]["submissions"][0]
    assert sub["id"] == s and sub["task_revision"] == 2  # submission records the version answered
    assert alice.post(f"/api/programme-tasks/{task}/discontinue").status_code == 200
    assert portal_view(a)["programmes"][0]["tasks"] == []
    assert a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 409


def test_submissions_are_append_only_in_database(app, integration_env):
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    sid = a.post(f"/api/portal/tasks/{task}/submit", json={"completed": False, "self_rating": 3}).json()["submission_id"]
    engine = create_engine(EPHEMERAL_TARGET.admin_url())
    try:
        for sql in ("UPDATE spps_identity_dev.platform_task_submissions SET self_rating=10 WHERE id=:i",
                    "DELETE FROM spps_identity_dev.platform_task_submissions WHERE id=:i"):
            with pytest.raises(Exception, match="append-only"):
                with engine.begin() as conn:
                    conn.execute(text(sql), {"i": sid})
    finally:
        engine.dispose()


def test_revoked_link_or_withdrawn_consent_blocks_submission_and_hides_programme(app, integration_env):
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    consent(a, person, practitioner_id(alice), decision="WITHDRAWN")
    assert a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 409
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    a.post(f"/api/portal/links/{link}/revoke")
    assert a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 404
    assert portal_view(a)["programmes"] == []
    # Practitioner keeps their own record of the programme.
    assert alice.get(f"/api/athletes/{record}/participation").json()["programmes"][0]["id"] == prog


def test_other_practitioners_and_accounts_are_isolated(app, integration_env):
    alice, record, a, person, link, prog, task = accepted(app, integration_env)
    bob = practitioner(app, integration_env, "bob")
    assert bob.get(f"/api/athletes/{record}/participation").status_code == 404
    assert bob.post(f"/api/programmes/{prog}/tasks", json={"title": "Injected task"}).status_code == 404
    assert bob.post(f"/api/programmes/{prog}/status", json={"status": "DISCONTINUED"}).status_code == 404
    assert bob.post(f"/api/programme-tasks/{task}/discontinue").status_code == 404
    sid = a.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).json()["submission_id"]
    assert bob.post(f"/api/task-submissions/{sid}/review", json={"feedback": "Not my athlete"}).status_code == 404
    other, _ = verified_login(app, integration_env)
    assert portal_view(other)["programmes"] == []
    assert other.post(f"/api/portal/tasks/{task}/submit", json={"completed": True}).status_code == 404
    assert other.post(f"/api/portal/programmes/{prog}/respond", json={"accept": False}).status_code == 404


def test_guardian_scopes_view_only_versus_proxy(app, integration_env):
    alice = practitioner(app, integration_env)
    pid = practitioner_id(alice)

    def guardian_link(scopes):
        g, dep, aid, rev = verified_authority(app, integration_env, scopes=scopes)
        code = invite(alice, rec := athlete_record(alice))
        consent(g, dep, pid, "CASE_LINK")
        link = g.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": dep}).json()["link_id"]
        alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"})
        return g, dep, rec, aid, rev

    g, dep, rec, aid, rev = guardian_link(["consent_management", "assigned_tasks"])
    prog, task = programme(alice, rec)
    alice.post(f"/api/programmes/{prog}/status", json={"status": "ASSIGNED"})
    view = portal_view(g)["programmes"][0]
    assert view["can_act"] is False and view["can_see_submissions"] is False
    assert g.post(f"/api/portal/programmes/{prog}/respond", json={"accept": True}).status_code == 404

    g2, dep2, rec2, aid2, rev2 = guardian_link(["consent_management", "assigned_tasks", "proxy_submission", "own_submissions"])
    prog2, task2 = programme(alice, rec2)
    alice.post(f"/api/programmes/{prog2}/status", json={"status": "ASSIGNED"})
    consent(g2, dep2, pid)
    assert g2.post(f"/api/portal/programmes/{prog2}/respond", json={"accept": True}).json()["status"] == "ACCEPTED"
    assert g2.post(f"/api/portal/tasks/{task2}/submit", json={"completed": True, "self_rating": 6}).status_code == 201
    sub = alice.get(f"/api/athletes/{rec2}/participation").json()["programmes"][0]["tasks"][0]["submissions"][0]
    assert sub["source"] == "GUARDIAN_PROXY"
    rev2.post(f"/api/portal/review/authorities/{aid2}/revoke", json={"reason": "Fictional authority ended"})
    assert g2.post(f"/api/portal/tasks/{task2}/submit", json={"completed": True}).status_code == 404
    assert portal_view(g2)["programmes"] == []


def test_released_summaries_only_and_never_private_notes(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    case = alice.post("/api/cases", json={"athlete_id": record, "title": "Fictional private case"}).json()["id"]
    alice.post("/api/notes", json={"case_id": case, "note_date": "2026-11-12", "content": "FICTIONAL-PRIVATE-NOTE-TEXT"})
    alice.post("/api/reports", json={"athlete_id": record, "case_id": case, "title": "Draft report",
                                     "body": "FICTIONAL-DRAFT-REPORT-BODY"})
    r = alice.post(f"/api/athletes/{record}/released-summaries", json={"kind": "progress_summary",
                   "title": "Fictional month summary", "body": "Fictional: steady progress on routines."})
    assert r.status_code == 201
    sid = r.json()["summary_id"]
    view = portal_view(a)
    assert [s["title"] for s in view["summaries"]] == ["Fictional month summary"]
    blob = json.dumps(view) + json.dumps(a.get("/api/portal/me").json())
    assert "FICTIONAL-PRIVATE-NOTE-TEXT" not in blob and "FICTIONAL-DRAFT-REPORT-BODY" not in blob
    assert alice.post(f"/api/released-summaries/{sid}/withdraw").status_code == 200
    assert portal_view(a)["summaries"] == []
    bob = practitioner(app, integration_env, "bob")
    assert bob.post(f"/api/athletes/{record}/released-summaries", json={"kind": "progress_summary",
                    "title": "Forged", "body": "Forged summary"}).status_code == 404
