"""Phase 8 acceptance and negative tests (API). PostgreSQL row-level security is in tests_v2_pg/test_phase8_rls.py.

Acceptance: an unlinked athlete account exposes nothing to any practitioner; deletion removes identity data and
severs links; every step works from a different browser (separate cookie jars stand in for Gmail -> Chrome -> app).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from .conftest import PRACTITIONER_B, PRACTITIONER_C, fresh_client, login
from .test_phase5_reports import COACH, _report
from .test_phase6_athlete_loop import portal_user, token_from
from .test_phase7_directory import PROFILE, _published

PASSWORD = "Fictional-pass-2026"


@pytest.fixture
def ss_app(v2env, tmp_path, fake_supabase, monkeypatch):
    from server.v2 import flags
    for name in ("SPPS_ATHLETE_LOOP_ENABLED", "SPPS_DIRECTORY_ENABLED", "SPPS_SELF_SERVE_ENABLED",
                 "SPPS_REPORT_LINKS_ENABLED"):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("SPPS_CRISIS_CONTACTS_TEXT", "Fictional helpline 0000 (24/7).")
    monkeypatch.setattr(flags, "portal_enabled", lambda: True)
    from test_support.app import create_app
    app = create_app(f"sqlite:///{tmp_path / 'ss.db'}", init_database=True, seed_demo=True)
    yield app
    app.state.engine.dispose()


def form_token(client):
    return client.get("/api/public/form-token").json()["form_token"]


def account_enquiry(app, client, headers, pid, sport="Hockey", message="Fictional: pre-match nerves before finals"):
    res = client.post("/api/v2/portal/enquiries", json={"practitioner_id": pid, "sport": sport, "message": message,
                                                        "form_token": form_token(client), "website": ""},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def inbox_item(p, contact):
    return next(e for e in p.get("/api/v2/directory/enquiries").json() if e["contact"] == contact)


ADULT = {"display_name": "Fictional Self-Serve Athlete", "code": "SS-1", "age_group": "18-24",
         "service_consent_confirmed": True}


def linked_adult(app, email="self.serve@example.org"):
    """Account first -> enquiry -> practitioner accepts -> athlete accepts in another browser. Returns everything."""
    p, hp, owner, ho, pid = _published(app)
    a, ha = portal_user(app, email, "athlete", name="Fictional Self-Serve Athlete")
    account_enquiry(app, a, ha, pid)
    e = inbox_item(p, email)
    res = p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json=ADULT, headers=hp)
    assert res.status_code == 201, res.text
    other = fresh_client(app)  # Chrome opened from the Android app: a different browser, signs in again
    h2 = {"X-CSRF-Token": other.post("/api/v2/portal/auth/login", json={"email": email, "password": PASSWORD}).json()["csrf"]}
    inv = other.get("/api/v2/portal/invitations").json()["data"]
    assert len(inv) == 1 and inv[0]["practitioner_name"]
    ok = other.post(f"/api/v2/portal/invitations/{inv[0]['id']}/accept", json={"consent": True}, headers=h2)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "active", ok.text
    return p, hp, pid, res.json(), other, h2


# ------------------------------------------------------------------ 1. practitioner accepts an enquiry
def test_accepting_a_public_enquiry_creates_the_record_and_sends_the_phase6_invitation(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    anon = fresh_client(ss_app)
    assert anon.post(f"/api/v2/public/directory/{pid}/enquiries", json={
        "name": "Fictional Public Athlete", "contact": "public.athlete@example.org", "role": "athlete",
        "sport": "Hockey", "message": "Fictional question about routines", "form_token": form_token(anon),
        "website": ""}).status_code == 201
    e = inbox_item(p, "public.athlete@example.org")
    assert p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json={**ADULT, "service_consent_confirmed": False},
                  headers=hp).status_code == 422
    c = fresh_client(ss_app)
    hc = login(c, PRACTITIONER_C)
    assert c.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json=ADULT, headers=hc).status_code == 404
    res = p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json=ADULT, headers=hp)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kind"] == "athlete" and "/athlete?invite=" in body["url"]
    assert any(a["id"] == body["athlete_id"] and a["code"] == "SS-1" for a in p.get("/api/athletes").json())
    assert token_from(ss_app, "public.athlete@example.org", "invite")
    after = inbox_item(p, "public.athlete@example.org")
    assert after["status"] == "accepted" and after["athlete_id"] == body["athlete_id"]
    assert p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json={**ADULT, "code": "SS-2"},
                  headers=hp).status_code == 409
    link = next(l_ for l_ in p.get("/api/v2/loop/links").json() if l_["id"] == body["link_id"])
    assert link["status"] == "invited"  # nothing flows until the athlete accepts


# ------------------------------------------------------------------ 2. account first; nothing shared until both accept
def test_an_unlinked_account_exposes_nothing_to_any_practitioner(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    a, ha = portal_user(ss_app, "quiet.account@example.org", "athlete", name="Fictional Quiet Account")
    for who, creds in (("b", PRACTITIONER_B), ("c", PRACTITIONER_C)):
        x = fresh_client(ss_app)
        login(x, creds)
        assert x.get("/api/v2/loop/links").json() == []
        assert all(e["contact"] != "quiet.account@example.org" for e in x.get("/api/v2/directory/enquiries").json())
        assert all(r.get("display_name") != "Fictional Quiet Account" for r in x.get("/api/athletes").json())
    # Owner directory overview and stats never carry account data either.
    assert "quiet.account" not in owner.get("/api/v2/admin/directory/enquiry-stats").text


def test_account_enquiry_shares_only_the_enquiry_until_both_sides_accept(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    a, ha = portal_user(ss_app, "first.account@example.org", "athlete", name="Fictional First Account")
    sent = account_enquiry(ss_app, a, ha, pid)
    assert sent["status"] == "sent" and "not a crisis" in sent["message"].lower()
    assert a.post("/api/v2/portal/enquiries", json={"practitioner_id": pid, "sport": "Hockey", "message": "Again",
                                                    "form_token": form_token(a), "website": ""},
                  headers=ha).status_code == 409  # one open enquiry per practitioner
    e = inbox_item(p, "first.account@example.org")
    assert e["from_account"] is True and set(e) == {"id", "name", "contact", "role", "sport", "message", "status",
                                                     "created_at", "responded_at", "from_account", "athlete_id"}
    assert p.get("/api/v2/loop/links").json() == []
    assert a.get("/api/v2/portal/links").json()["data"] == []
    assert a.get("/api/v2/portal/invitations").json()["data"] == []
    # The practitioner accepts: a record and an invited (not active) link; still nothing flows.
    res = p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json={**ADULT, "invite_email": "other@example.org"},
                 headers=hp)
    assert res.status_code == 201
    link_id = res.json()["link_id"]
    assert token_from(ss_app, "first.account@example.org", "invite")  # the account's own address, not the typed one
    assert p.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "programme", "title": "Breathing",
                                                                     "tasks": ["Box breathing"]}, headers=hp).status_code == 409
    assert p.get(f"/api/v2/loop/links/{link_id}/activity").json()["checkins"] == []
    mine = a.get("/api/v2/portal/enquiries").json()["data"]
    assert mine[0]["status"] == "accepted"
    # Another account cannot see or accept it.
    x, hx = portal_user(ss_app, "someone.else@example.org", "athlete")
    assert x.get("/api/v2/portal/invitations").json()["data"] == []
    inv = a.get("/api/v2/portal/invitations").json()["data"][0]
    assert x.post(f"/api/v2/portal/invitations/{inv['id']}/accept", json={"consent": True}, headers=hx).status_code == 404
    assert a.post(f"/api/v2/portal/invitations/{inv['id']}/accept", json={"consent": False}, headers=ha).status_code == 422
    assert a.post(f"/api/v2/portal/invitations/{inv['id']}/accept", json={"consent": True}, headers=ha).status_code == 200
    assert next(l_ for l_ in p.get("/api/v2/loop/links").json() if l_["id"] == link_id)["status"] == "active"


def test_guardian_account_enquiry_for_an_under_18_goes_guardian_first(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    g, hg = portal_user(ss_app, "fictional.parent@example.org", "guardian", name="Fictional Parent")
    account_enquiry(ss_app, g, hg, pid)
    e = inbox_item(p, "fictional.parent@example.org")
    assert e["role"] == "parent"
    assert p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json=ADULT, headers=hp).status_code == 422
    res = p.post(f"/api/v2/directory/enquiries/{e['id']}/accept",
                 json={**ADULT, "age_group": "15-17", "display_name": "Fictional Junior"}, headers=hp)
    assert res.status_code == 201 and res.json()["kind"] == "guardian"
    inv = g.get("/api/v2/portal/invitations").json()["data"][0]
    assert inv["kind"] == "guardian" and inv["is_minor"] is True
    assert g.post(f"/api/v2/portal/invitations/{inv['id']}/accept", json={"consent": True}, headers=hg).status_code == 422
    ok = g.post(f"/api/v2/portal/invitations/{inv['id']}/accept", json={
        "consent": True, "guardian_declaration": True, "guardian_evidence": "Fictional Parent", "athlete_email": "fictional.junior@example.org"},
        headers=hg)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "guardian_consented"
    assert token_from(ss_app, "fictional.junior@example.org", "invite")


def test_declined_and_withdrawn_enquiries(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    a, ha = portal_user(ss_app, "declined.account@example.org", "athlete")
    account_enquiry(ss_app, a, ha, pid)
    e = inbox_item(p, "declined.account@example.org")
    assert p.post(f"/api/v2/directory/enquiries/{e['id']}/decline", headers=hp).json()["status"] == "declined"
    assert a.get("/api/v2/portal/enquiries").json()["data"][0]["status"] == "declined"
    assert p.post(f"/api/v2/directory/enquiries/{e['id']}/accept", json=ADULT, headers=hp).status_code == 409
    second = account_enquiry(ss_app, a, ha, pid, message="Fictional second try")
    assert a.post(f"/api/v2/portal/enquiries/{second['id']}/withdraw", headers=ha).json()["data"]["status"] == "withdrawn"


# ------------------------------------------------------------------ 3. switch practitioner
def test_switching_practitioner_ends_the_link_keeps_the_data_and_starts_fresh(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    link_id = accepted["link_id"]
    assert p.post(f"/api/v2/loop/links/{link_id}/assignments", json={"kind": "programme", "title": "Breathing",
                                                                     "tasks": ["Box breathing"]}, headers=hp).status_code == 201
    task = a.get(f"/api/v2/portal/links/{link_id}/assignments").json()["data"][0]
    assert a.post(f"/api/v2/portal/assignments/{task['id']}/submit", json={"answers": {"done": True}},
                  headers=ha).status_code == 201
    c, hc, _, _, pid_c = _published(ss_app, PRACTITIONER_C, {**PROFILE, "display_name": "Dr Fictional Second"})
    assert a.post(f"/api/v2/portal/links/{link_id}/switch", json={
        "practitioner_id": pid, "sport": "Hockey", "message": "Fictional", "form_token": form_token(a), "website": ""},
        headers=ha).status_code == 409
    res = a.post(f"/api/v2/portal/links/{link_id}/switch", json={
        "practitioner_id": pid_c, "sport": "Hockey", "message": "Fictional: looking for a new practitioner",
        "form_token": form_token(a), "website": ""}, headers=ha)
    assert res.status_code == 201, res.text
    # Old practitioner keeps what was shared; the athlete no longer reaches the old link.
    old = next(l_ for l_ in p.get("/api/v2/loop/links").json() if l_["id"] == link_id)
    assert old["status"] == "ended" and old["ended_by"] == "athlete"
    assert p.get(f"/api/v2/loop/links/{link_id}/activity").json()["assignments"][0]["status"] == "completed"
    assert a.get(f"/api/v2/portal/links/{link_id}/assignments").status_code == 404
    # New practitioner has only the enquiry, and no link until both accept.
    assert inbox_item(c, "self.serve@example.org")["status"] == "new"
    assert c.get("/api/v2/loop/links").json() == []
    assert "Breathing" not in c.get("/api/v2/directory/enquiries").text


# ------------------------------------------------------------------ 4. lifecycle
def test_deactivate_and_reactivate_without_anyone_elses_consent(ss_app):
    a, ha = portal_user(ss_app, "pause.account@example.org", "athlete")
    assert a.post("/api/v2/portal/account/deactivate", json={"password": "wrong-password"}, headers=ha).status_code == 403
    assert a.post("/api/v2/portal/account/deactivate", json={"password": PASSWORD}, headers=ha).status_code == 200
    assert a.get("/api/v2/portal/me").status_code == 401
    other = fresh_client(ss_app)
    assert other.post("/api/v2/portal/auth/login", json={"email": "pause.account@example.org",
                                                         "password": PASSWORD}).status_code == 401
    assert other.post("/api/v2/portal/auth/reactivate", json={"email": "pause.account@example.org"}).status_code == 202
    assert other.post("/api/v2/portal/auth/reactivate", json={"email": "nobody@example.org"}).status_code == 202
    verify = token_from(ss_app, "pause.account@example.org", "verify")
    third = fresh_client(ss_app)  # the e-mailed link opened in yet another browser
    assert third.post("/api/v2/portal/auth/verify", json={"token": verify}).status_code == 200
    assert third.post("/api/v2/portal/auth/login", json={"email": "pause.account@example.org",
                                                         "password": PASSWORD}).status_code == 200


def test_export_contains_own_enquiries(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    out = a.get("/api/v2/portal/export").json()
    assert out["format"] == "spps-athlete-export-v1" and out["enquiries"][0]["status"] == "accepted"
    assert out["links"] and out["account"]["email"] == "self.serve@example.org"


def test_deleting_the_account_removes_identity_data_and_severs_every_link(ss_app):
    from server.v2.identity_models import AthleteAccount, AthleteSession, LoopInvite, LoopLink, LoopRequest
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    link_id = accepted["link_id"]
    with ss_app.state.v2_identity.Auth() as s:
        acc_id = s.scalar(select(AthleteAccount.id).where(AthleteAccount.email == "self.serve@example.org"))
    assert a.post("/api/v2/portal/account/delete", json={"password": PASSWORD, "confirm": "yes"},
                  headers=ha).status_code == 422
    assert a.post("/api/v2/portal/account/delete", json={"password": "wrong-password", "confirm": "DELETE"},
                  headers=ha).status_code == 403
    res = a.post("/api/v2/portal/account/delete", json={"password": PASSWORD, "confirm": "DELETE"}, headers=ha)
    assert res.status_code == 200 and res.json()["links_ended"] == 1
    ident = ss_app.state.v2_identity
    with ident.Auth() as s:
        assert s.scalar(select(AthleteAccount).where(AthleteAccount.email == "self.serve@example.org")) is None
        assert s.scalars(select(AthleteSession).where(AthleteSession.account_id == acc_id)).all() == []
        assert s.scalars(select(LoopInvite).where(LoopInvite.email == "self.serve@example.org")).all() == []
        assert s.scalars(select(LoopRequest)).all() == []
        link = s.get(LoopLink, link_id)
        assert link.status == "ended" and link.ended_by == "deleted" and link.athlete_account_id is None
    assert a.get("/api/v2/portal/me").status_code == 401
    assert fresh_client(ss_app).post("/api/v2/portal/auth/login", json={"email": "self.serve@example.org",
                                                                        "password": PASSWORD}).status_code == 401
    old = next(l_ for l_ in p.get("/api/v2/loop/links").json() if l_["id"] == link_id)
    assert old["status"] == "ended"
    # A new account with the same address starts empty.
    n, hn = portal_user(ss_app, "self.serve@example.org", "athlete")
    assert n.get("/api/v2/portal/links").json()["data"] == [] and n.get("/api/v2/portal/invitations").json()["data"] == []


# ------------------------------------------------------------------ 5. athlete-approved report links
def _signed_coach_report(p, hp, athlete_id, audience="coach"):
    tpl = p.post("/api/v2/report-templates", json={"name": "Coach note", "fields": [
        {"key": "summary", "label": "Summary for the coach", "kind": "narrative", "audiences": ["coach", "medical"]},
        {"key": "clinical", "label": "Clinical formulation", "kind": "narrative", "audiences": []},
        {"key": "medical", "label": "Medical referral", "kind": "narrative", "audiences": ["medical"]}]},
        headers=hp).json()
    r = _report(p, hp, athlete_id, tpl["id"], audience=audience)
    assert "id" in r, r
    assert p.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hp).status_code == 200
    return r["id"]


def test_report_link_opens_only_after_the_athlete_approves_and_closes_when_withdrawn(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    rid = _signed_coach_report(p, hp, accepted["athlete_id"])
    c = fresh_client(ss_app)
    hc = login(c, PRACTITIONER_C)
    assert c.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hc).status_code == 404
    med = _signed_coach_report(p, hp, accepted["athlete_id"], audience="medical")
    assert p.post(f"/api/v2/v2-reports/{med}/share-requests", json={}, headers=hp).status_code == 409
    req = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={"recipient_label": "Fictional club coach",
                                                                   "expires_in_days": 7}, headers=hp)
    assert req.status_code == 201 and req.json()["status"] == "pending"
    mine = a.get("/api/v2/portal/report-shares").json()["data"]
    assert len(mine) == 1 and COACH in str(mine[0]["content"]) and "CLINICAL-ONLY" not in str(mine[0]["content"])
    approved = a.post(f"/api/v2/portal/report-shares/{mine[0]['id']}/approve",
                      json={"recipient_email": "fictional.coach@example.org"}, headers=ha)
    assert approved.status_code == 200, approved.text
    url = approved.json()["data"]["url"]
    token = url.rsplit("/s/", 1)[1]
    coach = fresh_client(ss_app)  # no account
    page = coach.get(f"/s/{token}")
    assert page.status_code == 200 and COACH in page.text and "CLINICAL-ONLY" not in page.text
    assert coach.get(f"/api/v2/shared-reports/{token}").status_code == 200
    assert any(m["to"] == "fictional.coach@example.org" and f"/s/{token}" in m["body"] for m in ss_app.state.v2_outbox)
    assert a.post(f"/api/v2/portal/report-shares/{mine[0]['id']}/approve", json={}, headers=ha).status_code == 409
    assert a.post(f"/api/v2/portal/report-shares/{mine[0]['id']}/withdraw", headers=ha).status_code == 200
    assert coach.get(f"/s/{token}").status_code == 404
    views = p.get(f"/api/v2/v2-reports/{rid}/share-requests").json()[0]["views"]
    assert [v["outcome"] for v in views] == ["shown", "shown", "withdrawn"]
    assert coach.get("/s/" + "x" * 43).status_code == 404


def test_report_share_closes_when_revoked_or_the_link_ends(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    rid = _signed_coach_report(p, hp, accepted["athlete_id"])
    tokens = []
    for _ in range(2):
        sid = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hp).json()["id"]
        tokens.append((sid, a.post(f"/api/v2/portal/report-shares/{sid}/approve", json={},
                                   headers=ha).json()["data"]["url"].rsplit("/s/", 1)[1]))
    coach = fresh_client(ss_app)
    assert p.post(f"/api/v2/report-shares/{tokens[0][0]}/revoke", headers=hp).json()["status"] == "revoked"
    assert coach.get(f"/s/{tokens[0][1]}").status_code == 404
    assert coach.get(f"/s/{tokens[1][1]}").status_code == 200
    assert p.post(f"/api/v2/loop/links/{accepted['link_id']}/end", headers=hp).status_code == 200
    assert coach.get(f"/s/{tokens[1][1]}").status_code == 404
    declined = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hp)
    assert declined.status_code == 409  # no active link any more


def test_athlete_can_decline_and_nothing_is_minted(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    rid = _signed_coach_report(p, hp, accepted["athlete_id"])
    sid = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hp).json()["id"]
    assert a.post(f"/api/v2/portal/report-shares/{sid}/decline", headers=ha).json()["data"]["status"] == "declined"
    assert a.post(f"/api/v2/portal/report-shares/{sid}/approve", json={}, headers=ha).status_code == 409
    x, hx = portal_user(ss_app, "not.the.athlete@example.org", "athlete")
    assert x.get("/api/v2/portal/report-shares").json()["data"] == []
    assert x.post(f"/api/v2/portal/report-shares/{sid}/decline", headers=hx).status_code == 404


# ------------------------------------------------------------------ flags
def test_self_serve_is_off_by_default_and_needs_loop_and_directory(app, v2env):
    c = fresh_client(app)
    for method, path in (("get", "/api/v2/portal/enquiries"), ("get", "/api/v2/portal/invitations"),
                         ("post", "/api/v2/portal/account/delete"), ("post", "/api/v2/directory/enquiries/1/accept"),
                         ("get", "/s/" + "a" * 43), ("get", "/api/v2/portal/report-shares")):
        assert getattr(c, method)(path).status_code == 404, path


def test_self_serve_flag_alone_does_not_switch_anything_on(v2env, tmp_path, fake_supabase, monkeypatch):
    monkeypatch.setenv("SPPS_SELF_SERVE_ENABLED", "true")
    from test_support.app import create_app
    app = create_app(f"sqlite:///{tmp_path / 'ss-off.db'}", init_database=True, seed_demo=True)
    try:
        c = fresh_client(app)
        assert c.get("/api/v2/portal/invitations").status_code == 404
        assert c.post("/api/v2/portal/auth/reactivate", json={"email": "x@example.org"}).status_code == 404
    finally:
        app.state.engine.dispose()


def test_portal_and_public_handlers_never_touch_clinical_models():
    """Hard limit 6: the account, invitation, report-share and public link handlers use identity tables only (the
    account enquiry goes through the same anonymous directory insert the public form uses)."""
    import ast
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "server" / "v2" / "self_serve.py").read_text()
    tree = ast.parse(src)
    helpers = {"send_account_enquiry", "request_json", "open_invites", "check_password", "export_requests", "my_share",
               "decide", "open_share", "share_json"}
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        paths = [d.args[0].value for d in node.decorator_list if isinstance(d, ast.Call) and d.args
                 and isinstance(d.args[0], ast.Constant)]
        portal = any(p.startswith(("/api/v2/portal", "/s/", "/api/v2/shared-reports")) for p in paths)
        if portal or node.name in helpers:
            body = ast.get_source_segment(src, node)
            for name in ("Athlete", "AthleteProfile", "SharedReport", "DirectoryEnquiry", "athlete_for", "Note", "Case"):
                assert not re.search(rf"\b{name}\b", body), (node.name, name)
            checked += 1
    assert checked >= 20
