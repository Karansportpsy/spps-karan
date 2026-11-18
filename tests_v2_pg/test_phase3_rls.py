"""Phase 3 on PostgreSQL: every new table is practitioner-private; signatures and amendments follow the lock."""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from tests_v2.test_phase3_record import MAPPING, as_csv, roster

from .conftest import as_role, denied, pg_login, q, seed_athlete

TABLES = ("athlete_profiles", "note_templates", "note_signatures", "note_amendments", "note_drafts",
          "case_formulations", "athlete_imports")


def test_new_tables_are_force_rls_and_private_to_their_practitioner(pgenv, people, superuser):
    rec = seed_athlete(superuser, people["b"], note="Fictional B note for phase 3")
    t, pb = people["b"]["tenant_id"], people["b"]["id"]
    q(superuser, "UPDATE spps_dev.notes SET state='final', revision=2 WHERE id=%(n)s", {"n": rec["note_id"]})
    q(superuser, """INSERT INTO spps_dev.athlete_profiles(athlete_id,tenant_id,practitioner_id,referral_notes)
                    VALUES (%(a)s,%(t)s,%(p)s,'enc:v1:k1:x')""", {"a": rec["athlete_id"], "t": t, "p": pb})
    q(superuser, """INSERT INTO spps_dev.note_templates(tenant_id,practitioner_id,name,body) VALUES (%(t)s,%(p)s,'B','x')""",
      {"t": t, "p": pb})
    q(superuser, """INSERT INTO spps_dev.note_signatures(note_id,tenant_id,practitioner_id,content_sha256)
                    VALUES (%(n)s,%(t)s,%(p)s,repeat('a',64))""", {"n": rec["note_id"], "t": t, "p": pb})
    q(superuser, """INSERT INTO spps_dev.note_amendments(note_id,tenant_id,practitioner_id,content,reason)
                    VALUES (%(n)s,%(t)s,%(p)s,'enc:v1:k1:x','r')""", {"n": rec["note_id"], "t": t, "p": pb})
    q(superuser, """INSERT INTO spps_dev.note_drafts(tenant_id,practitioner_id,case_id,client_draft_id,note_date,content)
                    VALUES (%(t)s,%(p)s,%(c)s,%(d)s,'2026-11-17','enc:v1:k1:x')""",
      {"t": t, "p": pb, "c": rec["case_id"], "d": "d-" + rec["code"]})
    q(superuser, """INSERT INTO spps_dev.case_formulations(tenant_id,practitioner_id,athlete_id,version,body)
                    VALUES (%(t)s,%(p)s,%(a)s,1,'enc:v1:k1:x')""", {"t": t, "p": pb, "a": rec["athlete_id"]})
    q(superuser, """INSERT INTO spps_dev.athlete_imports(tenant_id,practitioner_id,filename,payload)
                    VALUES (%(t)s,%(p)s,'b.csv','enc:v1:k1:x')""", {"t": t, "p": pb})
    forced = q(superuser, """SELECT relname FROM pg_class WHERE relnamespace='spps_dev'::regnamespace
                             AND relname = ANY(%(t)s) AND relrowsecurity AND relforcerowsecurity""", {"t": list(TABLES)})
    assert {r[0] for r in forced} == set(TABLES)
    for who in ("a", "owner"):
        with as_role(pgenv, "spps_dev_app", people[who]) as conn:
            for table in TABLES:
                assert q(conn, f"SELECT count(*) FROM {table} WHERE practitioner_id=%(p)s", {"p": pb})[0][0] == 0, (who, table)
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        for table in TABLES:
            assert q(b, f"SELECT count(*) FROM {table} WHERE practitioner_id=%(p)s", {"p": pb})[0][0] >= 1, table
        assert denied(b, "UPDATE note_amendments SET reason='changed'")
        assert denied(b, "DELETE FROM note_amendments")
        assert denied(b, "UPDATE note_signatures SET content_sha256=repeat('b',64)")


def test_signature_and_amendment_require_own_final_note(pgenv, people, superuser):
    rec_a = seed_athlete(superuser, people["a"])  # note is a draft
    rec_b = seed_athlete(superuser, people["b"])
    q(superuser, "UPDATE spps_dev.notes SET state='final', revision=2 WHERE id=%(n)s", {"n": rec_b["note_id"]})
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        sig = """INSERT INTO note_signatures(note_id,tenant_id,practitioner_id,content_sha256)
                 VALUES (%(n)s,%(t)s,%(p)s,repeat('c',64))"""
        args = {"t": people["a"]["tenant_id"], "p": people["a"]["id"]}
        assert denied(a, sig, {**args, "n": rec_a["note_id"]})  # own note but still a draft
        assert denied(a, sig, {**args, "n": rec_b["note_id"]})  # someone else's final note
        amend = """INSERT INTO note_amendments(note_id,tenant_id,practitioner_id,content,reason)
                   VALUES (%(n)s,%(t)s,%(p)s,'x','r')"""
        assert denied(a, amend, {**args, "n": rec_a["note_id"]})
        assert denied(a, amend, {**args, "n": rec_b["note_id"]})
        assert denied(a, """INSERT INTO note_drafts(tenant_id,practitioner_id,case_id,client_draft_id,note_date,content)
                            VALUES (%(t)s,%(p)s,%(c)s,'d-x-1','2026-11-17','x')""", {**args, "c": rec_b["case_id"]})
        assert denied(a, """INSERT INTO athlete_profiles(athlete_id,tenant_id,practitioner_id)
                            VALUES (%(x)s,%(t)s,%(p)s)""", {**args, "x": rec_b["athlete_id"]})


def test_minor_profile_without_guardian_is_refused_by_the_database(pgenv, people, superuser):
    rec = seed_athlete(superuser, people["a"])
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert denied(a, """INSERT INTO athlete_profiles(athlete_id,tenant_id,practitioner_id,age_group)
                            VALUES (%(x)s,%(t)s,%(p)s,'15-17')""",
                      {"x": rec["athlete_id"], "t": people["a"]["tenant_id"], "p": people["a"]["id"]})


def test_import_25_note_sign_and_timeline_over_rls(pg_app, people):
    client = TestClient(pg_app)
    h = pg_login(client, people["a"])
    up = client.post("/api/v2/imports", json={"filename": "squad.csv", "content_base64": as_csv(
        [[c.replace("IMP-", "PGI-") if isinstance(c, str) else c for c in row] for row in roster()])}, headers=h)
    assert up.status_code == 201, up.text
    done = client.post(f"/api/v2/imports/{up.json()['id']}/commit", json={"mapping": MAPPING}, headers=h)
    assert done.status_code == 200 and done.json()["imported"] == 25, done.text
    athlete = next(a for a in client.get("/api/athletes").json() if a["code"] == "PGI-005")
    assert client.get(f"/api/v2/athletes/{athlete['id']}/profile").json()["is_minor"]
    case = client.post("/api/cases", json={"athlete_id": athlete["id"], "title": "Fictional PG case"}, headers=h)
    assert case.status_code == 201, case.text
    saved = client.put("/api/v2/note-drafts/draft-pg000001", json={"case_id": case.json()["id"],
                       "note_date": "2026-11-17", "content": "Fictional PG note to lock", "expected_revision": 0},
                       headers=h)
    assert saved.status_code == 200, saved.text
    nid = client.post("/api/v2/note-drafts/draft-pg000001/commit", headers=h).json()["id"]
    signed = client.post(f"/api/v2/notes/{nid}/sign", json={"expected_revision": 1}, headers=h)
    assert signed.status_code == 200 and signed.json()["integrity"] is True, signed.text
    am = client.post(f"/api/v2/notes/{nid}/amendments", json={"content": "Fictional amendment", "reason": "Detail"},
                     headers=h)
    assert am.status_code == 201, am.text
    form = client.post(f"/api/v2/athletes/{athlete['id']}/formulation",
                       json={"presenting": "Fictional", "expected_version": 0}, headers=h)
    assert form.status_code == 201, form.text
    tl = client.get(f"/api/v2/athletes/{athlete['id']}/timeline")
    assert tl.status_code == 200, tl.text
    kinds = {x["kind"] for x in tl.json()}
    assert {"note", "amendment", "formulation"} <= kinds
    other = TestClient(pg_app)
    pg_login(other, people["b"])
    assert other.get(f"/api/v2/athletes/{athlete['id']}/timeline").status_code == 404
    assert other.get(f"/api/v2/notes/{nid}").status_code == 404
