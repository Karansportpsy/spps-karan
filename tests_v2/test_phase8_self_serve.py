"""Phase 8 acceptance and negative tests (API). PostgreSQL row-level security is in tests_v2_pg/test_phase8_rls.py.

Acceptance: an unlinked athlete account exposes nothing to any practitioner; deletion removes identity data and
severs links; every step works from a different browser (separate cookie jars stand in for Gmail -> Chrome -> app).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from .conftest import PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete
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



def linked_adult(app, email="self.serve@example.org"):
    """Practitioner invites -> the athlete (a different browser, as from the Android app) accepts. Returns everything."""
    p, hp, owner, ho, pid = _published(app)
    aid = make_athlete(p, hp, code="SS-1", name="Fictional Self-Serve Athlete")
    inv = p.post(f"/api/v2/loop/athletes/{aid}/invite", json={"email": email}, headers=hp)
    assert inv.status_code == 201, inv.text
    portal_user(app, email, "athlete", name="Fictional Self-Serve Athlete")
    other = fresh_client(app)  # Chrome opened from the Android app: a different browser, signs in again
    h2 = {"X-CSRF-Token": other.post("/api/v2/portal/auth/login", json={"email": email, "password": PASSWORD}).json()["csrf"]}
    pending = other.get("/api/v2/portal/invitations").json()["data"]
    assert len(pending) == 1 and pending[0]["practitioner_name"]
    ok = other.post(f"/api/v2/portal/invitations/{pending[0]['id']}/accept", json={"consent": True}, headers=h2)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "active", ok.text
    return p, hp, pid, {"athlete_id": aid, "link_id": inv.json()["link_id"]}, other, h2


# ------------------------------------------------------------------ 2. account first; nothing shared until both accept
def test_an_unlinked_account_exposes_nothing_to_any_practitioner(ss_app):
    p, hp, owner, ho, pid = _published(ss_app)
    a, ha = portal_user(ss_app, "quiet.account@example.org", "athlete", name="Fictional Quiet Account")
    for who, creds in (("b", PRACTITIONER_B), ("c", PRACTITIONER_C)):
        x = fresh_client(ss_app)
        login(x, creds)
        assert x.get("/api/v2/loop/links").json() == []
        assert x.get("/api/v2/directory/enquiries").status_code == 404, "there is no enquiry inbox any more"
        assert all(r.get("display_name") != "Fictional Quiet Account" for r in x.get("/api/athletes").json())
    # The owner's directory overview never carries account data either.
    assert "quiet.account" not in owner.get("/api/v2/admin/directory").text


# ------------------------------------------------------------------ 3. switch practitioner
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


def test_export_contains_the_accounts_own_data_and_no_enquiries(ss_app):
    p, hp, pid, accepted, a, ha = linked_adult(ss_app)
    out = a.get("/api/v2/portal/export").json()
    assert out["format"] == "spps-athlete-export-v1" and "enquiries" not in out
    assert out["links"] and out["account"]["email"] == "self.serve@example.org"


def test_deleting_the_account_removes_identity_data_and_severs_every_link(ss_app):
    from server.v2.identity_models import AthleteAccount, AthleteSession, LoopInvite, LoopLink
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
    """Hard limit 6: the account, invitation, report-share and public link handlers use identity tables only."""
    import ast
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "server" / "v2" / "self_serve.py").read_text()
    tree = ast.parse(src)
    helpers = {"open_invites", "check_password", "export_requests", "my_share",
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
            for name in ("Athlete", "AthleteProfile", "SharedReport", "athlete_for", "Note", "Case"):
                assert not re.search(rf"\b{name}\b", body), (node.name, name)
            checked += 1
    assert checked >= 16  # enquiry handlers were removed, so fewer remain
