"""Phase 8 on PostgreSQL: an unlinked account exposes nothing to any practitioner, an account sees only its own
report shares are visible only to the practitioner who asked and the right approver while the link
is open, only the server-side auth role can delete identity data, and the whole self-serve flow runs over
PostgreSQL with every real flag on."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from .conftest import as_identity, as_role, denied, pg_login, q
from .test_phase6_rls import _seed_link, _token
from .test_phase7_rls import _snapshot


def _account(id_superuser, role="athlete"):
    tag = uuid.uuid4().hex[:8]
    return q(id_superuser, """INSERT INTO spps_identity_dev.loop_accounts(email,password_hash,display_name,role,status,
             created_at) VALUES (%(e)s,'x','Fictional',%(r)s,'active',now()) RETURNING id""",
             {"e": f"p8.{tag}@example.org", "r": role})[0][0]


def test_unlinked_account_is_invisible_to_practitioners_and_other_accounts(pgenv, people, id_superuser):
    mine, other = _account(id_superuser), _account(id_superuser)
    with as_identity(pgenv, "spps_identity_portal", account=mine) as a:
        assert denied(a, "SELECT * FROM loop_accounts LIMIT 1")  # portal role has no grant on accounts at all
    with as_identity(pgenv, "spps_identity_portal", account=other) as o:
        assert q(o, "SELECT count(*) FROM loop_links WHERE athlete_account_id=%(a)s", {"a": mine})[0][0] == 0
    for person in (people["a"], people["b"], people["owner"]):
        with as_identity(pgenv, "spps_identity_portal", practitioner=person) as p:
            assert q(p, "SELECT count(*) FROM loop_links WHERE athlete_account_id=%(a)s OR guardian_account_id=%(a)s",
                     {"a": mine})[0][0] == 0
            assert denied(p, "SELECT * FROM loop_accounts LIMIT 1")


def _share(id_superuser, link, person, approver="athlete"):
    return q(id_superuser, """INSERT INTO spps_identity_dev.loop_share_requests(link_id,tenant_id,practitioner_id,
             report_id,audience,title,recipient_label,snapshot,approver_role,expires_in_days,status,created_at)
             VALUES (%(l)s,%(t)s,%(p)s,1,'coach','Fictional report','','{}',%(ap)s,7,'pending',now()) RETURNING id""",
             {"l": link, "t": person["tenant_id"], "p": person["id"], "ap": approver})[0][0]


def test_report_shares_visible_only_to_the_asking_practitioner_and_the_right_approver(pgenv, people, id_superuser):
    link, acc, *_ = _seed_link(id_superuser, people)
    sid = _share(id_superuser, link, people["b"])
    with as_identity(pgenv, "spps_identity_portal", practitioner=people["b"]) as b:
        assert q(b, "SELECT count(*) FROM loop_share_requests WHERE id=%(s)s", {"s": sid})[0][0] == 1
    with as_identity(pgenv, "spps_identity_portal", practitioner=people["a"]) as a:
        assert q(a, "SELECT count(*) FROM loop_share_requests WHERE id=%(s)s", {"s": sid})[0][0] == 0
        assert denied(a, """INSERT INTO loop_share_requests(link_id,tenant_id,practitioner_id,report_id,audience,title,
                            snapshot,approver_role,expires_in_days,status,created_at)
                            VALUES (%(l)s,%(t)s,%(p)s,1,'coach','x','{}','athlete',7,'pending',now())""",
                      {"l": link, "t": people["a"]["tenant_id"], "p": people["a"]["id"]})
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as ath:
        assert q(ath, "SELECT count(*) FROM loop_share_requests WHERE id=%(s)s", {"s": sid})[0][0] == 1
        assert denied(ath, """INSERT INTO loop_share_requests(link_id,tenant_id,practitioner_id,report_id,audience,title,
                              snapshot,approver_role,expires_in_days,status,created_at)
                              VALUES (%(l)s,1,1,1,'coach','x','{}','athlete',7,'pending',now())""", {"l": link})
    for who in ("guardian", "other"):  # an adult's report: the guardian is not the approver
        with as_identity(pgenv, "spps_identity_portal", account=acc[who]) as o:
            assert q(o, "SELECT count(*) FROM loop_share_requests WHERE id=%(s)s", {"s": sid})[0][0] == 0, who
    q(id_superuser, "UPDATE spps_identity_dev.loop_links SET status='ended', ended_at=now() WHERE id=%(l)s", {"l": link})
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as ath:
        assert q(ath, "SELECT count(*) FROM loop_share_requests WHERE id=%(s)s", {"s": sid})[0][0] == 0


def test_approved_share_needs_a_token_and_expiry(pgenv, people, id_superuser):
    link, acc, *_ = _seed_link(id_superuser, people)
    sid = _share(id_superuser, link, people["b"])
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as ath:
        assert denied(ath, "UPDATE loop_share_requests SET status='approved' WHERE id=%(s)s", {"s": sid})
    approve = ("UPDATE loop_share_requests SET status='approved', token_hash=%(h)s, decided_at=now(), "
               "expires_at=now()+interval '7 days', decided_by_account=%(a)s WHERE id=%(s)s")
    args = {"s": sid, "h": uuid.uuid4().hex, "a": acc["athlete"]}
    with as_identity(pgenv, "spps_identity_portal", practitioner=people["b"]) as b:  # cannot approve for the athlete
        assert denied(b, approve, args)
        assert denied(b, "UPDATE loop_share_requests SET snapshot='{\"x\": 1}' WHERE id=%(s)s", {"s": sid})
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as ath:
        assert denied(ath, approve, {**args, "a": acc["other"]})  # must be recorded as their own decision
        assert denied(ath, "UPDATE loop_share_requests SET status='revoked' WHERE id=%(s)s", {"s": sid})
        with ath.cursor() as cur:
            cur.execute(approve, args)
            assert cur.rowcount == 1


def test_only_the_auth_role_deletes_identity_data_and_portal_cannot_reach_clinical(pgenv, people, id_superuser):
    acc = _account(id_superuser)
    with as_identity(pgenv, "spps_identity_portal", account=acc) as p:
        assert denied(p, "DELETE FROM loop_accounts WHERE id=%(a)s", {"a": acc})
        for t in ("athletes", "shared_reports", "report_consents"):
            assert denied(p, f"SELECT * FROM spps_dev.{t} LIMIT 1"), t
    with as_identity(pgenv, "spps_identity_auth") as auth:
        with auth.cursor() as cur:
            cur.execute("DELETE FROM loop_accounts WHERE id=%(a)s", {"a": acc})
            assert cur.rowcount == 1


# ------------------------------------------------------------------ the whole flow over PostgreSQL
@pytest.fixture
def ss_pg_app(pgenv, monkeypatch, tmp_path, loop_pg_app):
    loop_pg_app.state.engine.dispose()
    for k in ("SPPS_DIRECTORY_ENABLED", "SPPS_SELF_SERVE_ENABLED", "SPPS_REPORT_LINKS_ENABLED"):
        monkeypatch.setenv(k, "true")
    monkeypatch.setenv("SPPS_CRISIS_CONTACTS_TEXT", "Fictional helpline 0000 (24/7).")
    import server.main as main
    app = main.create_app(None, init_database=False)
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()


def test_full_self_serve_flow_over_postgresql_with_every_real_flag(ss_pg_app, people, superuser):
    _snapshot(superuser, people["b"])
    tag = uuid.uuid4().hex[:6]
    email = f"pg.selfserve.{tag}@example.org"
    a = TestClient(ss_pg_app)
    assert a.post("/api/v2/portal/auth/register", json={"email": email, "password": "Fictional-pass-2026",
                  "display_name": "Fictional PG Self", "role": "athlete", "terms_accepted": True}).status_code == 202
    assert TestClient(ss_pg_app).post("/api/v2/portal/auth/verify",
                                      json={"token": _token(ss_pg_app, email, "verify")}).status_code == 200
    ha = {"X-CSRF-Token": a.post("/api/v2/portal/auth/login", json={"email": email,
                                 "password": "Fictional-pass-2026"}).json()["csrf"]}
    p = TestClient(ss_pg_app)
    hp = pg_login(p, people["b"])
    athlete_id = p.post("/api/athletes", json={"code": f"PG8-{tag}", "display_name": "Fictional PG Self", "sport": "Hockey",
                                               "service_consent_recorded": True}, headers=hp).json()["id"]
    inv_sent = p.post(f"/api/v2/loop/athletes/{athlete_id}/invite", json={"email": email}, headers=hp)
    assert inv_sent.status_code == 201, inv_sent.text
    link_id = inv_sent.json()["link_id"]
    chrome = TestClient(ss_pg_app)  # Chrome from the Android app
    hc = {"X-CSRF-Token": chrome.post("/api/v2/portal/auth/login", json={"email": email,
                                      "password": "Fictional-pass-2026"}).json()["csrf"]}
    inv = chrome.get("/api/v2/portal/invitations").json()["data"]
    ok = chrome.post(f"/api/v2/portal/invitations/{inv[0]['id']}/accept", json={"consent": True}, headers=hc)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "active", ok.text
    # Athlete-approved coach link over PostgreSQL.
    tpl = p.post("/api/v2/report-templates", json={"name": f"PG coach {tag}", "fields": [
        {"key": "summary", "label": "Summary", "kind": "narrative", "audiences": ["coach"]},
        {"key": "clinical", "label": "Clinical", "kind": "narrative", "audiences": []}]}, headers=hp).json()
    r = p.post(f"/api/v2/athletes/{athlete_id}/v2-reports", json={"template_id": tpl["id"], "audience": "coach",
                                                                  "title": "Fictional PG update"}, headers=hp).json()
    p.put(f"/api/v2/v2-reports/{r['id']}", json={"values": {"summary": "PG-COACH-TEXT", "clinical": "PG-CLINICAL"},
                                                 "expected_revision": 1}, headers=hp)
    assert p.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=hp).status_code == 200
    share = p.post(f"/api/v2/v2-reports/{r['id']}/share-requests", json={}, headers=hp)
    assert share.status_code == 201, share.text
    approved = chrome.post(f"/api/v2/portal/report-shares/{share.json()['id']}/approve", json={}, headers=hc)
    assert approved.status_code == 200, approved.text
    token = approved.json()["data"]["url"].rsplit("/s/", 1)[1]
    page = TestClient(ss_pg_app).get(f"/s/{token}")
    assert page.status_code == 200 and "PG-COACH-TEXT" in page.text and "PG-CLINICAL" not in page.text
    # Self-service deletion: identity data gone, link severed, coach link closed.
    gone = chrome.post("/api/v2/portal/account/delete", json={"password": "Fictional-pass-2026", "confirm": "DELETE"},
                       headers=hc)
    assert gone.status_code == 200, gone.text
    assert TestClient(ss_pg_app).get(f"/s/{token}").status_code == 404
    assert next(x for x in p.get("/api/v2/loop/links").json() if x["id"] == link_id)["status"] == "ended"
    assert TestClient(ss_pg_app).post("/api/v2/portal/auth/login", json={"email": email,
                                      "password": "Fictional-pass-2026"}).status_code == 401
