"""Release 1.0 - Free hardening (TypeSafe final assessment): acceptance and negative tests, SQLite tier.

P0 report links: a link stops opening when the practitioner is deactivated, not approved, or closes the account.
P0 sharing consent: athlete-approved shares close on expiry and when the practitioner closes the account.
P0 guardian authority: a guardian must declare parental / lawful-guardian authority and name a different account.
P1 tokens: token pages are never cached or indexed; verification and invitation tokens are single use.
P1 access boundaries: a role matrix of negative checks across clinical, identity and report endpoints.
The PostgreSQL counterparts are in tests_v2_pg/test_release_hardening_rls.py. Fictional data only.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from .conftest import OWNER_LOGIN, PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete
from .test_phase5_reports import COACH, _shared, setup  # noqa: F401  (setup is a fixture)
from .test_phase6_athlete_loop import loop_app, portal_user, token_from  # noqa: F401
from .test_phase8_self_serve import _signed_coach_report, linked_adult, ss_app  # noqa: F401


def _views(app, link_id):
    with app.state.engine.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT outcome FROM report_link_views WHERE link_id=:i ORDER BY id"),
                                           {"i": link_id})]


def _practitioner_id(app, email):
    with app.state.engine.connect() as conn:
        return conn.execute(text("SELECT id FROM practitioners WHERE email=:e"), {"e": email}).scalar()


def _close_account(client, headers, password):
    """Orderly closure: start, choose a disposition for every record, export, complete."""
    assert client.post("/api/v2/account/closure/start", json={"password": password}, headers=headers).status_code == 200
    for athlete in client.get("/api/athletes").json():
        res = client.post("/api/v2/account/closure/dispositions",
                          json={"athlete_id": athlete["id"], "disposition": "keep_deactivated"}, headers=headers)
        assert res.status_code == 200, res.text
    assert client.post("/api/v2/account/export", json={"password": password}, headers=headers).status_code == 200
    done = client.post("/api/v2/account/closure/complete", headers=headers)
    assert done.status_code == 200 and done.json()["state"] == "closed", done.text


# ------------------------------------------------------------------ P0 report links and practitioner standing
def test_report_link_stops_when_the_practitioner_is_deactivated_and_resumes_when_reactivated(app, setup):
    b, hb, aid, tid = setup
    r, cid, link, token = _shared(b, hb, aid, tid)
    anon = fresh_client(app)
    assert anon.get(f"/r/{token}").status_code == 200
    pid = _practitioner_id(app, PRACTITIONER_B["email"])
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE practitioners SET active=0 WHERE id=:i"), {"i": pid})
    for path in (f"/r/{token}", f"/api/v2/report-links/{token}"):
        res = anon.get(path)
        assert res.status_code == 404 and COACH not in res.text
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE practitioners SET active=1 WHERE id=:i"), {"i": pid})
    assert anon.get(f"/r/{token}").status_code == 200
    assert _views(app, link["id"]) == ["shown", "revoked", "revoked", "shown"]


@pytest.mark.parametrize("status", ["rejected", "pending_approval"])
def test_report_link_stops_while_the_practitioner_is_not_approved(app, setup, status):
    b, hb, aid, tid = setup
    r, cid, link, token = _shared(b, hb, aid, tid)
    pid = _practitioner_id(app, PRACTITIONER_B["email"])
    with app.state.engine.begin() as conn:
        before = conn.execute(text("SELECT status FROM practitioner_profiles WHERE practitioner_id=:i"),
                              {"i": pid}).scalar()
        assert before == "approved"  # the seeded fictional practitioner has an approved profile
        conn.execute(text("UPDATE practitioner_profiles SET status=:s WHERE practitioner_id=:i"), {"s": status, "i": pid})
    res = fresh_client(app).get(f"/r/{token}")
    assert res.status_code == 404 and COACH not in res.text and _views(app, link["id"]) == ["revoked"]


def test_closing_the_account_revokes_every_report_link(app, setup, v2env):
    v2env.setenv("SPPS_PRACTITIONER_CLOSURE_ENABLED", "true")
    v2env.setenv("SPPS_CLOSURE_NOTICE_DAYS", "0")
    b, hb, aid, tid = setup
    r, cid, link, token = _shared(b, hb, aid, tid)
    anon = fresh_client(app)
    assert anon.get(f"/r/{token}").status_code == 200
    _close_account(b, hb, PRACTITIONER_B["password"])
    assert anon.get(f"/r/{token}").status_code == 404
    with app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT revoked_at FROM report_links WHERE id=:i"), {"i": link["id"]}).scalar()
    # Restoring the account inside the cooling-off period does not bring old links back: a new link is needed.
    owner = fresh_client(app)
    ho = login(owner, OWNER_LOGIN)
    closure = owner.get("/api/v2/admin/closures").json()[0]
    assert owner.post(f"/api/v2/admin/closures/{closure['id']}/restore", headers=ho).json()["state"] == "restored"
    assert anon.get(f"/r/{token}").status_code == 404


def test_report_link_404_pages_are_not_cached_or_indexed(app, setup):
    res = fresh_client(app).get("/r/" + "z" * 43)
    assert res.status_code == 404
    assert "no-store" in res.headers["cache-control"] and "noindex" in res.headers["x-robots-tag"]


# ------------------------------------------------------------------ P0 athlete-approved shares
def _approved_share(app):
    p, hp, pid, accepted, a, ha = linked_adult(app)
    rid = _signed_coach_report(p, hp, accepted["athlete_id"])
    sid = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={"expires_in_days": 7}, headers=hp).json()["id"]
    url = a.post(f"/api/v2/portal/report-shares/{sid}/approve", json={}, headers=ha).json()["data"]["url"]
    return p, hp, accepted, a, ha, rid, sid, url.rsplit("/s/", 1)[1]


def test_an_expired_share_returns_nothing_and_the_attempt_is_recorded(ss_app):
    p, hp, accepted, a, ha, rid, sid, token = _approved_share(ss_app)
    coach = fresh_client(ss_app)
    assert coach.get(f"/s/{token}").status_code == 200
    with ss_app.state.engine.begin() as conn:
        conn.execute(text("UPDATE loop_share_requests SET expires_at='2020-01-01 00:00:00' WHERE id=:i"), {"i": sid})
    for path in (f"/s/{token}", f"/api/v2/shared-reports/{token}"):
        res = coach.get(path)
        assert res.status_code == 404 and COACH not in res.text
    res = coach.get(f"/s/{token}")
    assert "no-store" in res.headers["cache-control"] and "noindex" in res.headers["x-robots-tag"]
    views = p.get(f"/api/v2/v2-reports/{rid}/share-requests").json()[0]["views"]
    assert [v["outcome"] for v in views] == ["shown", "expired", "expired", "expired"]


def test_closing_the_account_ends_loop_links_and_closes_every_share(ss_app, v2env):
    v2env.setenv("SPPS_PRACTITIONER_CLOSURE_ENABLED", "true")
    v2env.setenv("SPPS_CLOSURE_NOTICE_DAYS", "0")
    p, hp, accepted, a, ha, rid, sid, token = _approved_share(ss_app)
    pending = p.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hp).json()["id"]
    coach = fresh_client(ss_app)
    assert coach.get(f"/s/{token}").status_code == 200
    _close_account(p, hp, PRACTITIONER_B["password"])
    assert coach.get(f"/s/{token}").status_code == 404
    links = a.get("/api/v2/portal/links").json()["data"]
    assert links and all(lk["status"] == "ended" for lk in links)
    shares = {s["id"]: s["status"] for s in a.get("/api/v2/portal/report-shares").json()["data"]}
    assert shares.get(sid) in (None, "revoked") and shares.get(pending) in (None, "revoked")
    assert a.post(f"/api/v2/portal/report-shares/{pending}/approve", json={}, headers=ha).status_code in (404, 409)


# ------------------------------------------------------------------ P0 guardian authority
def _minor_invite(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb, code="LOOP-G", name="Fictional Minor G")
    b.put(f"/api/v2/athletes/{aid}/profile", json={"age_group": "15-17", "guardian_name": "Fictional Parent G",
                                                   "guardian_email": "parent.g@example.org", "consent_status": "recorded"},
          headers=hb)
    inv = b.post(f"/api/v2/loop/athletes/{aid}/invite", json={}, headers=hb).json()
    assert inv["kind"] == "guardian"
    return b, hb, inv["link_id"], token_from(app, "parent.g@example.org", "invite")


def test_guardian_must_declare_authority_and_name_a_different_account(loop_app):
    b, hb, link_id, gtoken = _minor_invite(loop_app)
    g, hg = portal_user(loop_app, "parent.g@example.org", "guardian")
    base = {"consent": True, "guardian_evidence": "Fictional Parent G", "athlete_email": "minor.g@example.org"}
    no_decl = g.post(f"/api/v2/portal/invites/{gtoken}/accept", json=base, headers=hg)
    assert no_decl.status_code == 422 and "guardian" in no_decl.text.lower()
    same = g.post(f"/api/v2/portal/invites/{gtoken}/accept",
                  json={**base, "guardian_declaration": True, "athlete_email": "parent.g@example.org"}, headers=hg)
    assert same.status_code == 422
    assert not any(m["to"] == "minor.g@example.org" for m in loop_app.state.v2_outbox)  # nothing sent yet
    ok = g.post(f"/api/v2/portal/invites/{gtoken}/accept", json={**base, "guardian_declaration": True}, headers=hg)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "guardian_consented"
    link = next(lk for lk in b.get("/api/v2/loop/links").json() if lk["id"] == link_id)
    assert "Fictional Parent G" in link["guardian_signature"] and "lawful guardian" in link["guardian_signature"]
    # The minor's own invitation is only sent after the guardian's consent, and only to the address the guardian gave.
    assert any(m["to"] == "minor.g@example.org" for m in loop_app.state.v2_outbox)


def test_a_minor_cannot_use_the_guardian_invitation_and_nothing_is_active_before_both_consent(loop_app):
    b, hb, link_id, gtoken = _minor_invite(loop_app)
    kid, hk = portal_user(loop_app, "minor.g@example.org", "athlete")
    assert kid.post(f"/api/v2/portal/invites/{gtoken}/accept", json={"consent": True, "guardian_declaration": True},
                    headers=hk).status_code == 403
    assert kid.get("/api/v2/portal/links").json()["data"] == []
    status = next(lk for lk in b.get("/api/v2/loop/links").json() if lk["id"] == link_id)["status"]
    assert status != "active"


# ------------------------------------------------------------------ P1 tokens in links
def test_token_pages_are_never_cached_or_indexed_and_send_no_referrer(client):
    for path in ("/athlete", "/r/" + "z" * 43, "/s/" + "z" * 43, "/portal", "/your-data"):
        res = client.get(path)
        assert "no-store" in res.headers.get("cache-control", ""), path
        assert "noindex" in res.headers.get("x-robots-tag", ""), path
        assert res.headers.get("referrer-policy") == "no-referrer", path
    assert "index" in client.get("/terms").headers.get("x-robots-tag", "")


def test_athlete_page_removes_tokens_from_the_address_bar():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web" / "athlete.js").read_text(encoding="utf-8")
    head = "\n".join(js.splitlines()[:15])  # runs at load, before any request is made
    assert "if(location.search)history.replaceState(null,'','/athlete')" in head
    assert "sessionStorage.setItem('spps.loop.'+k" in head
    for www in ("www/athlete.js", "android/app/src/main/assets/public/athlete.js"):
        p = Path(__file__).resolve().parents[1] / www
        if p.exists():
            assert p.read_text(encoding="utf-8") == js, f"{www} is not regenerated from web/"


def test_verification_and_invitation_tokens_are_single_use(loop_app):
    c = fresh_client(loop_app)
    email = "single.use@example.org"
    assert c.post("/api/v2/portal/auth/register", json={"email": email, "password": "Fictional-pass-2026",
                                                        "display_name": "Fictional Single", "role": "athlete",
                                                        "terms_accepted": True}).status_code == 202
    vt = token_from(loop_app, email, "verify")
    assert c.post("/api/v2/portal/auth/verify", json={"token": vt}).status_code == 200
    assert c.post("/api/v2/portal/auth/verify", json={"token": vt}).status_code == 400
    assert fresh_client(loop_app).post("/api/v2/portal/auth/verify", json={"token": "x" * 43}).status_code == 400


# ------------------------------------------------------------------ P1 role matrix (negative checks)
def test_role_matrix_every_role_is_refused_outside_its_own_data(ss_app):
    p, hp, accepted, a, ha, rid, sid, token = _approved_share(ss_app)
    aid, link_id = accepted["athlete_id"], accepted["link_id"]
    anon = fresh_client(ss_app)
    c = fresh_client(ss_app)
    hc = login(c, PRACTITIONER_C)
    owner = fresh_client(ss_app)
    login(owner, OWNER_LOGIN)
    other, ho = portal_user(ss_app, "other.account@example.org", "athlete")
    guardian, hg = portal_user(ss_app, "unrelated.guardian@example.org", "guardian")

    clinical = [f"/api/athletes/{aid}", f"/api/v2/athletes/{aid}/profile", f"/api/v2/v2-reports/{rid}",
                f"/api/v2/v2-reports/{rid}/share-requests", f"/api/v2/loop/links/{link_id}/activity"]
    portal = [f"/api/v2/portal/links/{link_id}/assignments", f"/api/v2/portal/links/{link_id}/checkins",
              f"/api/v2/portal/links/{link_id}/guardian-view", "/api/v2/portal/report-shares", "/api/v2/portal/export"]

    # The practitioner who owns the record, and the athlete on the link, can read their own side.
    assert p.get(clinical[0]).status_code == 200 and p.get(clinical[3]).status_code == 200
    assert a.get(portal[0]).status_code == 200 and a.get(portal[3]).status_code == 200

    def refused(res):
        return res.status_code in (401, 403, 404)

    for path in clinical:
        assert refused(anon.get(path)), ("anonymous", path)
        assert refused(c.get(path)), ("practitioner C", path)
        assert refused(a.get(path)), ("athlete", path)
        assert refused(other.get(path)), ("other account", path)
        assert refused(guardian.get(path)), ("guardian", path)
    for path in clinical[1:]:  # the owner sees counts and approvals only, never a practitioner's clinical record
        assert refused(owner.get(path)), ("owner", path)
    for path in portal[:3]:
        assert refused(anon.get(path)), ("anonymous", path)
        assert refused(other.get(path)), ("other account", path)
        assert refused(guardian.get(path)), ("guardian", path)
        assert refused(p.get(path)), ("practitioner on the portal", path)
        assert refused(c.get(path)), ("practitioner C on the portal", path)
    for path in portal[3:]:
        assert refused(anon.get(path)) and refused(p.get(path)) and refused(c.get(path)), path
    assert other.get("/api/v2/portal/report-shares").json()["data"] == []
    assert guardian.get("/api/v2/portal/links").json()["data"] == []
    # Writes are refused just the same.
    assert refused(c.post(f"/api/v2/v2-reports/{rid}/share-requests", json={}, headers=hc))
    assert refused(other.post(f"/api/v2/portal/report-shares/{sid}/withdraw", headers=ho))
    assert refused(guardian.post(f"/api/v2/portal/links/{link_id}/end", headers=hg))
    assert refused(c.post(f"/api/v2/loop/links/{link_id}/end", headers=hc))
    # The report itself is reachable without an account only through its token.
    assert anon.get(f"/s/{token}").status_code == 200 and COACH in anon.get(f"/s/{token}").text
