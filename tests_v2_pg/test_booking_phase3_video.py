"""Real identity functions and API: Phase 3 transitions, privacy, guardians, video and installer privileges."""
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from .conftest import dsn, make_practitioner, pg_login, q
from .test_booking_phase2_rls import (booking_app, client, consent, listed_practitioner, portal_account,
                                    request, _guardian_with_dependant, SERVICE)

ROOT = Path(__file__).resolve().parents[1]


def requested(app, superuser, tenant_id, *, service=None, guardian=None):
    pr, p, service_id, slot_id = listed_practitioner(app, superuser, tenant_id, service=service)
    a, person, _ = portal_account(app) if guardian is None else (*guardian, None)
    consent(a, person, pr["id"])
    res = request(a, pr, service_id, slot_id, person)
    assert res.status_code == 201, res.text
    return pr, p, a, res.json()["booking_id"]


def another_slot(p, booking):
    original = next(b for b in p.get("/api/booking-requests").json() if b["booking_id"] == booking)
    start = datetime.fromisoformat(original["starts_at"]) + timedelta(days=2)
    r = p.post("/api/slots", json={"starts_at": start.isoformat(), "duration_minutes": 50, "amount_paise": 0})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("accept", [True, False])
def test_pending_proposal_response_preserves_service_and_approval_first(booking_app, superuser, tenant_id,
                                                                        id_superuser, accept):
    pr, p, a, bid = requested(booking_app, superuser, tenant_id)
    r = p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": another_slot(p, bid)})
    assert r.status_code == 201, r.text
    proposal = r.json()["proposal_booking_id"]
    row = next(b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == proposal)
    assert row["status"] == "RESCHEDULE_PROPOSED" and row["service_name"] == SERVICE["name"]
    res = a.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": accept})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == ("PENDING_APPROVAL" if accept else "DECLINED")
    assert a.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": accept}).status_code == 404
    if accept:
        approved = p.post(f"/api/booking-requests/{proposal}/decision", json={"decision": "approve"})
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "CONFIRMED"
        assert q(id_superuser, "SELECT status,athlete_record_id FROM spps_identity_dev.practitioner_athlete_links WHERE practitioner_id=%s",
                 (pr["id"],)) == [("ACTIVE", approved.json()["athlete_id"])]
        original = next(b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == bid)
        assert original["status"] == "CANCELLED"
    else:
        original = next(b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == bid)
        assert original["status"] == "PENDING_APPROVAL"


def test_confirmed_reschedule_accept_stays_confirmed(booking_app, superuser, tenant_id):
    _, p, a, bid = requested(booking_app, superuser, tenant_id)
    assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).json()["status"] == "CONFIRMED"
    res = p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": another_slot(p, bid)})
    proposal = res.json()["proposal_booking_id"]
    r = a.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": True})
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED", r.text


@pytest.mark.parametrize("terminal", ["declined", "past"])
def test_no_decision_or_proposal_from_declined_or_past(booking_app, superuser, tenant_id, id_superuser, terminal):
    _, p, _, bid = requested(booking_app, superuser, tenant_id)
    slot = another_slot(p, bid)
    if terminal == "declined":
        r = p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "decline"})
        assert r.status_code == 200 and r.json()["status"] == "DECLINED"
    else:
        q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-interval '2 hours',ends_at=now()-interval '1 hour' WHERE id=%s", (bid,))
    for decision in ("approve", "decline"):
        assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": decision}).status_code == 409
    assert p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": slot}).status_code == 409


def test_other_practitioner_gets_404_for_decision_proposal_and_ticket(booking_app, superuser, tenant_id):
    _, p, _, bid = requested(booking_app, superuser, tenant_id)
    other = client(booking_app)
    other.headers.update(pg_login(other, make_practitioner(superuser, tenant_id)))
    assert other.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).status_code == 404
    assert other.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": 999999}).status_code == 404
    assert other.post(f"/api/booking-requests/{bid}/video-ticket").status_code == 404


@pytest.mark.parametrize("terminal", ["declined", "past"])
def test_proposal_response_cannot_revive_a_terminal_original(booking_app, superuser, tenant_id, id_superuser, terminal):
    _, p, a, bid = requested(booking_app, superuser, tenant_id)
    r = p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": another_slot(p, bid)})
    proposal = r.json()["proposal_booking_id"]
    if terminal == "declined":
        assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "decline"}).status_code == 200
    else:
        q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-interval '2 hours',ends_at=now()-interval '1 hour' WHERE id=%s", (bid,))
    for accept in (True, False):
        assert a.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": accept}).status_code == 409
    assert q(id_superuser, "SELECT status FROM spps_identity_dev.platform_bookings WHERE id=%s", (bid,))[0][0] == ("DECLINED" if terminal == "declined" else "PENDING_APPROVAL")


def test_fixed_reasons_and_minimum_data_survive_directory_flag_off(booking_app, superuser, tenant_id, monkeypatch):
    _, p, _, bid = requested(booking_app, superuser, tenant_id)
    monkeypatch.setenv("SPPS_DIRECTORY_BOOKING_ENABLED", "false")
    row = next(b for b in p.get("/api/booking-requests").json() if b["booking_id"] == bid)
    assert row["first_request"] and row["service_name"]
    assert not {"client_note", "via_guardian", "paid", "amount_paise", "payment_status"} & row.keys()
    assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "decline", "reason": "Private narrative"}).status_code == 422
    r = p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "decline", "reason": "Service unavailable"})
    assert r.status_code == 200 and r.json()["status"] == "DECLINED"


@pytest.mark.parametrize("case", ["before", "inside", "after", "in_person", "pending"])
def test_video_ticket_server_window_and_eligibility(booking_app, superuser, tenant_id, id_superuser, case):
    service = {"mode": "in_person"} if case == "in_person" else None
    _, p, a, bid = requested(booking_app, superuser, tenant_id, service=service)
    if case != "pending":
        assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).status_code == 200
    start = {"before": "11 minutes", "after": "-66 minutes"}.get(case, "9 minutes")
    q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+%s::interval,ends_at=now()+%s::interval+interval '50 minutes' WHERE id=%s",
      (start, start, bid))
    expected = 200 if case == "inside" else 409
    for c, base in [(p, "/api/booking-requests"), (a, "/api/portal/bookings")]:
        r = c.post(f"{base}/{bid}/video-ticket")
        assert r.status_code == expected, (case, r.status_code)
        if expected == 200:
            assert r.json()["room_url"].startswith(f"/video/{bid}?ticket=")


def test_third_account_cannot_get_video_or_respond_to_proposal(booking_app, superuser, tenant_id):
    _, p, a, bid = requested(booking_app, superuser, tenant_id)
    third, _, _ = portal_account(booking_app)
    res = p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": another_slot(p, bid)})
    proposal = res.json()["proposal_booking_id"]
    assert third.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": True}).status_code == 404
    assert third.post(f"/api/portal/bookings/{bid}/video-ticket").status_code == 404
    assert third.get("/api/portal/bookings").json() == []


def test_guardian_responds_and_joins_only_while_authority_is_valid(booking_app, pgenv, superuser, tenant_id, id_superuser):
    g, dependant = _guardian_with_dependant(booking_app, pgenv, ["booking_management", "appointment_metadata", "consent_management"])
    _, p, _, bid = requested(booking_app, superuser, tenant_id, guardian=(g, dependant))
    res = p.post(f"/api/booking-requests/{bid}/reschedule", json={"slot_id": another_slot(p, bid)})
    proposal = res.json()["proposal_booking_id"]
    assert g.post(f"/api/portal/bookings/{proposal}/reschedule-response", json={"accept": True}).json()["status"] == "PENDING_APPROVAL"
    assert p.post(f"/api/booking-requests/{proposal}/decision", json={"decision": "approve"}).status_code == 200
    q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '5 minutes',ends_at=now()+interval '55 minutes' WHERE id=%s", (proposal,))
    ticket = g.post(f"/api/portal/bookings/{proposal}/video-ticket")
    assert ticket.status_code == 200
    from urllib.parse import urlsplit
    query = urlsplit(ticket.json()["room_url"]).query
    assert g.get(f"/api/video/{proposal}/status?{query}").status_code == 200
    q(id_superuser, "UPDATE spps_identity_dev.guardian_authorities SET status='REVOKED',revoked_at=now() WHERE dependent_person_id=%s", (dependant,))
    assert g.post(f"/api/portal/bookings/{proposal}/video-ticket").status_code == 404
    assert g.get(f"/api/video/{proposal}/status?{query}").status_code == 403


def test_video_exact_boundaries_and_ticket_revalidation(booking_app, superuser, tenant_id, pgenv):
    pr, p, _, bid = requested(booking_app, superuser, tenant_id)
    assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).status_code == 200
    # One PostgreSQL transaction fixes now() for exact boundary assertions.
    with psycopg.connect(dsn(pgenv["SPPS_V2_PG_IDENTITY_ADMIN_URL"])) as c:
        for start, end, expected in [("10 minutes", "60 minutes", True), ("10 minutes 1 second", "60 minutes", False),
                                      ("-65 minutes", "-15 minutes", True), ("-65 minutes", "-15 minutes -1 second", False)]:
            q(c, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+%s::interval,ends_at=now()+%s::interval WHERE id=%s", (start, end, bid))
            assert q(c, "SELECT spps_identity_dev.video_booking_open(b) FROM spps_identity_dev.platform_bookings b WHERE id=%s", (bid,))[0][0] is expected
        q(c, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '5 minutes',ends_at=now()+interval '55 minutes' WHERE id=%s", (bid,))
        ticket_hash = "a" * 64
        q(c, "SELECT spps_identity_dev.practitioner_video_ticket(%s,%s,%s)", (pr["id"], bid, ticket_hash))
        assert q(c, "SELECT * FROM spps_identity_dev.video_ticket_check(%s)", (ticket_hash,))
        q(c, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-interval '66 minutes',ends_at=now()-interval '16 minutes' WHERE id=%s", (bid,))
        assert q(c, "SELECT * FROM spps_identity_dev.video_ticket_check(%s)", (ticket_hash,)) == []


def test_phase3_serial_installs_as_non_superuser_and_keeps_grants(id_superuser):
    # Reproduce the normal managed installer's identity ownership without granting superuser/bypassrls.
    q(id_superuser, "CREATE ROLE spps_phase3_test_installer NOLOGIN NOSUPERUSER NOBYPASSRLS")
    try:
        q(id_superuser, "GRANT spps_identity_owner TO spps_phase3_test_installer")
        q(id_superuser, "GRANT USAGE ON SCHEMA spps_ops TO spps_phase3_test_installer")
        q(id_superuser, "GRANT SELECT,INSERT ON spps_ops.migration_history TO spps_phase3_test_installer")
        q(id_superuser, "DROP FUNCTION spps_identity_dev.portal_booking_details(text)")
        q(id_superuser, "SET SESSION AUTHORIZATION spps_phase3_test_installer")
        assert q(id_superuser, "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user") == [(False, False)]
        id_superuser.execute((ROOT / "supabase/sql/46_0045_v2_appointments_video.sql").read_text(encoding="utf-8"))
    finally:
        q(id_superuser, "ROLLBACK")
        q(id_superuser, "RESET SESSION AUTHORIZATION")
        q(id_superuser, "REVOKE spps_identity_owner FROM spps_phase3_test_installer")
        q(id_superuser, "REVOKE ALL ON SCHEMA spps_ops FROM spps_phase3_test_installer")
        q(id_superuser, "REVOKE ALL ON spps_ops.migration_history FROM spps_phase3_test_installer")
        q(id_superuser, "DROP ROLE spps_phase3_test_installer")
    rows = q(id_superuser, """SELECT pg_get_userbyid(proowner),prosecdef,
           has_function_privilege('spps_identity_auth',oid,'EXECUTE'),
           has_function_privilege('spps_identity_portal',oid,'EXECUTE')
           FROM pg_proc WHERE oid='spps_identity_dev.portal_booking_details(text)'::regprocedure""")
    assert rows == [("spps_identity_owner", True, True, False)]
