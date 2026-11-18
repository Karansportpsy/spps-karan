"""Phase 3 acceptance and negative tests: import 25 athletes, write and lock a note, see it on the timeline."""
from __future__ import annotations

import base64
import csv
import io
import json

import pytest
from sqlalchemy import text

from .conftest import PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete, make_case

SPORTS = ["Badminton", "Hockey", "Swimming", "Athletics", "Shooting"]


def roster(n=25, *, bad=False):
    rows = [["Athlete ID", "Name", "Sport", "Level", "Age group", "Guardian", "Guardian email", "Consent",
             "Focus score", "Score date"]]
    for i in range(1, n + 1):
        minor = i % 5 == 0
        rows.append([f"IMP-{i:03d}", f"Fictional Athlete {i:02d}", SPORTS[i % 5], "state" if i % 2 else "club",
                     "15-17" if minor else "18-24", "Fictional Guardian" if minor else "",
                     f"guardian{i}@example.org" if minor else "", "yes", str(5 + i % 5) if i % 3 else "",
                     "2026-10-01"])
    if bad:
        rows[3][0] = rows[2][0]  # duplicate code (rows 3 and 4 of the sheet)
        rows[5][5] = rows[5][6] = ""  # under-18 without guardian
        rows[5][4] = "15-17"
        rows[7][3] = "galactic"  # unknown level
        rows[9][8] = "eleven"  # not a number
    return rows


def as_csv(rows) -> str:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return base64.b64encode(out.getvalue().encode()).decode()


def as_xlsx(rows) -> str:
    from openpyxl import Workbook
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return base64.b64encode(out.getvalue()).decode()


MAPPING = {"code": "Athlete ID", "display_name": "Name", "sport": "Sport", "level": "Level", "age_group": "Age group",
           "guardian_name": "Guardian", "guardian_email": "Guardian email", "consent_recorded": "Consent"}


def _factor(c, h):
    res = c.post("/api/factors", json={"label": "Focus (fictional)", "kind": "assessment", "low": 0, "high": 10}, headers=h)
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_practitioner_imports_25_fictional_athletes_with_scores(app, fmt):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    fid = _factor(b, hb)
    content = as_csv(roster()) if fmt == "csv" else as_xlsx(roster())
    up = b.post("/api/v2/imports", json={"filename": f"squad.{fmt}", "content_base64": content}, headers=hb)
    assert up.status_code == 201, up.text
    info = up.json()
    assert info["rows_total"] == 25 and info["suggested_mapping"]["code"] == "Athlete ID"
    scores = [{"column": "Focus score", "factor_id": fid, "date_column": "Score date"}]
    pv = b.post(f"/api/v2/imports/{info['id']}/preview", json={"mapping": MAPPING, "scores": scores}, headers=hb)
    assert pv.status_code == 200 and pv.json()["can_commit"] and pv.json()["valid_rows"] == 25, pv.text
    before = len(b.get("/api/athletes").json())
    done = b.post(f"/api/v2/imports/{info['id']}/commit", json={"mapping": MAPPING, "scores": scores}, headers=hb)
    assert done.status_code == 200 and done.json()["imported"] == 25, done.text
    athletes = b.get("/api/athletes").json()
    assert len(athletes) == before + 25
    minor = next(a for a in athletes if a["code"] == "IMP-005")
    prof = b.get(f"/api/v2/athletes/{minor['id']}/profile").json()
    assert prof["is_minor"] and prof["guardian_email"] == "guardian5@example.org" and prof["consent_status"] == "recorded"
    obs = b.get(f"/api/athletes/{minor['id']}/observations").json()
    assert [o["value"] for o in obs] == [5.0] and obs[0]["observed_on"] == "2026-10-01"
    skipped = next(a for a in athletes if a["code"] == "IMP-003")  # empty score cell stays missing
    assert b.get(f"/api/athletes/{skipped['id']}/observations").json() == []
    again = b.post(f"/api/v2/imports/{info['id']}/commit", json={"mapping": MAPPING, "scores": scores}, headers=hb)
    assert again.status_code == 409


def test_bad_rows_are_explained_and_nothing_is_imported(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    fid = _factor(b, hb)
    up = b.post("/api/v2/imports", json={"filename": "bad.csv", "content_base64": as_csv(roster(bad=True))},
                headers=hb).json()
    scores = [{"column": "Focus score", "factor_id": fid, "date_column": "Score date"}]
    pv = b.post(f"/api/v2/imports/{up['id']}/preview", json={"mapping": MAPPING, "scores": scores}, headers=hb).json()
    assert not pv["can_commit"]
    found = {(e["row"], e["field"]) for e in pv["errors"]}
    assert (4, "code") in found and (6, "guardian_name") in found and (8, "level") in found
    assert (10, "Focus score") in found
    before = len(b.get("/api/athletes").json())
    res = b.post(f"/api/v2/imports/{up['id']}/commit", json={"mapping": MAPPING, "scores": scores}, headers=hb)
    assert res.status_code == 422 and "Nothing was imported" in res.text
    assert len(b.get("/api/athletes").json()) == before


def test_import_rejects_missing_columns_unknown_files_and_existing_codes(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    make_athlete(b, hb, code="IMP-001")
    up = b.post("/api/v2/imports", json={"filename": "s.csv", "content_base64": as_csv(roster(3))}, headers=hb).json()
    res = b.post(f"/api/v2/imports/{up['id']}/preview", json={"mapping": {"code": "Athlete ID"}}, headers=hb)
    assert res.status_code == 422 and "display_name" in res.text
    pv = b.post(f"/api/v2/imports/{up['id']}/preview", json={"mapping": MAPPING}, headers=hb).json()
    assert any(e["row"] == 2 and "already exists" in e["message"] for e in pv["errors"])
    bad = b.post("/api/v2/imports", json={"filename": "s.pdf", "content_base64": as_csv(roster(1))}, headers=hb)
    assert bad.status_code == 422


def test_note_autosave_sign_lock_amend_and_timeline(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    cid = make_case(b, hb, aid)
    tpl = b.post("/api/v2/note-templates", json={"name": "Session (SOAP)", "body": "S:\nO:\nA:\nP:"}, headers=hb)
    assert tpl.status_code == 201 and b.get("/api/v2/note-templates").json()[0]["name"] == "Session (SOAP)"
    did = "draft-1234abcd"
    r1 = b.put(f"/api/v2/note-drafts/{did}", json={"case_id": cid, "note_date": "2026-11-16", "content": "S: nerves",
                                                   "expected_revision": 0}, headers=hb)
    assert r1.status_code == 200 and r1.json()["revision"] == 1
    r2 = b.put(f"/api/v2/note-drafts/{did}", json={"case_id": cid, "note_date": "2026-11-16",
                                                   "content": "S: nerves before final. P: breathing routine.",
                                                   "expected_revision": 1}, headers=hb)
    assert r2.json()["revision"] == 2
    stale = b.put(f"/api/v2/note-drafts/{did}", json={"case_id": cid, "note_date": "2026-11-16", "content": "old",
                                                      "expected_revision": 1}, headers=hb)
    assert stale.status_code == 409  # another window saved first: nothing is lost silently
    with app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT content FROM note_drafts")).scalar().startswith("enc:v1:")
    other = fresh_client(app)  # the draft is on the server: another browser sees it
    login(other, PRACTITIONER_B)
    assert other.get("/api/v2/note-drafts").json()[0]["content"].endswith("breathing routine.")
    note = b.post(f"/api/v2/note-drafts/{did}/commit", headers=hb)
    assert note.status_code == 201
    nid = note.json()["id"]
    assert b.get("/api/v2/note-drafts").json() == []
    assert b.post(f"/api/v2/notes/{nid}/amendments", json={"content": "x" * 5, "reason": "Too early"},
                  headers=hb).status_code == 409  # drafts are edited, not amended
    signed = b.post(f"/api/v2/notes/{nid}/sign", json={"expected_revision": 1}, headers=hb)
    assert signed.status_code == 200 and signed.json()["locked"] and signed.json()["integrity"] is True
    assert b.post(f"/api/notes/{nid}/finalize", json={"expected_revision": 2}, headers=hb).status_code == 409
    am = b.post(f"/api/v2/notes/{nid}/amendments", json={"content": "Correction: the match was the semi-final.",
                                                         "reason": "Factual correction"}, headers=hb)
    assert am.status_code == 201 and len(am.json()["amendments"]) == 1
    assert am.json()["content"].startswith("S: nerves before final")  # the signed text is unchanged
    tl = b.get(f"/api/v2/athletes/{aid}/timeline").json()
    kinds = [x["kind"] for x in tl]
    assert "note" in kinds and "amendment" in kinds
    note_item = next(x for x in tl if x["kind"] == "note")
    assert note_item["state"] == "final" and note_item["date"] == "2026-11-16"


def test_offline_draft_key_is_per_practitioner_and_stable(app):
    b = fresh_client(app)
    login(b, PRACTITIONER_B)
    c = fresh_client(app)
    login(c, PRACTITIONER_C)
    kb1, kb2, kc = (b.get("/api/v2/offline-key").json(), b.get("/api/v2/offline-key").json(),
                    c.get("/api/v2/offline-key").json())
    assert kb1 == kb2 and kb1["key"] != kc["key"] and len(base64.b64decode(kb1["key"])) == 32
    assert fresh_client(app).get("/api/v2/offline-key").status_code == 401


def test_profile_rules_for_minors_and_consent(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    base = {"level": "national", "age_group": "15-17", "consent_status": "recorded"}
    assert b.put(f"/api/v2/athletes/{aid}/profile", json=base, headers=hb).status_code == 422
    ok = b.put(f"/api/v2/athletes/{aid}/profile", json={**base, "guardian_name": "Fictional Parent",
                                                         "guardian_phone": "+910000000000",
                                                         "referral_notes": "Fictional: referred by coach"}, headers=hb)
    assert ok.status_code == 200 and ok.json()["is_minor"]
    with app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT referral_notes FROM athlete_profiles")).scalar().startswith("enc:v1:")
    w = b.put(f"/api/v2/athletes/{aid}/profile", json={**base, "guardian_name": "Fictional Parent",
                                                        "guardian_phone": "+910000000000",
                                                        "consent_status": "withdrawn"}, headers=hb)
    assert w.json()["consent_status"] == "withdrawn"
    assert b.post("/api/cases", json={"athlete_id": aid, "title": "New case after withdrawal"},
                  headers=hb).status_code == 403


def test_formulation_is_practitioner_written_versioned_and_encrypted(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    empty = b.get(f"/api/v2/athletes/{aid}/formulation").json()
    assert empty["sections"] == ["presenting", "predisposing", "precipitating", "perpetuating", "protective"]
    assert empty["current"] is None and empty["authored_by"] == "practitioner"
    v1 = b.post(f"/api/v2/athletes/{aid}/formulation", json={"presenting": "Fictional: pre-competition worry",
                                                             "expected_version": 0}, headers=hb)
    assert v1.status_code == 201 and v1.json()["version"] == 1
    assert b.post(f"/api/v2/athletes/{aid}/formulation", json={"presenting": "x", "expected_version": 0},
                  headers=hb).status_code == 409
    v2 = b.post(f"/api/v2/athletes/{aid}/formulation", json={"presenting": "Fictional: worry",
                                                             "protective": "Fictional: supportive coach",
                                                             "expected_version": 1}, headers=hb)
    got = b.get(f"/api/v2/athletes/{aid}/formulation").json()
    assert got["current"]["version"] == 2 and got["current"]["protective"] == "Fictional: supportive coach"
    assert len(got["history"]) == 1 and v2.status_code == 201
    with app.state.engine.connect() as conn:
        assert all(v.startswith("enc:v1:") for (v,) in conn.execute(text("SELECT body FROM case_formulations")))


def test_every_new_route_refuses_another_practitioners_records(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    cid = make_case(b, hb, aid)
    tid = b.post("/api/v2/note-templates", json={"name": "B template", "body": "x" * 5}, headers=hb).json()["id"]
    b.put("/api/v2/note-drafts/draft-b0000001", json={"case_id": cid, "note_date": "2026-11-17",
                                                      "content": "B draft text", "expected_revision": 0}, headers=hb)
    nid = b.post("/api/v2/note-drafts/draft-b0000001/commit", headers=hb).json()["id"]
    imp = b.post("/api/v2/imports", json={"filename": "b.csv", "content_base64": as_csv(roster(2))}, headers=hb).json()
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    checks = [
        c.get(f"/api/v2/athletes/{aid}/profile"), c.put(f"/api/v2/athletes/{aid}/profile", json={}, headers=hc),
        c.get(f"/api/v2/athletes/{aid}/formulation"),
        c.post(f"/api/v2/athletes/{aid}/formulation", json={"presenting": "x", "expected_version": 0}, headers=hc),
        c.get(f"/api/v2/athletes/{aid}/timeline"), c.get(f"/api/v2/notes/{nid}"),
        c.post(f"/api/v2/notes/{nid}/sign", json={"expected_revision": 1}, headers=hc),
        c.post(f"/api/v2/notes/{nid}/amendments", json={"content": "xxxxx", "reason": "abc"}, headers=hc),
        c.post(f"/api/v2/note-templates/{tid}/archive", headers=hc),
        c.post(f"/api/v2/imports/{imp['id']}/preview", json={"mapping": MAPPING}, headers=hc),
        c.post(f"/api/v2/imports/{imp['id']}/commit", json={"mapping": MAPPING}, headers=hc),
        c.put("/api/v2/note-drafts/draft-c0000001", json={"case_id": cid, "note_date": "2026-11-17", "content": "x",
                                                          "expected_revision": 0}, headers=hc),
    ]
    assert [r.status_code for r in checks] == [404] * len(checks), [r.text for r in checks]
    assert c.get("/api/v2/note-templates").json() == [] and c.get("/api/v2/note-drafts").json() == []
    blob = json.dumps([r.json() for r in checks])
    assert "B draft text" not in blob and "B template" not in blob


def test_export_includes_phase3_records(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb)
    b.post(f"/api/v2/athletes/{aid}/formulation", json={"presenting": "Fictional exported text",
                                                        "expected_version": 0}, headers=hb)
    import zipfile
    res = b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    data = json.loads(zipfile.ZipFile(io.BytesIO(res.content)).read("export.json"))
    assert "Fictional exported text" in data["data"]["case_formulations"][0]["body"]
