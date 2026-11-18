"""Phase 5 on PostgreSQL: frozen signed reports, consent-bound links, anonymous access only through the function."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests_v2.test_phase5_reports import CLINICAL, COACH, MEDICAL

from .conftest import as_role, denied, pg_login, q, seed_athlete


def _signed_report(superuser, person, audience="coach"):
    rec = seed_athlete(superuser, person)
    t = person["tenant_id"]
    tid = q(superuser, """INSERT INTO spps_dev.report_templates(tenant_id,practitioner_id,name,fields)
                          VALUES (%(t)s,%(p)s,'T','[]') RETURNING id""", {"t": t, "p": person["id"]})[0][0]
    rid = q(superuser, """INSERT INTO spps_dev.shared_reports(tenant_id,practitioner_id,athlete_id,template_id,title,
                          audience,status,snapshot,content_sha256,signed_at)
                          VALUES (%(t)s,%(p)s,%(a)s,%(tid)s,'R',%(au)s,'signed','enc:v1:k1:x',repeat('a',64),now())
                          RETURNING id""", {"t": t, "p": person["id"], "a": rec["athlete_id"], "tid": tid,
                                            "au": audience})[0][0]
    return rec, rid


def test_signed_reports_cannot_be_edited_and_are_private(pgenv, people, superuser):
    rec, rid = _signed_report(superuser, people["b"])
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        with b.cursor() as cur:
            cur.execute("""UPDATE shared_reports SET "values"='{"x":"y"}' WHERE id=%(r)s""", {"r": rid})
            assert cur.rowcount == 0  # frozen once signed
    for who in ("a", "owner"):
        with as_role(pgenv, "spps_dev_app", people[who]) as conn:
            assert q(conn, "SELECT count(*) FROM shared_reports WHERE id=%(r)s", {"r": rid})[0][0] == 0


def test_link_rows_need_a_signed_report_and_a_matching_active_consent(pgenv, people, superuser):
    rec, rid = _signed_report(superuser, people["b"], "coach")
    pb, t = people["b"]["id"], people["b"]["tenant_id"]
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        medical = q(b, """INSERT INTO report_consents(tenant_id,practitioner_id,athlete_id,audience,given_by,method)
                          VALUES (%(t)s,%(p)s,%(a)s,'medical','athlete','written') RETURNING id""",
                    {"t": t, "p": pb, "a": rec["athlete_id"]})[0][0]
        link = """INSERT INTO report_links(tenant_id,practitioner_id,report_id,consent_id,token_hash,expires_at)
                  VALUES (%(t)s,%(p)s,%(r)s,%(c)s,repeat('b',64),now()+interval '7 days')"""
        assert denied(b, link, {"t": t, "p": pb, "r": rid, "c": medical})  # consent is for another audience
        coach = q(b, """INSERT INTO report_consents(tenant_id,practitioner_id,athlete_id,audience,given_by,method)
                        VALUES (%(t)s,%(p)s,%(a)s,'coach','athlete','written') RETURNING id""",
                  {"t": t, "p": pb, "a": rec["athlete_id"]})[0][0]
        assert denied(b, """INSERT INTO report_links(tenant_id,practitioner_id,report_id,consent_id,token_hash,expires_at)
                            VALUES (%(t)s,%(p)s,%(r)s,%(c)s,repeat('c',64),now()+interval '90 days')""",
                      {"t": t, "p": pb, "r": rid, "c": coach})  # longer than 31 days
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert denied(a, """INSERT INTO report_links(tenant_id,practitioner_id,report_id,consent_id,token_hash,expires_at)
                            VALUES (%(t)s,%(p)s,%(r)s,1,repeat('d',64),now()+interval '1 day')""",
                      {"t": people["a"]["tenant_id"], "p": people["a"]["id"], "r": rid})


def test_anonymous_reaches_a_report_only_through_the_function(pgenv, people, superuser):
    rec, rid = _signed_report(superuser, people["b"], "coach")
    t, pb = people["b"]["tenant_id"], people["b"]["id"]
    cid = q(superuser, """INSERT INTO spps_dev.report_consents(tenant_id,practitioner_id,athlete_id,audience,given_by,method)
                          VALUES (%(t)s,%(p)s,%(a)s,'coach','athlete','written') RETURNING id""",
            {"t": t, "p": pb, "a": rec["athlete_id"]})[0][0]
    hashes = {}
    for name, extra in (("ok", ""), ("expired", ""), ("revoked", "")):
        import secrets
        h = secrets.token_hex(32)
        hashes[name] = h
        q(superuser, f"""INSERT INTO spps_dev.report_links(tenant_id,practitioner_id,report_id,consent_id,token_hash,
                         expires_at,revoked_at,created_at) VALUES (%(t)s,%(p)s,%(r)s,%(c)s,%(h)s,
                         {"now() - interval '1 day'" if name == 'expired' else "now() + interval '7 days'"},
                         {"now()" if name == 'revoked' else "NULL"},
                         {"now() - interval '8 days'" if name == 'expired' else "now()"})""",
          {"t": t, "p": pb, "r": rid, "c": cid, "h": h})
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT count(*) FROM shared_reports")[0][0] == 0
        assert q(anon, "SELECT count(*) FROM report_links")[0][0] == 0
        assert q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": hashes["ok"]})[0][0] == "enc:v1:k1:x"
        assert q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": hashes["expired"]})[0][0] is None
        assert q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": hashes["revoked"]})[0][0] is None
        assert q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": "f" * 64})[0][0] is None
        anon.commit()
    q(superuser, "UPDATE spps_dev.report_consents SET withdrawn_at=now() WHERE id=%(c)s", {"c": cid})
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": hashes["ok"]})[0][0] is None
        anon.commit()
    outcomes = [r[0] for r in q(superuser, """SELECT outcome FROM spps_dev.report_link_views v
                                              JOIN spps_dev.report_links l ON l.id=v.link_id
                                              WHERE l.report_id=%(r)s ORDER BY v.id""", {"r": rid})]
    assert outcomes == ["shown", "expired", "revoked", "withdrawn"]
    for role in ("spps_dev_auth", "spps_identity_portal"):
        with as_role(pgenv, role) as conn:
            assert denied(conn, "SELECT spps_dev.v2_open_report_link(%(h)s)", {"h": hashes["ok"]})


def test_coach_redaction_and_link_over_rls(pg_app, people, monkeypatch):
    monkeypatch.setenv("SPPS_REPORT_LINKS_ENABLED", "true")
    c = TestClient(pg_app)
    h = pg_login(c, people["a"])
    aid = c.post("/api/athletes", json={"code": "PG5-1", "display_name": "Fictional Report Athlete", "sport": "Judo",
                                        "service_consent_recorded": True}, headers=h).json()["id"]
    tpl = c.post("/api/v2/report-templates", json={"name": "PG template", "fields": [
        {"key": "summary", "label": "Summary", "kind": "narrative", "audiences": ["coach"]},
        {"key": "clinical", "label": "Clinical", "kind": "narrative", "audiences": []},
        {"key": "medical", "label": "Medical", "kind": "narrative", "audiences": ["medical"]}]}, headers=h)
    assert tpl.status_code == 201, tpl.text
    r = c.post(f"/api/v2/athletes/{aid}/v2-reports", json={"template_id": tpl.json()["id"], "audience": "coach",
                                                           "title": "Fictional PG report"}, headers=h).json()
    c.put(f"/api/v2/v2-reports/{r['id']}", json={"values": {"summary": COACH, "clinical": CLINICAL, "medical": MEDICAL},
                                                 "expected_revision": 1}, headers=h)
    s = c.post(f"/api/v2/v2-reports/{r['id']}/sign", json={"expected_revision": 2}, headers=h)
    assert s.status_code == 200, s.text
    c.post(f"/api/v2/athletes/{aid}/report-consents", json={"audience": "coach", "given_by": "athlete"}, headers=h)
    link = c.post(f"/api/v2/v2-reports/{r['id']}/links", json={"expires_in_days": 7}, headers=h)
    assert link.status_code == 201, link.text
    token = link.json()["url"].rsplit("/r/", 1)[1]
    anon = TestClient(pg_app)
    out = anon.get(f"/api/v2/report-links/{token}")
    assert out.status_code == 200, out.text
    text_ = json.dumps(out.json())
    assert "keep pre-match routine" in text_ and "CLINICAL-ONLY-MARKER" not in text_ and "MEDICAL-ONLY-MARKER" not in text_
    c.post(f"/api/v2/report-links/{link.json()['id']}/revoke", headers=h)
    assert anon.get(f"/api/v2/report-links/{token}").status_code == 404
