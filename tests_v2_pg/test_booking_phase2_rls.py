"""Booking-only directory, phase 2 on PostgreSQL (migration 0043): service RLS, the public projection, and the whole
directory booking path on the real 13A identity functions with the real flags on.

Acceptance: a verified adult athlete picks a listed practitioner and an active service, requests a published slot, and
the practitioner sees it as PENDING_APPROVAL with the minimum (display name, service, time, note); approval creates the
practitioner's own record, activates the link and confirms. Negative: unauthenticated, missing consent, clinical note,
duplicate, minor without verified guardian authority (or without booking_management), rate limit, other practitioner.
"""
from __future__ import annotations

import itertools
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import TENANT, as_identity, as_role, denied, make_practitioner, pg_login, q
from .test_phase7_rls import _snapshot

PASSWORD = "Fictional-Portal-Pass-2026"
SERVICE = {"name": "Fictional focus session", "short_description": "Routine planning.", "duration_minutes": 50,
           "mode": "online"}
_ip = itertools.count(random.SystemRandom().randint(1, 200))
_hours = itertools.count(random.SystemRandom().randint(0, 150000))


# ------------------------------------------------------------------ database policies
def _service(superuser, person, *, active=True, name="Fictional PG service"):
    return q(superuser, """INSERT INTO spps_dev.practitioner_services(practitioner_id,tenant_id,name,duration_minutes,mode,
                           active) VALUES (%(p)s,%(t)s,%(n)s,50,'online',%(a)s) RETURNING id""",
             {"p": person["id"], "t": person["tenant_id"], "n": name, "a": active})[0][0]


def test_practitioner_services_are_own_rows_only(pgenv, superuser, tenant_id):
    a, b = make_practitioner(superuser, tenant_id), make_practitioner(superuser, tenant_id)
    b_service = _service(superuser, b)
    with as_role(pgenv, "spps_dev_app", a) as conn:
        q(conn, """INSERT INTO practitioner_services(practitioner_id,tenant_id,name,duration_minutes,mode)
                   VALUES (%(p)s,%(t)s,'Fictional own service',50,'online')""", {"p": a["id"], "t": tenant_id})
        assert q(conn, "SELECT count(*) FROM practitioner_services")[0][0] == 1
        assert q(conn, "SELECT count(*) FROM practitioner_services WHERE id=%(i)s", {"i": b_service})[0][0] == 0
        assert denied(conn, """INSERT INTO practitioner_services(practitioner_id,tenant_id,name,duration_minutes,mode)
                               VALUES (%(p)s,%(t)s,'Forged',50,'online')""", {"p": b["id"], "t": tenant_id})
        with conn.cursor() as cur:
            cur.execute("UPDATE practitioner_services SET name='Hijacked' WHERE id=%(i)s", {"i": b_service})
            assert cur.rowcount == 0
        assert denied(conn, "DELETE FROM practitioner_services WHERE practitioner_id=%(p)s", {"p": a["id"]})
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            q(conn, """INSERT INTO practitioner_services(practitioner_id,tenant_id,name,duration_minutes,mode)
                       VALUES (%(p)s,%(t)s,'Too long a session',240,'online')""", {"p": a["id"], "t": tenant_id})
    assert q(superuser, "SELECT name FROM spps_dev.practitioner_services WHERE id=%(i)s", {"i": b_service})[0][0] \
        == "Fictional PG service"


def test_service_projection_is_public_only_while_listed_and_written_only_by_its_owner(pgenv, superuser, tenant_id):
    x, y = make_practitioner(superuser, tenant_id), make_practitioner(superuser, tenant_id)
    sid, inactive, y_sid = _service(superuser, x), _service(superuser, x, active=False), _service(superuser, y)
    with as_role(pgenv, "spps_dev_app", x) as conn:
        q(conn, """INSERT INTO directory_service_snapshot(service_id,practitioner_id,tenant_id,name,duration_minutes,mode)
                   VALUES (%(s)s,%(p)s,%(t)s,'Fictional PG service',50,'online')""", {"s": sid, "p": x["id"], "t": tenant_id})
        assert denied(conn, """INSERT INTO directory_service_snapshot(service_id,practitioner_id,tenant_id,name,
                               duration_minutes,mode) VALUES (%(s)s,%(p)s,%(t)s,'Inactive',50,'online')""",
                      {"s": inactive, "p": x["id"], "t": tenant_id})
        assert denied(conn, """INSERT INTO directory_service_snapshot(service_id,practitioner_id,tenant_id,name,
                               duration_minutes,mode) VALUES (%(s)s,%(p)s,%(t)s,'Not mine',50,'online')""",
                      {"s": y_sid, "p": y["id"], "t": tenant_id})
        conn.commit()
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT count(*) FROM directory_service_snapshot WHERE practitioner_id=%(p)s", {"p": x["id"]})[0][0] == 0
        assert q(anon, "SELECT count(*) FROM practitioner_services")[0][0] == 0
    _snapshot(superuser, x)
    with as_role(pgenv, "spps_dev_app") as anon:
        assert q(anon, "SELECT service_id FROM directory_service_snapshot WHERE practitioner_id=%(p)s", {"p": x["id"]}) == [(sid,)]
        assert q(anon, "SELECT count(*) FROM practitioner_services")[0][0] == 0  # the base table stays closed
        with anon.cursor() as cur:
            cur.execute("DELETE FROM directory_service_snapshot WHERE service_id=%(s)s", {"s": sid})
            assert cur.rowcount == 0
    with as_role(pgenv, "spps_dev_app", y) as other:
        with other.cursor() as cur:
            cur.execute("UPDATE directory_service_snapshot SET name='Hijacked' WHERE service_id=%(s)s", {"s": sid})
            assert cur.rowcount == 0


@pytest.mark.parametrize("role", ["spps_identity_portal", "spps_identity_auth"])
def test_portal_and_identity_roles_cannot_reach_clinical_tables(pgenv, role):
    with as_identity(pgenv, role) as conn:
        for table in ("practitioner_services", "directory_service_snapshot", "athletes", "appointment_slots", "notes"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                q(conn, f"SELECT 1 FROM spps_dev.{table} LIMIT 1")


def test_identity_functions_are_owned_by_the_identity_owner_and_not_public(id_superuser):
    rows = q(id_superuser, """SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef,
                                     has_function_privilege('spps_identity_portal', p.oid, 'EXECUTE'),
                                     has_function_privilege('spps_identity_auth', p.oid, 'EXECUTE')
                              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                              WHERE n.nspname='spps_identity_dev' AND p.proname IN ('portal_directory_booking_context',
                              'portal_request_directory_booking','practitioner_booking_details',
                              'practitioner_approve_directory_booking','practitioner_decline_directory_link')""")
    assert len(rows) == 5
    assert all(owner == "spps_identity_owner" and definer and not portal and auth
               for _, owner, definer, portal, auth in rows), rows
    # Existing links and bookings are untouched: every link that was ACTIVE still has a record.
    assert q(id_superuser, """SELECT count(*) FROM spps_identity_dev.practitioner_athlete_links
                              WHERE status='ACTIVE' AND athlete_record_id IS NULL""")[0][0] == 0


# ------------------------------------------------------------------ the application with every real flag on
@pytest.fixture
def booking_app(pgenv, monkeypatch, tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    for k, v in {"MODEL_C_ENV": "local", "MODEL_C_INTEGRATION_DEV": "true", "SPPS_DEV_CLINICAL_PARITY": "true",
                 "SPPS_BETA_V1_FREE": "true", "SPPS_SUPABASE_AUTH_ENABLED": "false", "SPPS_BOT_MIN_FILL_SECONDS": "0",
                 "MODEL_C_PUBLIC_TENANT": TENANT, "SPPS_PORTAL_ENABLED": "true", "SPPS_DIRECTORY_ENABLED": "true",
                 "SPPS_DIRECTORY_BOOKING_ENABLED": "true", "SPPS_CRISIS_CONTACTS_TEXT": "Fictional helpline 0000 (24/7).",
                 "SPPS_PORTAL_DEV_OUTBOX": str(outbox),
                 "SPPS_FIELD_KEYS": "k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=", "SPPS_FIELD_KEY_CURRENT": "k1",
                 "DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_APP_URL"],
                 "SPPS_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_AUTH_URL"],
                 "SPPS_IDENTITY_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_AUTH_URL"],
                 "SPPS_IDENTITY_PORTAL_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_PORTAL_URL"]}.items():
        monkeypatch.setenv(k, v)
    from server.v2 import guards
    guards.reset()
    import server.main as main
    app = main.create_app(None, init_database=False)
    app.state.test_outbox = outbox
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()
    for name in ("portal_auth_engine", "portal_engine"):
        engine = getattr(app.state, name, None)
        if engine is not None:
            engine.dispose()


def client(app, ip=None):
    return TestClient(app, client=(ip or f"198.51.100.{next(_ip) % 250 + 1}", 50000))


def portal_account(app, role="athlete", ip=None):
    c = client(app, ip)
    email = f"pg-book-{uuid.uuid4().hex[:10]}@example.invalid"
    r = c.post("/api/portal/auth/register", json={"email": email, "password": PASSWORD, "role": role,
                                                  "display_name": f"Fictional {role.title()} {email[8:12]}",
                                                  "adult_attested": True, "terms_accepted": True})
    assert r.status_code == 202, r.text
    token = [json.loads(line) for line in Path(app.state.test_outbox).read_text(encoding="utf-8").splitlines()
             if json.loads(line)["to"] == email and json.loads(line)["kind"] == "verify-email"][-1]["token"]
    assert c.post("/api/portal/auth/verify-email", json={"token": token}).status_code == 200
    r = c.post("/api/portal/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    me = c.get("/api/portal/auth/me").json()
    return c, me["person_id"], email


def listed_practitioner(app, superuser, tenant_id, *, minutes=60, service=None, taking=True):
    person = make_practitioner(superuser, tenant_id)
    _snapshot(superuser, person)
    if not taking:
        q(superuser, "UPDATE spps_dev.directory_snapshot SET taking_new_clients=false WHERE practitioner_id=%(p)s",
          {"p": person["id"]})
    p = client(app)
    pg_login_headers = pg_login(p, person)
    p.headers.update(pg_login_headers)
    s = p.post("/api/v2/practitioner-services", json={**SERVICE, **(service or {})})
    assert s.status_code == 201, s.text
    start = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
             + timedelta(hours=next(_hours) * 3 + 48))
    slot = p.post("/api/slots", json={"starts_at": start.isoformat(), "duration_minutes": minutes, "amount_paise": 0})
    assert slot.status_code == 201, slot.text
    return person, p, s.json()["id"], slot.json()["id"]


def consent(c, person_id, practitioner_id, *purposes):
    for purpose in purposes or ("CASE_LINK", "APPOINTMENT_BOOKING", "VIDEO_CONSULTATION"):
        r = c.post("/api/portal/consents", json={"subject_person_id": person_id, "purpose": purpose,
                                                 "practitioner_id": practitioner_id, "decision": "GRANTED"})
        assert r.status_code == 201, r.text


def request(c, practitioner, service_id, slot_id, person_id, note="Fictional practical note: weekday evenings."):
    token = c.get("/api/public/form-token").json()["form_token"]
    return c.post("/api/portal/directory-bookings", json={
        "practitioner_id": practitioner["id"], "service_id": service_id, "slot_id": slot_id,
        "subject_person_id": person_id, "note": note, "form_token": token, "website": ""})


def test_adult_books_from_the_directory_practitioner_sees_minimum_then_approves(booking_app, superuser, tenant_id,
                                                                                id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    card = client(booking_app).get(f"/api/v2/public/directory/{pr['id']}").json()
    assert [s["service_id"] for s in card["services"]] == [service_id]
    a, person, email = portal_account(booking_app)
    res = a.get(f"/api/portal/directory/{pr['id']}/slots?service_id={service_id}")
    assert res.status_code == 200, res.text
    assert [s["slot_id"] for s in res.json()["slots"]] == [slot_id]
    consent(a, person, pr["id"])
    r = request(a, pr, service_id, slot_id, person)
    assert r.status_code == 201, r.text
    booking = r.json()["booking_id"]
    assert r.json()["status"] == "PENDING_APPROVAL" and "crisis" not in r.json()["message"].lower()
    assert "Fictional helpline" in r.json()["notice"]
    # The slot is now held for everyone else.
    assert a.get(f"/api/portal/directory/{pr['id']}/slots?service_id={service_id}").json()["slots"] == []
    # Practitioner: pending, first request, minimum data only; no clinical record exists yet.
    row = [b for b in p.get("/api/booking-requests").json() if b["booking_id"] == booking][0]
    assert (row["status"], row["first_request"], row["service_name"]) == ("PENDING_APPROVAL", True, SERVICE["name"])
    assert row["subject_display_name"].startswith("Fictional Athlete")
    assert email not in json.dumps(row) and "email" not in row
    assert p.get("/api/athletes").json() == [] or all(x["display_name"] != row["subject_display_name"]
                                                      for x in p.get("/api/athletes").json())
    link = q(id_superuser, """SELECT status, origin, athlete_record_id FROM spps_identity_dev.practitioner_athlete_links
                              WHERE person_id=%(p)s AND practitioner_id=%(r)s""", {"p": person, "r": pr["id"]})
    assert link == [("PRACTITIONER_REVIEW", "directory", None)]
    portal_links = a.get("/api/portal/links").json()
    assert [l["status"] for l in portal_links if l["practitioner_id"] == pr["id"]] == ["PRACTITIONER_REVIEW"]
    # Another practitioner can neither see nor decide it.
    other = make_practitioner(superuser, tenant_id)
    o = client(booking_app)
    o.headers.update(pg_login(o, other))
    assert booking not in [b["booking_id"] for b in o.get("/api/booking-requests").json()]
    assert o.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"}).status_code == 404
    # Approve: record created under the practitioner's RLS, link ACTIVE, booking CONFIRMED.
    d = p.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "CONFIRMED"
    athlete_id = d.json()["athlete_id"]
    assert athlete_id in [x["id"] for x in p.get("/api/athletes").json()]
    assert q(id_superuser, """SELECT status, athlete_record_id FROM spps_identity_dev.practitioner_athlete_links
                              WHERE person_id=%(p)s AND practitioner_id=%(r)s""", {"p": person, "r": pr["id"]}) \
        == [("ACTIVE", athlete_id)]
    mine = [b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == booking][0]
    assert mine["status"] == "CONFIRMED"
    # Once linked, a further request uses the same active link and does not need a new record.
    slot2 = p.post("/api/slots", json={"starts_at": (datetime.now(timezone.utc) + timedelta(days=400 + next(_hours) % 300))
                                       .replace(minute=0, second=0, microsecond=0).isoformat(),
                                       "duration_minutes": 60, "amount_paise": 0}).json()["id"]
    again = request(a, pr, service_id, slot2, person)
    assert again.status_code == 201, again.text
    assert again.json()["booking_id"] != booking
    second = [b for b in p.get("/api/booking-requests").json() if b["booking_id"] == again.json()["booking_id"]][0]
    assert second["first_request"] is False
    assert p.post(f"/api/booking-requests/{again.json()['booking_id']}/decision",
                  json={"decision": "approve"}).json()["status"] == "CONFIRMED"


def test_unauthenticated_and_forged_requests_are_refused(booking_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    anon = client(booking_app)
    body = {"practitioner_id": pr["id"], "service_id": service_id, "slot_id": slot_id, "subject_person_id": 1,
            "form_token": anon.get("/api/public/form-token").json()["form_token"]}
    assert anon.post("/api/portal/directory-bookings", json=body).status_code == 401
    assert anon.get(f"/api/portal/directory/{pr['id']}/slots?service_id={service_id}").status_code == 401
    # A practitioner session is not a portal session.
    assert p.post("/api/portal/directory-bookings", json=body).status_code == 401
    a, person, _ = portal_account(booking_app)
    consent(a, person, pr["id"])
    no_csrf = TestClient(booking_app, cookies=a.cookies)
    assert no_csrf.post("/api/portal/directory-bookings", json={**body, "subject_person_id": person}).status_code == 403
    # Bot check: missing token or a filled honeypot.
    assert a.post("/api/portal/directory-bookings",
                  json={**body, "subject_person_id": person, "form_token": ""}).status_code == 400
    assert a.post("/api/portal/directory-bookings",
                  json={**body, "subject_person_id": person, "website": "http://spam.invalid"}).status_code == 400
    # Booking for somebody else's person id is refused.
    b, other_person, _ = portal_account(booking_app)
    assert request(a, pr, service_id, slot_id, other_person).status_code == 403


def test_consent_note_duplicate_and_availability_rules(booking_app, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    a, person, _ = portal_account(booking_app)
    # No consent yet: refused, and nothing was created.
    assert request(a, pr, service_id, slot_id, person).status_code == 409
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.practitioner_athlete_links WHERE person_id=%(p)s",
             {"p": person})[0][0] == 0
    consent(a, person, pr["id"], "CASE_LINK", "APPOINTMENT_BOOKING")
    # Online service needs the video consent too.
    assert request(a, pr, service_id, slot_id, person).status_code == 409
    consent(a, person, pr["id"], "VIDEO_CONSULTATION")
    clinical = request(a, pr, service_id, slot_id, person, note="Fictional: I have a diagnosis of depression")
    assert clinical.status_code == 422 and "Fictional helpline" in clinical.json()["detail"]
    assert request(a, pr, service_id, slot_id, person, note="x" * 301).status_code == 422
    assert request(a, pr, service_id + 99999, slot_id, person).status_code == 404
    assert request(a, pr, service_id, slot_id + 99999, person).status_code == 409
    from server.v2 import guards
    guards.reset()  # the rest of this test checks rules, not the limiter (test_booking_rate_limit_holds does that)
    assert request(a, pr, service_id, slot_id, person).status_code == 201
    # One open request per pending link.
    slot2 = p.post("/api/slots", json={"starts_at": (datetime.now(timezone.utc) + timedelta(days=500 + next(_hours) % 300))
                                       .replace(minute=0, second=0, microsecond=0).isoformat(),
                                       "duration_minutes": 60, "amount_paise": 0}).json()["id"]
    dup = request(a, pr, service_id, slot2, person)
    assert dup.status_code == 409 and "already" in dup.json()["detail"]
    # A slot shorter than the service is not offered.
    short_pr, sp, short_service, short_slot = listed_practitioner(booking_app, superuser, tenant_id, minutes=30)
    consent(a, person, short_pr["id"])
    assert a.get(f"/api/portal/directory/{short_pr['id']}/slots?service_id={short_service}").json()["slots"] == []
    assert request(a, short_pr, short_service, short_slot, person).status_code == 409
    # Not taking new clients: a new person cannot start a link.
    closed_pr, cp, closed_service, closed_slot = listed_practitioner(booking_app, superuser, tenant_id, taking=False)
    consent(a, person, closed_pr["id"])
    assert request(a, closed_pr, closed_service, closed_slot, person).status_code == 409
    # An unlisted practitioner cannot be booked through the directory at all.
    q(superuser, "DELETE FROM spps_dev.directory_snapshot WHERE practitioner_id=%(p)s", {"p": closed_pr["id"]})
    assert request(a, closed_pr, closed_service, closed_slot, person).status_code == 404


def test_decline_closes_the_pending_link_and_a_new_request_can_follow(booking_app, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    a, person, _ = portal_account(booking_app)
    consent(a, person, pr["id"])
    booking = request(a, pr, service_id, slot_id, person).json()["booking_id"]
    r = p.post(f"/api/booking-requests/{booking}/decision", json={"decision": "decline", "reason": "Unavailable at this time"})
    assert r.status_code == 200 and r.json()["status"] == "DECLINED"
    assert q(id_superuser, """SELECT status FROM spps_identity_dev.practitioner_athlete_links
                              WHERE person_id=%(p)s AND practitioner_id=%(r)s""", {"p": person, "r": pr["id"]}) == [("DECLINED",)]
    assert p.get("/api/athletes").json() == []
    assert request(a, pr, service_id, slot_id, person).status_code == 201  # the freed slot can be asked for again


def _guardian_with_dependant(app, pgenv, scopes):
    from tools.identity_ops import grant_reviewer
    g, guardian_person, _ = portal_account(app, role="guardian")
    dep = g.post("/api/portal/dependants", json={"display_name": "Fictional Young Athlete"})
    assert dep.status_code == 201, dep.text
    dependant = dep.json()["person_id"]
    if scopes is None:
        return g, dependant
    r = g.post("/api/portal/guardian-authorities", json={"dependant_person_id": dependant, "scopes": scopes,
                                                         "evidence_reference": "Fictional birth record FX-9"})
    assert r.status_code == 201, r.text
    rev, _, rev_email = portal_account(app)
    grant_reviewer(pgenv["SPPS_V2_PG_IDENTITY_ADMIN_URL"], rev_email)
    d = rev.post(f"/api/portal/review/authorities/{r.json()['authority_id']}/decision",
                 json={"decision": "VERIFIED", "reason": "Fictional document checked", "valid_days": 30})
    assert d.status_code == 200, d.text
    return g, dependant


def test_minor_cannot_be_booked_without_verified_guardian_authority(booking_app, pgenv, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    # 1. Dependant added, no authority: the guardian may neither consent nor book.
    g, dependant = _guardian_with_dependant(booking_app, pgenv, None)
    assert g.post("/api/portal/consents", json={"subject_person_id": dependant, "purpose": "CASE_LINK",
                                                "practitioner_id": pr["id"], "decision": "GRANTED"}).status_code == 404
    assert request(g, pr, service_id, slot_id, dependant).status_code == 403
    # 2. Verified authority, but without booking_management: still refused.
    g2, dep2 = _guardian_with_dependant(booking_app, pgenv, ["appointment_metadata", "consent_management"])
    consent(g2, dep2, pr["id"])
    assert request(g2, pr, service_id, slot_id, dep2).status_code == 403
    # 3. A guardian account cannot book "for itself" as an athlete either.
    assert request(g2, pr, service_id, slot_id, g2.get("/api/portal/auth/me").json()["person_id"]).status_code == 403
    # 4. Verified authority with booking_management and consent: accepted, shown as via guardian.
    g3, dep3 = _guardian_with_dependant(booking_app, pgenv, ["booking_management", "consent_management"])
    consent(g3, dep3, pr["id"])
    ok = request(g3, pr, service_id, slot_id, dep3)
    assert ok.status_code == 201, ok.text
    row = [b for b in p.get("/api/booking-requests").json() if b["booking_id"] == ok.json()["booking_id"]][0]
    assert row["first_request"] is True and "via_guardian" not in row and "client_note" not in row


def test_approval_rechecks_guardian_authority(booking_app, pgenv, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    g, dep = _guardian_with_dependant(booking_app, pgenv, ["booking_management", "consent_management"])
    consent(g, dep, pr["id"])
    booking = request(g, pr, service_id, slot_id, dep).json()["booking_id"]
    q(id_superuser, """UPDATE spps_identity_dev.guardian_authorities SET status='REVOKED', revoked_at=now()
                       WHERE dependent_person_id=%(d)s""", {"d": dep})
    r = p.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"})
    assert r.status_code in (403, 409), r.text
    assert p.get("/api/athletes").json() == []  # the clinical record was rolled back with the refusal


def test_booking_rate_limit_holds(booking_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    a, person, _ = portal_account(booking_app, ip="203.0.113.77")
    codes = [a.post("/api/portal/directory-bookings", json={
        "practitioner_id": pr["id"], "service_id": service_id, "slot_id": slot_id, "subject_person_id": person,
        "form_token": "", "website": ""}).status_code for _ in range(22)]
    assert codes[:20] == [400] * 20 and codes[20:] == [429, 429]  # 20 per hour per address
    # The per-account ceiling (10 per hour) applies across addresses too.
    from server.v2 import guards
    guards.reset()
    b, person_b, _ = portal_account(booking_app)
    hits = []
    for i in range(12):
        c = TestClient(booking_app, cookies=b.cookies, headers={"X-CSRF-Token": b.headers["X-CSRF-Token"]},
                       client=(f"192.0.2.{i + 10}", 50000))
        token = c.get("/api/public/form-token").json()["form_token"]
        hits.append(c.post("/api/portal/directory-bookings", json={
            "practitioner_id": pr["id"], "service_id": service_id, "slot_id": slot_id + 99999,
            "subject_person_id": person_b, "form_token": token}).status_code)
    assert 429 not in hits[:10] and hits[10:] == [429, 429]


def test_public_card_over_postgresql_lists_only_active_services(booking_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(booking_app, superuser, tenant_id)
    extra = p.post("/api/v2/practitioner-services", json={**SERVICE, "name": "Fictional second service"}).json()["id"]
    p.post(f"/api/v2/practitioner-services/{extra}/deactivate")
    anon = client(booking_app)
    card = anon.get(f"/api/v2/public/directory/{pr['id']}").json()
    assert [s["service_id"] for s in card["services"]] == [service_id]
    assert set(card["services"][0]) == {"service_id", "name", "short_description", "duration_minutes", "mode"}
