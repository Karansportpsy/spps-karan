"""Booking-only directory, phase 2 (SQLite tier): practitioner service catalogue, the public projection, the flag,
allowlist and preflight wiring, and the rule that public/portal handlers never touch clinical base tables.

The appointment request itself runs on the 13A identity functions, which exist only in PostgreSQL: those acceptance
and negative tests are in tests_v2_pg/test_booking_phase2_rls.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import text

from .conftest import PRACTITIONER_B, PRACTITIONER_C, fresh_client, login
from .test_phase7_directory import PROFILE, _published

ROOT = Path(__file__).resolve().parents[1]
SERVICE = {"name": "Pre-competition focus session", "short_description": "Fictional routine planning.",
           "duration_minutes": 50, "mode": "online"}
PUBLIC_SERVICE_KEYS = {"service_id", "name", "short_description", "duration_minutes", "mode"}


@pytest.fixture
def dir_app(v2env, tmp_path, fake_supabase, monkeypatch):
    monkeypatch.setenv("SPPS_DIRECTORY_ENABLED", "true")
    monkeypatch.setenv("SPPS_CRISIS_CONTACTS_TEXT", "Fictional helpline 0000 (24/7).")
    from test_support.app import create_app
    app = create_app(f"sqlite:///{tmp_path / 'svc.db'}", init_database=True, seed_demo=True)
    yield app
    app.state.engine.dispose()


def add(c, h, **over):
    r = c.post("/api/v2/practitioner-services", json={**SERVICE, **over}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ CRUD
def test_practitioner_adds_edits_reorders_and_deactivates_own_services(app):
    c = fresh_client(app)
    h = login(c, PRACTITIONER_B)
    empty = c.get("/api/v2/practitioner-services").json()
    # Not published yet: the panel must say why athletes cannot see the services.
    assert empty == {"services": [], "listed": False, "profile_status": "none"}
    a = add(c, h)
    b = add(c, h, name="Return-to-play confidence", mode="in_person", duration_minutes=60)
    assert (a["active"], a["sort_order"], b["sort_order"]) == (True, 0, 1)
    edited = c.put(f"/api/v2/practitioner-services/{a['id']}", json={**SERVICE, "name": "  Match   focus  ",
                                                                      "duration_minutes": 45}, headers=h)
    assert edited.status_code == 200 and edited.json()["name"] == "Match focus" and edited.json()["duration_minutes"] == 45
    order = c.post("/api/v2/practitioner-services/order", json={"service_ids": [b["id"], a["id"]]}, headers=h)
    assert [s["id"] for s in order.json()["services"]] == [b["id"], a["id"]]
    off = c.post(f"/api/v2/practitioner-services/{a['id']}/deactivate", headers=h)
    assert off.status_code == 200 and off.json()["active"] is False
    assert c.post(f"/api/v2/practitioner-services/{a['id']}/activate", headers=h).json()["active"] is True
    # There is no delete: history stays explainable.
    assert c.delete(f"/api/v2/practitioner-services/{a['id']}", headers=h).status_code in (404, 405)


@pytest.mark.parametrize("bad", [{"duration_minutes": 19}, {"duration_minutes": 121}, {"mode": "phone"},
                                 {"name": "ab"}, {"name": "   x   "}, {"short_description": "x" * 281},
                                 {"price": 1500, "name": "x"}])
def test_invalid_services_are_refused(app, bad):
    c = fresh_client(app)
    h = login(c, PRACTITIONER_B)
    assert c.post("/api/v2/practitioner-services", json={**SERVICE, **bad}, headers=h).status_code == 422


def test_service_has_no_price_anywhere():
    from server.v2.models import DirectoryServiceSnapshot, PractitionerService
    for model in (PractitionerService, DirectoryServiceSnapshot):
        assert not [c for c in model.__table__.columns if "price" in c.name or "paise" in c.name or "fee" in c.name]
    sql = (ROOT / "migrations" / "0043_v2_practitioner_services.sql").read_text(encoding="utf-8").lower()
    create = sql.split("create table spps_dev.practitioner_services")[1].split(");")[0]
    assert "price" not in create and "paise" not in create


def test_practitioner_cannot_see_or_change_another_practitioners_services(app):
    b, hb = fresh_client(app), None
    hb = login(b, PRACTITIONER_B)
    mine = add(b, hb)
    c = fresh_client(app)
    hc = login(c, PRACTITIONER_C)
    assert c.get("/api/v2/practitioner-services").json()["services"] == []
    sid = mine["id"]
    assert c.put(f"/api/v2/practitioner-services/{sid}", json=SERVICE, headers=hc).status_code == 404
    assert c.post(f"/api/v2/practitioner-services/{sid}/deactivate", headers=hc).status_code == 404
    assert c.post(f"/api/v2/practitioner-services/{sid}/activate", headers=hc).status_code == 404
    assert c.post("/api/v2/practitioner-services/order", json={"service_ids": [sid]}, headers=hc).status_code == 422
    assert b.get("/api/v2/practitioner-services").json()["services"][0]["active"] is True


def test_signed_out_visitor_cannot_use_the_practitioner_api(app):
    anon = fresh_client(app)
    assert anon.get("/api/v2/practitioner-services").status_code == 401
    assert anon.post("/api/v2/practitioner-services", json=SERVICE).status_code in (401, 403)


# ------------------------------------------------------------------ public card
def test_public_card_shows_only_active_services_of_listed_practitioners(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    active = add(p, hp)
    hidden = add(p, hp, name="Fictional inactive service")
    assert p.post(f"/api/v2/practitioner-services/{hidden['id']}/deactivate", headers=hp).status_code == 200
    # Another practitioner, not listed, with an active service: never public.
    c = fresh_client(dir_app)
    hc = login(c, PRACTITIONER_C)
    add(c, hc, name="Unlisted practitioner service")
    anon = fresh_client(dir_app)
    for card in (anon.get("/api/v2/public/directory").json()["results"][0], anon.get(f"/api/v2/public/directory/{pid}").json()):
        assert [s["name"] for s in card["services"]] == [SERVICE["name"]]
        assert set(card["services"][0]) == PUBLIC_SERVICE_KEYS
        assert card["services"][0]["service_id"] == active["id"]
    assert "Unlisted practitioner service" not in anon.get("/api/v2/public/directory").text
    # Edits show at once; delisting removes the services with the card.
    p.put(f"/api/v2/practitioner-services/{active['id']}", json={**SERVICE, "name": "Renamed fictional session"}, headers=hp)
    assert anon.get(f"/api/v2/public/directory/{pid}").json()["services"][0]["name"] == "Renamed fictional session"
    assert owner.post(f"/api/v2/admin/directory/{pid}/delist", json={"reason": "Fictional"}, headers=ho).status_code == 200
    assert anon.get(f"/api/v2/public/directory/{pid}").status_code == 404
    assert "Renamed fictional session" not in anon.get("/api/v2/public/directory").text


def test_public_card_reads_the_projection_not_the_practitioner_table(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    s = add(p, hp)
    # A change written only to the practitioner's own table is not public until the app syncs the projection.
    with dir_app.state.engine.begin() as conn:
        conn.execute(text("UPDATE practitioner_services SET name='PRIVATE-MARKER' WHERE id=:i"), {"i": s["id"]})
    assert "PRIVATE-MARKER" not in fresh_client(dir_app).get(f"/api/v2/public/directory/{pid}").text


def test_reorder_is_reflected_on_the_public_card(dir_app):
    p, hp, owner, ho, pid = _published(dir_app)
    a, b = add(p, hp), add(p, hp, name="Second fictional service")
    p.post("/api/v2/practitioner-services/order", json={"service_ids": [b["id"], a["id"]]}, headers=hp)
    names = [s["name"] for s in fresh_client(dir_app).get(f"/api/v2/public/directory/{pid}").json()["services"]]
    assert names == ["Second fictional service", SERVICE["name"]]


# ------------------------------------------------------------------ flag, allowlist, preflight
def test_directory_booking_is_off_by_default_and_needs_portal_and_directory(monkeypatch):
    from server.v2 import flags
    assert flags.FLAGS["SPPS_DIRECTORY_BOOKING_ENABLED"][0] == "false"
    monkeypatch.setenv("SPPS_DIRECTORY_BOOKING_ENABLED", "true")
    monkeypatch.setenv("SPPS_DIRECTORY_ENABLED", "true")
    monkeypatch.setenv("SPPS_PORTAL_ENABLED", "false")
    assert not flags.directory_booking_enabled()
    monkeypatch.setenv("SPPS_PORTAL_ENABLED", "true")
    assert flags.directory_booking_enabled()


def test_booking_routes_are_unreachable_while_the_flag_is_off(dir_app):
    anon = fresh_client(dir_app)
    body = {"practitioner_id": 1, "service_id": 1, "slot_id": 1, "subject_person_id": 1}
    assert anon.post("/api/portal/directory-bookings", json=body).status_code in (403, 404)
    assert anon.get("/api/portal/directory/1/slots?service_id=1").status_code in (403, 404)


def test_allowlist_gates_booking_routes_on_the_flag(monkeypatch):
    from server.v2.allowlist import v2_allowed
    assert v2_allowed("GET", "/api/v2/practitioner-services") is True
    assert v2_allowed("POST", "/api/v2/practitioner-services/7/deactivate") is True
    assert v2_allowed("DELETE", "/api/v2/practitioner-services/7") is False
    assert v2_allowed("POST", "/api/portal/directory-bookings") is False
    for k in ("SPPS_DIRECTORY_BOOKING_ENABLED", "SPPS_DIRECTORY_ENABLED", "SPPS_PORTAL_ENABLED"):
        monkeypatch.setenv(k, "true")
    assert v2_allowed("POST", "/api/portal/directory-bookings") is True
    assert v2_allowed("GET", "/api/portal/directory/3/slots") is True
    assert v2_allowed("GET", "/api/portal/directory-bookings") is not True


def test_preflight_requires_0043_and_the_prerequisite_flags():
    from server.v2 import preflight
    assert "0043_v2_practitioner_services.sql" in preflight.FLAG_MIGRATIONS["SPPS_DIRECTORY_BOOKING_ENABLED"]
    assert {"table", "function"} == {k for k, _ in preflight.MIGRATION_OBJECTS["0043_v2_practitioner_services.sql"]}
    base = {"SPPS_FORM_TOKEN_SECRET": "x" * 40, "SPPS_FIELD_KEYS": "k1:" + "A" * 43 + "=", "SPPS_FIELD_KEY_CURRENT": "k1"}
    with pytest.raises(preflight.PreflightError, match="DIRECTORY_BOOKING"):
        preflight.validate_env({**base, "SPPS_DIRECTORY_BOOKING_ENABLED": "true", "SPPS_DIRECTORY_ENABLED": "true",
                                "SPPS_CRISIS_CONTACTS_TEXT": "Fictional helpline 0000"})


def test_booking_endpoint_is_rate_limited_and_bot_checked():
    from server.v2 import guards
    assert guards.LIMITS["directory-booking"][0] <= 20 and guards.LIMITS["directory-booking-account"][0] <= 10
    src = (ROOT / "server" / "v2" / "directory_booking.py").read_text(encoding="utf-8")
    handler = src.split("def request_booking")[1]
    assert handler.index('limit_ip(request, "directory-booking")') < handler.index("check_bot(") \
        < handler.index('"directory-booking-account"') < handler.index("portal_request_directory_booking")
    assert "CLINICAL_WORDS.search(note)" in handler


# ------------------------------------------------------------------ no clinical base table from public/portal handlers
def test_public_and_portal_handlers_import_no_clinical_base_table():
    """directory_booking.py may use only the public read models; v1 clinical models are never imported."""
    tree = ast.parse((ROOT / "server" / "v2" / "directory_booking.py").read_text(encoding="utf-8"))
    imported = {(n.module or "", a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert ("models", "DirectoryServiceSnapshot") in imported and ("models", "DirectorySnapshot") in imported
    assert not [x for x in imported if x[0] in ("..models", "models") and x[1] not in
                ("DirectoryServiceSnapshot", "DirectorySnapshot")], imported
    assert not [x for x in imported if x[0] == "" and x[1] == "models"]
    import server.v2.directory_booking as m
    assert not hasattr(m, "PractitionerService") and not hasattr(m, "Athlete") and not hasattr(m, "AppointmentSlot")
    src = (ROOT / "server" / "v2" / "services.py").read_text(encoding="utf-8")
    public = src.split("def public_services")[1].split("\ndef ")[0]
    assert "DirectoryServiceSnapshot" in public and "PractitionerService" not in public


def test_serial_file_is_generated_and_unchanged():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "v2_serial_sql.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    serial = (ROOT / "supabase" / "sql" / "44_0043_v2_practitioner_services.sql").read_text(encoding="utf-8")
    body = serial.split("SET LOCAL ROLE spps_identity_owner;")[1].split("RESET ROLE;")[0]
    # Every identity-schema object is created or altered as spps_identity_owner (what broke 0042).
    before = serial.split("SET LOCAL ROLE spps_identity_owner;")[0]
    assert "spps_identity_dev" not in "\n".join(l for l in before.splitlines() if not l.startswith("--"))
    assert "ALTER TABLE spps_identity_dev.practitioner_athlete_links" in body
    after = serial.split("RESET ROLE;")[1]
    assert "spps_identity_dev" not in after.split("INSERT INTO spps_ops.migration_history")[0]
