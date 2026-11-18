"""Phase 4 on PostgreSQL: licensed instruments can never hold item text; scores are practitioner-private."""
from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests_v2.test_phase4_assessments import CUSTOM

from .conftest import as_role, denied, pg_login, q, seed_athlete


def _csai(conn):
    return q(conn, "SELECT id FROM instruments WHERE short_name='CSAI-2' AND practitioner_id IS NULL")[0][0]


def test_catalogue_is_seeded_score_entry_only_without_item_text(superuser):
    rows = q(superuser, """SELECT short_name, licence, licence_confirmed, assignable_to_athlete,
                                  (SELECT count(*) FROM spps_dev.instrument_items it WHERE it.instrument_id=i.id),
                                  (SELECT count(*) FROM spps_dev.instrument_subscales s WHERE s.instrument_id=i.id)
                             FROM spps_dev.instruments i WHERE practitioner_id IS NULL ORDER BY short_name""")
    assert [r[0] for r in rows] == ["ACSI-28", "CSAI-2", "POMS", "SMS-28"]
    assert all(r[1] == "licensed_score_entry_only" and r[2] is False and r[3] is False and r[4] == 0 and r[5] > 0
               for r in rows)


def test_database_refuses_item_text_for_a_licensed_instrument_even_as_superuser(superuser):
    iid = q(superuser, "SELECT id FROM spps_dev.instruments WHERE short_name='POMS'")[0][0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        q(superuser, """INSERT INTO spps_dev.instrument_items(instrument_id,position,text,subscale_key)
                        VALUES (%(i)s,1,'fictional text','tension')""", {"i": iid})


def test_practitioners_cannot_add_items_or_change_licences_of_platform_instruments(pgenv, people):
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        iid = _csai(a)
        assert denied(a, """INSERT INTO instrument_items(instrument_id,position,text,subscale_key)
                            VALUES (%(i)s,1,NULL,'self_confidence')""", {"i": iid})
        with a.cursor() as cur:
            cur.execute("UPDATE instruments SET licence='own' WHERE id=%(i)s", {"i": iid})
            assert cur.rowcount == 0
        assert denied(a, """INSERT INTO instruments(tenant_id,practitioner_id,name,licence)
                            VALUES (%(t)s,%(p)s,'x','licensed_score_entry_only')""",
                      {"t": people["a"]["tenant_id"], "p": people["a"]["id"]})
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        with owner.cursor() as cur:
            cur.execute("UPDATE instruments SET licence_confirmed=true WHERE id=%(i)s", {"i": _csai(owner)})
            assert cur.rowcount == 1


def test_administrations_and_scores_are_private(pgenv, people, superuser):
    rec = seed_athlete(superuser, people["b"])
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        iid = _csai(b)
        adm = q(b, """INSERT INTO instrument_administrations(tenant_id,practitioner_id,athlete_id,instrument_id,
                      administered_on,mode) VALUES (%(t)s,%(p)s,%(a)s,%(i)s,'2026-11-01','subscale_entry') RETURNING id""",
                {"t": people["b"]["tenant_id"], "p": people["b"]["id"], "a": rec["athlete_id"], "i": iid})[0][0]
        q(b, "INSERT INTO instrument_scores(administration_id,subscale_key,score) VALUES (%(a)s,'self_confidence',22)",
          {"a": adm})
        b.commit()
    for who in ("a", "owner"):
        with as_role(pgenv, "spps_dev_app", people[who]) as conn:
            assert q(conn, "SELECT count(*) FROM instrument_administrations WHERE id=%(a)s", {"a": adm})[0][0] == 0
            assert q(conn, "SELECT count(*) FROM instrument_scores WHERE administration_id=%(a)s", {"a": adm})[0][0] == 0
            assert denied(conn, "INSERT INTO instrument_scores(administration_id,subscale_key,score) "
                                "VALUES (%(a)s,'cognitive_anxiety',10)", {"a": adm})
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:  # cannot score someone else's athlete
        assert denied(a, """INSERT INTO instrument_administrations(tenant_id,practitioner_id,athlete_id,instrument_id,
                            administered_on,mode) VALUES (%(t)s,%(p)s,%(x)s,%(i)s,'2026-11-01','subscale_entry')""",
                      {"t": people["a"]["tenant_id"], "p": people["a"]["id"], "x": rec["athlete_id"], "i": _csai(a)})


def test_builder_and_trends_over_rls(pg_app, people):
    c = TestClient(pg_app)
    h = pg_login(c, people["a"])
    inst = c.post("/api/v2/instruments", json=CUSTOM, headers=h)
    assert inst.status_code == 201, inst.text
    aid = c.post("/api/athletes", json={"code": "PG4-1", "display_name": "Fictional Trend Athlete", "sport": "Golf",
                                        "service_consent_recorded": True}, headers=h).json()["id"]
    items = [i["id"] for i in inst.json()["items"]]
    r = c.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": inst.json()["id"], "administered_on": "2026-11-01",
        "responses": {str(items[0]): 5, str(items[1]): 2, str(items[2]): 4, str(items[3]): 5}}, headers=h)
    assert r.status_code == 201 and r.json()["scores"]["focus"]["score"] == 9.0, r.text
    lic = next(i for i in c.get("/api/v2/instruments").json() if i["short_name"] == "ACSI-28")
    assert c.post(f"/api/v2/athletes/{aid}/instrument-administrations", json={
        "instrument_id": lic["id"], "administered_on": "2026-11-02", "subscale_scores": {"total": 55}},
        headers=h).status_code == 201
    t = c.get(f"/api/v2/athletes/{aid}/instrument-trends/{lic['id']}").json()
    assert t["interpolated"] is False and next(s for s in t["series"] if s["key"] == "total")["points"][0]["score"] == 55
    other = TestClient(pg_app)
    pg_login(other, people["b"])
    assert other.get(f"/api/v2/instruments/{inst.json()['id']}").status_code == 404
