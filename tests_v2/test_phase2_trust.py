"""Phase 2 acceptance and negative tests: access log, export, closure, AI switch, encryption, rate limits."""
from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import text

from .conftest import (OWNER_LOGIN, PRACTITIONER_B, PRACTITIONER_C, fresh_client, login, make_athlete, make_case,
                       make_note)

SECRET_NOTE = "Fictional note: athlete reported pre-match nerves before the semi-final."


def _setup_b(app):
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    aid = make_athlete(b, hb, code="B-ONE", name="Fictional B Athlete")
    cid = make_case(b, hb, aid)
    nid = make_note(b, hb, cid, SECRET_NOTE)
    return b, hb, aid, cid, nid


def test_notes_are_encrypted_at_rest_and_readable_through_the_api(app):
    b, hb, aid, cid, nid = _setup_b(app)
    with app.state.engine.connect() as conn:
        stored = conn.execute(text("SELECT content FROM notes WHERE id=:i"), {"i": nid}).scalar()
    assert stored.startswith("enc:v1:k1:") and SECRET_NOTE not in stored
    assert b.get(f"/api/cases/{cid}").json()["notes"][0]["content"] == SECRET_NOTE


def test_ciphertext_is_bound_to_its_column(v2env):
    from server.v2 import fieldcrypt
    import pytest
    enc = fieldcrypt.encrypt("text", "notes.content")
    with pytest.raises(Exception):
        fieldcrypt.decrypt(enc, "case_formulations.body")


def test_key_rotation_re_encrypts_every_row_and_old_key_can_then_be_removed(app, v2env):
    _b, _hb, _aid, cid, nid = _setup_b(app)
    from server.v2 import fieldcrypt
    k2 = fieldcrypt.new_key_entry("k2")
    v2env.setenv("SPPS_FIELD_KEYS", "k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=," + k2)
    v2env.setenv("SPPS_FIELD_KEY_CURRENT", "k2")
    from tools.v2_rotate_field_key import rotate
    report = rotate(str(app.state.engine.url))
    assert report["notes"]["re_encrypted"] >= 1 and report["notes"]["not_on_current_key"] == 0
    v2env.setenv("SPPS_FIELD_KEYS", k2)  # old key removed
    b = fresh_client(app)
    login(b, PRACTITIONER_B)
    assert b.get(f"/api/cases/{cid}").json()["notes"][0]["content"] == SECRET_NOTE


def test_every_athlete_read_and_write_is_logged_and_visible_to_the_practitioner(app):
    b, hb, aid, cid, nid = _setup_b(app)
    b.get(f"/api/athletes/{aid}")
    log = b.get(f"/api/v2/athletes/{aid}/access-log").json()
    actions = {(r["action"], r["resource"]) for r in log}
    assert ("read", "/api/athletes/{athlete_id}") in actions
    assert ("write", "/api/notes") in actions and ("write", "/api/cases") in actions
    assert all(SECRET_NOTE not in json.dumps(r) for r in log)
    assert all(r["request_id"] for r in log)


def test_practitioner_cannot_read_another_practitioners_access_log_or_athletes(app):
    _b, _hb, aid, cid, nid = _setup_b(app)
    c = fresh_client(app)
    login(c, PRACTITIONER_C)
    for path in (f"/api/v2/athletes/{aid}/access-log", f"/api/athletes/{aid}", f"/api/cases/{cid}"):
        assert c.get(path).status_code == 404, path
    assert c.get("/api/v2/admin/access-counts").status_code == 403


def test_owner_sees_counts_only_never_clinical_content(app):
    _b, _hb, aid, cid, nid = _setup_b(app)
    owner = fresh_client(app)
    login(owner, OWNER_LOGIN)
    counts = owner.get("/api/v2/admin/access-counts").json()
    assert counts and all(set(r) == {"practitioner_id", "name", "reads", "writes", "exports"} for r in counts)
    assert SECRET_NOTE not in json.dumps(counts) and "Fictional B Athlete" not in json.dumps(counts)
    # The owner's own workspace has no route to another practitioner's athlete or note.
    assert owner.get(f"/api/athletes/{aid}").status_code == 404
    assert owner.get(f"/api/cases/{cid}").status_code == 404
    assert owner.get(f"/api/v2/athletes/{aid}/access-log").status_code == 404


def test_export_contains_only_the_requesting_practitioners_data(app):
    b, hb, aid, cid, nid = _setup_b(app)
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    c_aid = make_athlete(c, hc, code="C-ONE", name="Fictional C Athlete")
    make_note(c, hc, make_case(c, hc, c_aid), "Fictional C-only note that must never leave C's export.")
    assert b.post("/api/v2/account/export", json={"password": "wrong-password"}, headers=hb).status_code in (401, 403)
    res = b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert {"export.json", "README.txt", "csv/athletes.csv", "csv/notes.csv"} <= names
    blob = b"".join(z.read(n) for n in names).decode("utf-8", "ignore")
    assert SECRET_NOTE in blob and "Fictional B Athlete" in blob
    assert "Fictional C Athlete" not in blob and "C-only note" not in blob
    assert "pbkdf2" not in blob and "enc:v1:" not in blob
    data = json.loads(z.read("export.json"))
    assert {a["id"] for a in data["data"]["athletes"]} == {aid}
    recorded = b.get("/api/v2/account/exports").json()
    assert len(recorded) == 1 and recorded[0]["sha256"] == res.headers["X-Export-SHA256"]
    assert c.get("/api/v2/account/exports").json() == []


def test_ai_is_off_by_default_everywhere(app, client):
    assert client.get("/api/runtime").json()["ai"] is False
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    assert b.get("/api/v2/settings").json() == {"ai_available": False}
    assert b.post("/api/v2/settings/ai", json={"opt_in": True}, headers=hb).status_code == 404


def test_ai_requires_global_switch_and_personal_opt_in(app, v2env):
    from server.v2.trust import require_ai
    from fastapi import HTTPException
    import pytest
    b = fresh_client(app)
    hb = login(b, PRACTITIONER_B)
    v2env.setenv("SPPS_AI_FEATURES_ENABLED", "true")
    assert b.get("/api/v2/settings").json()["ai_opt_in"] is False
    from server.models import Practitioner
    from sqlalchemy import select
    with app.state.DB() as db:
        person = db.scalar(select(Practitioner).where(Practitioner.email == PRACTITIONER_B["email"]))
        with pytest.raises(HTTPException):
            require_ai(db, person)
    assert b.post("/api/v2/settings/ai", json={"opt_in": True}, headers=hb).json()["ai_opt_in"] is True
    with app.state.DB() as db:
        require_ai(db, person)  # now allowed
        v2env.setenv("SPPS_AI_FEATURES_ENABLED", "false")
        with pytest.raises(HTTPException):
            require_ai(db, person)  # the global switch always wins


def test_no_ai_wording_in_any_app_screen():
    """App screens only. The legal documents (privacy, terms, your-data) keep their statements that data is never
    used to train AI models; that is a promise about data use, not an AI feature (DECISIONS D-207)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "web"
    legal = {"privacy.html", "terms.html", "policies.html", "your-data.html"}
    for f in [p for p in list(root.glob("*.js")) + list(root.glob("*.html")) if p.name not in legal]:
        assert not re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", f.read_text(encoding="utf-8")), f.name


def test_your_data_page_comes_from_one_file_and_states_the_promises(client):
    page = client.get("/your-data")
    assert page.status_code == 200
    for phrase in ("never sell", "advertising", "train artificial-intelligence", "row-level security",
                   "cannot read your athletes"):
        assert phrase in page.text


def test_public_and_auth_endpoints_are_rate_limited(app):
    c = fresh_client(app)
    codes = [c.post("/api/auth/login", json={"email": "nobody@example.org", "password": "x",
                                              "terms_accepted": True}).status_code for _ in range(13)]
    assert 429 in codes
    from server.v2 import guards
    guards.reset()
    from server.v2.guards import LIMITS
    limit, _ = LIMITS["public-read"]
    statuses = {c.get("/api/public/services").status_code for _ in range(limit + 1)}
    assert 429 in statuses


def _closure_ready(app, v2env):
    v2env.setenv("SPPS_PRACTITIONER_CLOSURE_ENABLED", "true")
    v2env.setenv("SPPS_CLOSURE_NOTICE_DAYS", "0")
    return _setup_b(app)


def test_closure_is_off_by_default(app):
    b, hb, *_ = _setup_b(app)
    assert b.get("/api/v2/account/closure").status_code == 404


def test_orderly_closure_owner_archive_has_professional_data_only(app, v2env):
    b, hb, aid, cid, nid = _closure_ready(app, v2env)
    assert b.post("/api/v2/account/closure/start", json={"password": "wrong"}, headers=hb).status_code in (401, 403)
    started = b.post("/api/v2/account/closure/start", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    assert started.status_code == 200 and started.json()["state"] == "closing"
    assert "Choose what happens to every athlete record" in started.json()["blockers"]
    b.post("/api/v2/account/closure/dispositions", json={"athlete_id": aid, "disposition": "export_and_erase"},
           headers=hb)
    blocked = b.post("/api/v2/account/closure/complete", headers=hb)
    assert blocked.status_code == 409 and "Export your data" in blocked.text
    b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    done = b.post("/api/v2/account/closure/complete", headers=hb)
    assert done.status_code == 200 and done.json()["state"] == "closed"
    assert b.get("/api/athletes").status_code in (401, 403)  # sessions revoked, account inactive

    owner = fresh_client(app)
    ho = login(owner, OWNER_LOGIN)
    closures = owner.get("/api/v2/admin/closures").json()
    arch = closures[0]["archive"]
    blob = json.dumps(arch)
    assert arch["professional"]["qualifications"] and arch["closure"]["dispositions"] == {"export_and_erase": 1}
    assert SECRET_NOTE not in blob and "Fictional B Athlete" not in blob and "B-ONE" not in blob
    # Purge waits for the cooling-off period; restore is possible inside it.
    assert owner.post(f"/api/v2/admin/closures/{closures[0]['id']}/purge", headers=ho).status_code == 409
    assert owner.post(f"/api/v2/admin/closures/{closures[0]['id']}/restore", headers=ho).json()["state"] == "restored"


def test_purge_after_cooling_off_erases_only_the_chosen_records(app, v2env):
    v2env.setenv("SPPS_CLOSURE_COOLING_OFF_DAYS", "0")
    b, hb, aid, cid, nid = _closure_ready(app, v2env)
    keep = make_athlete(b, hb, code="B-KEEP", name="Fictional Kept Athlete")
    b.post("/api/v2/account/closure/start", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    b.post("/api/v2/account/closure/dispositions", json={"athlete_id": aid, "disposition": "export_and_erase"}, headers=hb)
    b.post("/api/v2/account/closure/dispositions", json={"athlete_id": keep, "disposition": "keep_deactivated"}, headers=hb)
    b.post("/api/v2/account/export", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    assert b.post("/api/v2/account/closure/complete", headers=hb).status_code == 200
    owner = fresh_client(app)
    ho = login(owner, OWNER_LOGIN)
    cid_ = owner.get("/api/v2/admin/closures").json()[0]["id"]
    res = owner.post(f"/api/v2/admin/closures/{cid_}/purge", headers=ho)
    assert res.status_code == 200 and res.json()["erased_records"] == 1
    with app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM athletes WHERE id=:i"), {"i": aid}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM notes WHERE id=:i"), {"i": nid}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM athletes WHERE id=:i"), {"i": keep}).scalar() == 1


def test_another_practitioner_cannot_touch_someone_elses_closure(app, v2env):
    b, hb, aid, *_ = _closure_ready(app, v2env)
    b.post("/api/v2/account/closure/start", json={"password": PRACTITIONER_B["password"]}, headers=hb)
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    assert c.get("/api/v2/account/closure").json()["state"] == "none"
    res = c.post("/api/v2/account/closure/dispositions", json={"athlete_id": aid, "disposition": "export_and_erase"},
                 headers=hc)
    assert res.status_code == 409
    assert c.get("/api/v2/admin/closures").status_code == 403
