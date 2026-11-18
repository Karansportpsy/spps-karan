"""Phase 2 on PostgreSQL: practitioner isolation, the owner cannot read clinical text, append-only access log."""
from __future__ import annotations

import io
import json
import zipfile

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import as_role, denied, pg_login, q, seed_athlete


@pytest.fixture
def records(superuser, people):
    return {"a": seed_athlete(superuser, people["a"], name="Fictional A Athlete", note="A-only fictional note"),
            "b": seed_athlete(superuser, people["b"], name="Fictional B Athlete", note="B-only fictional note")}


def test_practitioner_a_cannot_read_practitioner_b_athletes_or_notes(pgenv, people, records):
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert q(a, "SELECT count(*) FROM athletes WHERE id=%(i)s", {"i": records["b"]["athlete_id"]})[0][0] == 0
        assert q(a, "SELECT count(*) FROM cases WHERE id=%(i)s", {"i": records["b"]["case_id"]})[0][0] == 0
        assert q(a, "SELECT count(*) FROM notes WHERE id=%(i)s", {"i": records["b"]["note_id"]})[0][0] == 0
        assert q(a, "SELECT count(*) FROM athletes WHERE id=%(i)s", {"i": records["a"]["athlete_id"]})[0][0] == 1


def test_owner_role_cannot_read_clinical_text(pgenv, people, records):
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        for table, key in (("athletes", "athlete_id"), ("cases", "case_id"), ("notes", "note_id")):
            for who in ("a", "b"):
                assert q(owner, f"SELECT count(*) FROM {table} WHERE id=%(i)s", {"i": records[who][key]})[0][0] == 0
    with as_role(pgenv, "spps_dev_auth", people["owner"]) as auth:
        assert denied(auth, "SELECT content FROM notes")


def test_access_log_and_exports_are_private_and_append_only(pgenv, people, superuser, records):
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        q(b, """INSERT INTO record_access_log(tenant_id,practitioner_id,actor_id,athlete_id,action,resource)
                VALUES (%(t)s,%(p)s,%(p)s,%(a)s,'read','/api/athletes/{athlete_id}')""",
          {"t": people["b"]["tenant_id"], "p": people["b"]["id"], "a": records["b"]["athlete_id"]})
        q(b, """INSERT INTO data_exports(tenant_id,practitioner_id,sha256,size_bytes,files)
                VALUES (%(t)s,%(p)s,repeat('a',64),10,1)""", {"t": people["b"]["tenant_id"], "p": people["b"]["id"]})
        b.commit()
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        # a forged row claiming to be practitioner A is refused
        assert denied(b, """INSERT INTO record_access_log(tenant_id,practitioner_id,actor_id,action,resource)
                            VALUES (%(t)s,%(p)s,%(p)s,'read','x')""",
                      {"t": people["a"]["tenant_id"], "p": people["a"]["id"]})
        assert denied(b, "UPDATE record_access_log SET action='write'")
        assert denied(b, "DELETE FROM record_access_log")
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert q(a, "SELECT count(*) FROM record_access_log WHERE practitioner_id=%(p)s",
                 {"p": people["b"]["id"]})[0][0] == 0
        assert q(a, "SELECT count(*) FROM data_exports WHERE practitioner_id=%(p)s",
                 {"p": people["b"]["id"]})[0][0] == 0
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert q(owner, "SELECT count(*) FROM record_access_log")[0][0] == 0  # rows: never
        counts = q(owner, "SELECT * FROM spps_dev.v2_owner_access_counts()")
        assert any(r[0] == people["b"]["id"] and r[1] >= 1 for r in counts)  # numbers: yes
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert q(a, "SELECT * FROM spps_dev.v2_owner_access_counts()") == []  # not the owner: nothing


def test_closure_state_cannot_be_forged_and_archive_is_owner_only(pgenv, people, superuser):
    with as_role(pgenv, "spps_dev_app", people["a"]) as a:
        assert denied(a, """INSERT INTO practitioner_closures(tenant_id,practitioner_id,state,notice_ends_at)
                            VALUES (%(t)s,%(p)s,'closed',now())""", {"t": people["a"]["tenant_id"], "p": people["a"]["id"]})
        cid = q(a, """INSERT INTO practitioner_closures(tenant_id,practitioner_id,state,notice_ends_at)
                      VALUES (%(t)s,%(p)s,'closing',now()+interval '30 days') RETURNING id""",
                {"t": people["a"]["tenant_id"], "p": people["a"]["id"]})[0][0]
        assert denied(a, "UPDATE practitioner_closures SET state='closed' WHERE id=%(c)s", {"c": cid})
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with a.transaction():
                q(a, "SELECT spps_dev.v2_complete_own_closure(%(c)s,'{}'::jsonb,14)", {"c": cid})  # notice not over
        assert q(a, "SELECT count(*) FROM owner_practitioner_archive")[0][0] == 0
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with a.transaction():
                q(a, "SELECT spps_dev.v2_owner_purge_closure(%(c)s)", {"c": cid})  # not the owner
    with as_role(pgenv, "spps_dev_app", people["b"]) as b:
        assert q(b, "SELECT count(*) FROM practitioner_closures WHERE practitioner_id=%(p)s",
                 {"p": people["a"]["id"]})[0][0] == 0


def test_closure_and_erasure_functions_are_not_executable_by_other_roles(pgenv, people):
    for role in ("spps_dev_auth", "spps_identity_auth", "spps_identity_portal"):
        with as_role(pgenv, role, people["a"]) as conn:
            assert denied(conn, "SELECT spps_dev.v2_complete_own_closure(1,'{}'::jsonb,14)")
            assert denied(conn, "SELECT spps_dev.v2_owner_purge_closure(1)")
    with as_role(pgenv, "spps_dev_app", people["owner"]) as owner:
        assert denied(owner, "SELECT spps_dev.v2_erase_athlete_records(ARRAY[1])")  # no app role, not even the owner


def test_export_over_rls_contains_only_own_data_and_notes_are_ciphertext_at_rest(pg_app, people, superuser):
    client = TestClient(pg_app)
    h = pg_login(client, people["a"])
    aid = client.post("/api/athletes", json={"code": "PGX-1", "display_name": "Fictional Export Athlete",
                                             "sport": "Tennis", "service_consent_recorded": True}, headers=h)
    assert aid.status_code == 201, aid.text
    case = client.post("/api/cases", json={"athlete_id": aid.json()["id"], "title": "Fictional export case"}, headers=h)
    note = client.post("/api/notes", json={"case_id": case.json()["id"], "note_date": "2026-11-17",
                                           "content": "Fictional PG export note"}, headers=h)
    assert note.status_code == 201, note.text
    stored = q(superuser, "SELECT content FROM spps_dev.notes WHERE id=%(i)s", {"i": note.json()["id"]})[0][0]
    assert stored.startswith("enc:v1:k1:")
    log = client.get(f"/api/v2/athletes/{aid.json()['id']}/access-log")
    assert log.status_code == 200 and any(r["action"] == "write" for r in log.json()), log.text
    res = client.post("/api/v2/account/export", json={"password": people["a"]["password"]}, headers=h)
    assert res.status_code == 200, res.text
    z = zipfile.ZipFile(io.BytesIO(res.content))
    blob = z.read("export.json").decode()
    assert "Fictional PG export note" in blob
    assert "B-only fictional note" not in blob and "Fictional B Athlete" not in blob
    data = json.loads(blob)
    assert all(a["owner_practitioner_id"] == people["a"]["id"] for a in data["data"]["athletes"])
    owner = TestClient(pg_app)
    pg_login(owner, people["owner"])
    counts = owner.get("/api/v2/admin/access-counts")
    assert counts.status_code == 200 and "Fictional PG export note" not in counts.text
