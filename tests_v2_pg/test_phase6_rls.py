"""Phase 6 on PostgreSQL: athlete sees only own data, guardian only what is shared, practitioner only linked
athletes, ending a link removes access immediately, portal code never reaches clinical tables."""
from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient

from .conftest import as_identity, denied, pg_login, q


def _seed_link(id_superuser, people, *, minor=False):
    tag = uuid.uuid4().hex[:8]
    acc = {}
    for role in ("athlete", "guardian", "other"):
        acc[role] = q(id_superuser, """INSERT INTO spps_identity_dev.loop_accounts(email,password_hash,display_name,role,
                      status,created_at) VALUES (%(e)s,'x',%(n)s,%(r)s,'active',now()) RETURNING id""",
                      {"e": f"{role}.{tag}@example.org", "n": "Fictional", "r": "guardian" if role == "guardian" else "athlete"})[0][0]
    p = people["b"]
    link = q(id_superuser, """INSERT INTO spps_identity_dev.loop_links(tenant_id,practitioner_id,practitioner_name,
             practitioner_email,athlete_record_id,athlete_label,is_minor,athlete_account_id,guardian_account_id,status,scope,
             guardian_consent_at,athlete_consent_at,guardian_consent_evidence,guardian_detail_by_practitioner,
             guardian_detail_by_athlete,ended_by,created_at)
             VALUES (%(t)s,%(p)s,'Dr Fictional','',1,'Fictional',%(m)s,%(a)s,%(g)s,'active','[]',now(),now(),'x',false,
                     false,'',now()) RETURNING id""",
             {"t": p["tenant_id"], "p": p["id"], "m": minor, "a": acc["athlete"], "g": acc["guardian"]})[0][0]
    asg = q(id_superuser, """INSERT INTO spps_identity_dev.loop_assignments(link_id,kind,title,payload,schedule,due_on,
            status,created_at) VALUES (%(l)s,'programme','Fictional','{}','','','assigned',now()) RETURNING id""", {"l": link})[0][0]
    chk = q(id_superuser, """INSERT INTO spps_identity_dev.loop_checkins(link_id,due_on,answers,urgent,submitted_at)
            VALUES (%(l)s,%(d)s,'{"stress": 7}',false,now()) RETURNING id""", {"l": link, "d": "2026-11-01"})[0][0]
    q(id_superuser, """INSERT INTO spps_identity_dev.loop_shared_summaries(link_id,title,body,for_guardian,created_at)
                       VALUES (%(l)s,'Fictional summary','ok',true,now())""", {"l": link})
    return link, acc, asg, chk


def test_athlete_guardian_and_other_accounts_see_exactly_their_share(pgenv, people, id_superuser):
    link, acc, asg, chk = _seed_link(id_superuser, people)
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as a:
        assert q(a, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 1
        assert q(a, "SELECT count(*) FROM loop_assignments WHERE link_id=%(l)s", {"l": link})[0][0] == 1
    with as_identity(pgenv, "spps_identity_portal", account=acc["guardian"]) as g:
        assert q(g, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 0  # no raw answers
        assert q(g, "SELECT count(*) FROM loop_submissions WHERE link_id=%(l)s", {"l": link})[0][0] == 0
        assert q(g, "SELECT count(*) FROM loop_shared_summaries WHERE link_id=%(l)s", {"l": link})[0][0] == 1
        assert q(g, "SELECT * FROM spps_identity_dev.v2_loop_participation(%(l)s)", {"l": link})[0] == (0, 1, 1)
        assert denied(g, "INSERT INTO loop_checkins(link_id,due_on,answers,urgent,submitted_at) "
                         "VALUES (%(l)s,'2026-01-01','{}',false,now())", {"l": link})
    with as_identity(pgenv, "spps_identity_portal", account=acc["other"]) as o:
        for t in ("loop_links", "loop_checkins", "loop_assignments", "loop_shared_summaries"):
            col = "id" if t == "loop_links" else "link_id"
            assert q(o, f"SELECT count(*) FROM {t} WHERE {col}=%(l)s", {"l": link})[0][0] == 0, t
        assert q(o, "SELECT * FROM spps_identity_dev.v2_loop_participation(%(l)s)", {"l": link}) == []


def test_guardian_sees_raw_answers_only_when_both_sides_enable_it(pgenv, people, id_superuser):
    link, acc, *_ = _seed_link(id_superuser, people, minor=True)
    q(id_superuser, "UPDATE spps_identity_dev.loop_links SET guardian_detail_by_practitioner=true WHERE id=%(l)s", {"l": link})
    with as_identity(pgenv, "spps_identity_portal", account=acc["guardian"]) as g:
        assert q(g, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 0
    q(id_superuser, "UPDATE spps_identity_dev.loop_links SET guardian_detail_by_athlete=true WHERE id=%(l)s", {"l": link})
    with as_identity(pgenv, "spps_identity_portal", account=acc["guardian"]) as g:
        assert q(g, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 1


def test_practitioner_sees_only_own_links(pgenv, people, id_superuser):
    link, *_ = _seed_link(id_superuser, people)
    with as_identity(pgenv, "spps_identity_portal", practitioner=people["b"]) as b:
        assert q(b, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 1
    with as_identity(pgenv, "spps_identity_portal", practitioner=people["a"]) as a:
        for t, col in (("loop_links", "id"), ("loop_checkins", "link_id"), ("loop_assignments", "link_id")):
            assert q(a, f"SELECT count(*) FROM {t} WHERE {col}=%(l)s", {"l": link})[0][0] == 0
        assert denied(a, "INSERT INTO loop_assignments(link_id,kind,title,payload,schedule,due_on,status,created_at) "
                         "VALUES (%(l)s,'programme','x','{}','','','assigned',now())", {"l": link})


def test_ending_a_link_removes_access_immediately(pgenv, people, id_superuser):
    link, acc, *_ = _seed_link(id_superuser, people)
    with as_identity(pgenv, "spps_identity_portal", account=acc["athlete"]) as a:
        with a.cursor() as cur:
            cur.execute("UPDATE loop_links SET status='ended', ended_at=now(), ended_by='athlete' WHERE id=%(l)s", {"l": link})
            assert cur.rowcount == 1
        # same transaction, next statement: nothing is visible any more
        assert q(a, "SELECT count(*) FROM loop_checkins WHERE link_id=%(l)s", {"l": link})[0][0] == 0
        assert q(a, "SELECT count(*) FROM loop_assignments WHERE link_id=%(l)s", {"l": link})[0][0] == 0
        a.commit()
    with as_identity(pgenv, "spps_identity_portal", account=acc["guardian"]) as g:
        assert q(g, "SELECT count(*) FROM loop_shared_summaries WHERE link_id=%(l)s", {"l": link})[0][0] == 0
        with g.cursor() as cur:
            cur.execute("UPDATE loop_links SET status='active' WHERE id=%(l)s", {"l": link})
            assert cur.rowcount == 0  # an ended link cannot be reopened from the portal


def test_portal_roles_cannot_reach_clinical_tables(pgenv, people):
    for role in ("spps_identity_portal", "spps_identity_auth"):
        with as_identity(pgenv, role, account=1) as conn:
            for t in ("athletes", "notes", "cases", "instrument_scores", "shared_reports"):
                assert denied(conn, f"SELECT * FROM spps_dev.{t} LIMIT 1"), (role, t)


def test_loop_source_never_imports_clinical_models_in_portal_handlers():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "server" / "v2" / "athlete_loop.py").read_text()
    portal = src[src.index("# ------------------------------------------------------------------ portal: public + auth"):
                 src.index("# ------------------------------------------------------------------ timeline")]
    for name in ("Athlete", "Note", "Case", "Observation", "athlete_for", "db_session", "InstrumentScore"):
        assert not re.search(rf"\b{name}\b", portal), name


def _token(app, to, kind):
    for m in reversed(app.state.v2_outbox):
        if m["to"] == to:
            found = re.search(kind + r"=([A-Za-z0-9_-]+)", m["body"])
            if found:
                return found.group(1)
    raise AssertionError(kind)


def test_full_loop_over_postgresql_with_both_real_flags(loop_pg_app, people):
    tag = uuid.uuid4().hex[:6]
    email = f"pg.athlete.{tag}@example.org"
    p = TestClient(loop_pg_app)
    h = pg_login(p, people["a"])
    aid = p.post("/api/athletes", json={"code": f"PG6-{tag}", "display_name": "Fictional PG Loop", "sport": "Tennis",
                                        "service_consent_recorded": True}, headers=h).json()["id"]
    inv = p.post(f"/api/v2/loop/athletes/{aid}/invite", json={"email": email}, headers=h)
    assert inv.status_code == 201, inv.text
    a = TestClient(loop_pg_app)  # another browser
    assert a.post("/api/v2/portal/auth/register", json={"email": email, "password": "Fictional-pass-2026",
                  "display_name": "Fictional", "role": "athlete", "terms_accepted": True}).status_code == 202
    assert a.post("/api/v2/portal/auth/verify", json={"token": _token(loop_pg_app, email, "verify")}).status_code == 200
    csrf = a.post("/api/v2/portal/auth/login", json={"email": email, "password": "Fictional-pass-2026"}).json()["csrf"]
    ha = {"X-CSRF-Token": csrf}
    token = _token(loop_pg_app, email, "invite")
    assert a.get(f"/api/v2/portal/invites/{token}").status_code == 200
    acc = a.post(f"/api/v2/portal/invites/{token}/accept", json={"consent": True}, headers=ha)
    assert acc.status_code == 200 and acc.json()["data"]["status"] == "active", acc.text
    link = inv.json()["link_id"]
    assert p.put(f"/api/v2/loop/links/{link}/checkin-plan", json={"frequency": "daily",
                 "items": ["stress", "mood", "confidence"]}, headers=h).status_code == 200
    assert p.post(f"/api/v2/loop/links/{link}/alert-rules", json={"item": "stress", "op": ">=", "threshold": 8},
                  headers=h).status_code == 201
    c = a.post(f"/api/v2/portal/links/{link}/checkins", json={"answers": {"stress": 9, "mood": 5, "confidence": 5}},
               headers=ha)
    assert c.status_code == 201, c.text
    act = p.get(f"/api/v2/loop/links/{link}/activity").json()
    assert act["alerts"] and act["checkins"][-1]["status"] == "submitted"
    other = TestClient(loop_pg_app)
    pg_login(other, people["b"])
    assert other.get(f"/api/v2/loop/links/{link}/activity").status_code == 404
    assert p.post(f"/api/v2/loop/links/{link}/end", headers=h).status_code == 200
    assert a.get(f"/api/v2/portal/links/{link}/checkins").status_code == 404
    assert a.get("/api/v2/portal/export").json()["links"][0]["status"] == "ended"
