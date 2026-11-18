"""Stage 13B: approval-first booking, sandbox payments, refunds, reschedule, concurrency.

Contract §B acceptance: simultaneous claims cannot both succeed; decline before payment
never refunds; payment without approval is rejected; expired hold releases the slot;
provider retries never double-confirm; verified capture confirms exactly once;
cancellation/refund auditable; guardian and practitioner access stays scoped.
"""
import itertools
import json
import random
import threading
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from server.payments import SandboxProvider
from tests_integration.test_portal_auth import client, verified_login
from tests_integration.test_portal_guardian import verified_authority
from tests_integration.test_portal_links import (athlete_record, consent, invite, linked, practitioner,
                                                 practitioner_id)
from tools.identity_review_db import EPHEMERAL_TARGET

# Each test process starts at a random point in a ~20-year window so repeated runs on
# the same database (flaky-test detection) do not collide on practitioner time ranges.
_hours = itertools.count(random.SystemRandom().randint(0, 175000))


def future_slot(p, amount=150000, minutes=50):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=next(_hours) + 48)
    r = p.post("/api/slots", json={"starts_at": start.isoformat(), "duration_minutes": minutes, "amount_paise": amount})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def admin(sql, params=None):
    engine = create_engine(EPHEMERAL_TARGET.admin_url())
    try:
        with engine.begin() as conn:
            r = conn.execute(text(sql), params or {})
            return r.fetchall() if r.returns_rows else None
    finally:
        engine.dispose()


def bookable(app, env, video=False):
    alice, record, a, person, link = linked(app, env)
    pid = practitioner_id(alice)
    for purpose in ("APPOINTMENT_BOOKING",) + (("VIDEO_CONSULTATION",) if video else ()):
        a.post("/api/portal/consents", json={"subject_person_id": person, "purpose": purpose,
                                             "practitioner_id": pid, "decision": "GRANTED"})
    return alice, a, person, link


def request(a, link, slot, **extra):
    return a.post("/api/portal/bookings", json={"link_id": link, "slot_id": slot, **extra})


def approve(alice, booking):
    r = alice.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    return r.json()["status"]


def pay(a, booking):
    order = a.post(f"/api/portal/bookings/{booking}/pay")
    assert order.status_code == 200, order.text
    checkout = a.post(f"/api/portal/bookings/{booking}/sandbox-checkout", json={"order_id": order.json()["order_id"]}).json()
    r = a.post(f"/api/portal/bookings/{booking}/verify-payment",
               json={"order_id": checkout["order_id"], "payment_id": checkout["payment_id"], "signature": checkout["signature"]})
    assert r.status_code == 200, r.text
    return r.json()["outcome"], checkout


def status_of(a, booking):
    return [b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == booking][0]["status"]


def confirmed(app, env):
    alice, a, person, link = bookable(app, env)
    slot = future_slot(alice)
    booking = request(a, link, slot).json()["booking_id"]
    approve(alice, booking)
    assert pay(a, booking)[0] == "CONFIRMED"
    return alice, a, person, link, slot, booking


def test_approval_first_happy_path_split_and_notifications(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    slot = future_slot(alice, amount=150000)
    assert slot in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]
    r = request(a, link, slot, note="Fictional pre-competition nerves")
    assert r.status_code == 201 and r.json()["status"] == "PENDING_APPROVAL"
    booking = r.json()["booking_id"]
    # Payment before approval is impossible.
    assert a.post(f"/api/portal/bookings/{booking}/pay").status_code == 409
    assert slot not in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]
    assert [s for s in alice.get("/api/slots").json() if s["id"] == slot][0]["status"] == "reserved"
    queue = {b["booking_id"]: b for b in alice.get("/api/booking-requests").json()}
    assert queue[booking]["status"] == "PENDING_APPROVAL" and queue[booking]["client_note"] == "Fictional pre-competition nerves"
    assert approve(alice, booking) == "APPROVED_AWAITING_PAYMENT"
    first_order = a.post(f"/api/portal/bookings/{booking}/pay").json()
    assert a.post(f"/api/portal/bookings/{booking}/pay").json()["order_id"] == first_order["order_id"]  # retry reuses order
    outcome, checkout = pay(a, booking)
    assert outcome == "CONFIRMED" and status_of(a, booking) == "CONFIRMED"
    # Replays of the same verified payment never double-confirm or double-allocate.
    again = a.post(f"/api/portal/bookings/{booking}/verify-payment", json={
        "order_id": checkout["order_id"], "payment_id": checkout["payment_id"], "signature": checkout["signature"]})
    assert again.json()["outcome"] == "ALREADY_RECORDED"
    view = [b for b in alice.get("/api/booking-requests").json() if b["booking_id"] == booking][0]
    assert (view["practitioner_paise"], view["platform_paise"], view["payment_status"]) == (105000, 45000, "CAPTURED")
    assert admin("SELECT count(*) FROM spps_identity_dev.platform_booking_payments WHERE booking_id=:b", {"b": booking})[0][0] == 1
    kinds = {n["kind"] for n in a.get("/api/portal/notifications").json()}
    assert {"booking.approved_payment_due", "booking.confirmed"} <= kinds
    assert {"booking.requested", "booking.confirmed"} <= {n["kind"] for n in alice.get("/api/practitioner/notifications").json()}
    a.get("/api/portal/notifications?mark_read=true")
    assert all(n["read"] for n in a.get("/api/portal/notifications").json())


def test_forged_signature_and_amount_are_rejected(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    booking = request(a, link, future_slot(alice)).json()["booking_id"]
    approve(alice, booking)
    order = a.post(f"/api/portal/bookings/{booking}/pay").json()["order_id"]
    checkout = a.post(f"/api/portal/bookings/{booking}/sandbox-checkout", json={"order_id": order}).json()
    bad = a.post(f"/api/portal/bookings/{booking}/verify-payment",
                 json={"order_id": order, "payment_id": checkout["payment_id"], "signature": "0" * 64})
    assert bad.status_code == 403 and status_of(a, booking) == "APPROVED_AWAITING_PAYMENT"
    sandbox = SandboxProvider(integration_env["SPPS_SANDBOX_PAYMENT_SECRET"])
    body = json.dumps({"event": "payment.captured", "payment": {"order_id": order, "payment_id": "sbx_pay_cheap",
                                                                "amount_paise": 100}}).encode()
    with client(app) as hook:
        r = hook.post("/api/payments/webhook/sandbox", content=body, headers={"x-spps-signature": sandbox.webhook_signature(body)})
        assert r.status_code == 409  # amount mismatch
        assert hook.post("/api/payments/webhook/sandbox", content=body, headers={"x-spps-signature": "f" * 64}).status_code == 403
    assert status_of(a, booking) == "APPROVED_AWAITING_PAYMENT"


def test_consent_gates_booking_and_video(app, integration_env):
    alice, record, a, person, link = linked(app, integration_env)
    slot = future_slot(alice)
    assert request(a, link, slot).status_code == 409
    consent_body = {"subject_person_id": person, "practitioner_id": practitioner_id(alice), "decision": "GRANTED"}
    a.post("/api/portal/consents", json={**consent_body, "purpose": "APPOINTMENT_BOOKING"})
    assert request(a, link, slot, video_requested=True).status_code == 409
    a.post("/api/portal/consents", json={**consent_body, "purpose": "VIDEO_CONSULTATION"})
    assert request(a, link, slot, video_requested=True).status_code == 201


def test_simultaneous_claims_only_one_succeeds(app, integration_env):
    alice, a1, _, link1 = bookable(app, integration_env)
    # Second athlete linked to the same practitioner.
    record2 = athlete_record(alice)
    code = invite(alice, record2)
    a2, person2 = verified_login(app, integration_env)[0], None
    person2 = a2.get("/api/portal/auth/me").json()["person_id"]
    pid = practitioner_id(alice)
    for purpose in ("CASE_LINK", "APPOINTMENT_BOOKING"):
        a2.post("/api/portal/consents", json={"subject_person_id": person2, "purpose": purpose,
                                              "practitioner_id": pid, "decision": "GRANTED"})
    link2 = a2.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": person2}).json()["link_id"]
    alice.post(f"/api/portal-links/{link2}/decision", json={"decision": "approve"})
    slot = future_slot(alice)
    barrier, results = threading.Barrier(2), {}

    def claim(name, c, link):
        barrier.wait()
        results[name] = request(c, link, slot).status_code

    threads = [threading.Thread(target=claim, args=("a1", a1, link1)), threading.Thread(target=claim, args=("a2", a2, link2))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results.values()) == [201, 409], results
    active = admin("SELECT count(*) FROM spps_identity_dev.platform_bookings WHERE slot_id=:s AND practitioner_id=:p "
                   "AND status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED')", {"s": slot, "p": pid})
    assert active[0][0] == 1


def test_database_rejects_overlapping_hold_even_without_api_checks(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    booking = request(a, link, future_slot(alice)).json()["booking_id"]
    row = admin("SELECT practitioner_id, tenant_id, starts_at, ends_at, person_id, link_id, requested_by_account_id "
                "FROM spps_identity_dev.platform_bookings WHERE id=:b", {"b": booking})[0]
    try:
        admin("INSERT INTO spps_identity_dev.platform_bookings(practitioner_id,tenant_id,slot_id,starts_at,ends_at,"
              "amount_paise,person_id,link_id,requested_by_account_id,status) VALUES (:p,:t,999999,:s + interval '10 minutes',"
              ":e,10000,:per,:l,:a,'PENDING_APPROVAL')",
              {"p": row[0], "t": row[1], "s": row[2], "e": row[3], "per": 424242, "l": row[5], "a": row[6]})
        raise AssertionError("overlapping practitioner hold was accepted")
    except Exception as exc:  # noqa: BLE001
        assert "ex_booking_practitioner_time" in str(exc)


def test_decline_before_payment_has_no_refund_path(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    slot = future_slot(alice)
    booking = request(a, link, slot).json()["booking_id"]
    r = alice.post(f"/api/booking-requests/{booking}/decision", json={"decision": "decline", "reason": "Fictional clash"})
    assert r.json()["status"] == "DECLINED"
    assert a.post(f"/api/portal/bookings/{booking}/pay").status_code == 409
    assert admin("SELECT count(*) FROM spps_identity_dev.platform_booking_payments WHERE booking_id=:b", {"b": booking})[0][0] == 0
    assert slot in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]  # released


def test_expired_hold_releases_slot_and_late_capture_is_refunded(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    slot = future_slot(alice)
    booking = request(a, link, slot).json()["booking_id"]
    approve(alice, booking)
    order = a.post(f"/api/portal/bookings/{booking}/pay").json()["order_id"]
    admin("UPDATE spps_identity_dev.platform_bookings SET action_deadline=now()-interval '1 minute' WHERE id=:b", {"b": booking})
    assert status_of(a, booking) == "EXPIRED"
    assert a.post(f"/api/portal/bookings/{booking}/pay").status_code == 409
    assert slot in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]
    # The payer completed checkout just before expiry; the provider webhook arrives late.
    sandbox = SandboxProvider(integration_env["SPPS_SANDBOX_PAYMENT_SECRET"])
    body = json.dumps({"event": "payment.captured", "payment": {"order_id": order, "payment_id": "sbx_pay_late" + uuid4().hex[:12],
                                                                "amount_paise": 150000}}).encode()
    with client(app) as hook:
        r = hook.post("/api/payments/webhook/sandbox", content=body, headers={"x-spps-signature": sandbox.webhook_signature(body)})
        assert r.json()["outcome"] == "REFUND_REQUIRED"
        replay = hook.post("/api/payments/webhook/sandbox", content=body, headers={"x-spps-signature": sandbox.webhook_signature(body)})
        assert replay.json()["outcome"] == "ALREADY_RECORDED"
    assert status_of(a, booking) == "REFUNDED"
    pay_row = admin("SELECT status, refund_id FROM spps_identity_dev.platform_booking_payments WHERE booking_id=:b", {"b": booking})
    assert pay_row[0][0] == "REFUNDED" and pay_row[0][1].startswith("sbx_rfnd_")


def test_pending_request_expires_without_approval(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    booking = request(a, link, future_slot(alice)).json()["booking_id"]
    admin("UPDATE spps_identity_dev.platform_bookings SET action_deadline=now()-interval '1 second' WHERE id=:b", {"b": booking})
    r = alice.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"})
    assert r.status_code == 409 and status_of(a, booking) == "EXPIRED"


def test_cancellations_and_refunds(app, integration_env):
    alice, a, person, link, slot, booking = confirmed(app, integration_env)
    r = a.post(f"/api/portal/bookings/{booking}/cancel", json={"reason": "Fictional injury"})
    assert r.json()["status"] == "REFUND_PENDING"
    assert status_of(a, booking) == "REFUNDED"  # sandbox refund processed immediately
    assert slot in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]
    alice2, a2, _, link2, _, booking2 = confirmed(app, integration_env)
    assert alice2.post(f"/api/booking-requests/{booking2}/cancel", json={"reason": ""}).status_code == 422
    assert alice2.post(f"/api/booking-requests/{booking2}/cancel",
                       json={"reason": "Fictional practitioner illness"}).json()["status"] == "REFUND_PENDING"
    assert status_of(a2, booking2) == "REFUNDED"
    assert "booking.refunded" in {n["kind"] for n in a2.get("/api/portal/notifications").json()}
    # Unpaid cancellation simply cancels.
    alice3, a3, _, link3 = bookable(app, integration_env)
    b3 = request(a3, link3, future_slot(alice3)).json()["booking_id"]
    assert a3.post(f"/api/portal/bookings/{b3}/cancel", json={}).json()["status"] == "CANCELLED"


def test_reschedule_confirmed_booking_moves_payment(app, integration_env):
    alice, a, person, link, old_slot, booking = confirmed(app, integration_env)
    new_slot = future_slot(alice, amount=999900)
    proposal = alice.post(f"/api/booking-requests/{booking}/reschedule", json={"slot_id": new_slot})
    assert proposal.status_code == 201
    pid = proposal.json()["proposal_booking_id"]
    assert alice.post(f"/api/booking-requests/{booking}/reschedule", json={"slot_id": future_slot(alice)}).status_code == 409
    assert status_of(a, pid) == "RESCHEDULE_PROPOSED"
    assert a.post(f"/api/portal/bookings/{pid}/reschedule-response", json={"accept": True}).json()["status"] == "CONFIRMED"
    assert status_of(a, booking) == "CANCELLED" and status_of(a, pid) == "CONFIRMED"
    moved = admin("SELECT booking_id, amount_paise FROM spps_identity_dev.platform_booking_payments WHERE booking_id IN (:o,:n)",
                  {"o": booking, "n": pid})
    assert moved == [(pid, 150000)]  # paid amount kept, not the new slot price
    assert old_slot in [s["slot_id"] for s in a.get(f"/api/portal/links/{link}/slots").json()]


def test_reschedule_declined_keeps_original(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    booking = request(a, link, future_slot(alice)).json()["booking_id"]
    pid = alice.post(f"/api/booking-requests/{booking}/reschedule", json={"slot_id": future_slot(alice)}).json()["proposal_booking_id"]
    assert a.post(f"/api/portal/bookings/{pid}/reschedule-response", json={"accept": False}).json()["status"] == "DECLINED"
    assert status_of(a, booking) == "PENDING_APPROVAL" and status_of(a, pid) == "CANCELLED"


def test_booking_isolation_between_practitioners_and_clients(app, integration_env):
    alice, a, person, link, slot, booking = confirmed(app, integration_env)
    bob = practitioner(app, integration_env, "bob")
    assert booking not in [b["booking_id"] for b in bob.get("/api/booking-requests").json()]
    for path, body in (("decision", {"decision": "decline"}), ("cancel", {"reason": "Not my booking"}),
                       ("complete", None), ("reschedule", {"slot_id": slot})):
        assert bob.post(f"/api/booking-requests/{booking}/{path}", json=body).status_code in (404, 409)
    other, _ = verified_login(app, integration_env)
    assert booking not in [b["booking_id"] for b in other.get("/api/portal/bookings").json()]
    assert other.post(f"/api/portal/bookings/{booking}/cancel", json={}).status_code == 404
    assert other.post(f"/api/portal/bookings/{booking}/pay").status_code == 404
    assert other.get(f"/api/portal/links/{link}/slots").status_code == 404
    assert other.post("/api/portal/bookings", json={"link_id": link, "slot_id": slot}).status_code == 404
    assert status_of(a, booking) == "CONFIRMED"


def test_revoked_link_blocks_new_bookings_and_payment(app, integration_env):
    alice, a, person, link = bookable(app, integration_env)
    booking = request(a, link, future_slot(alice)).json()["booking_id"]
    approve(alice, booking)
    a.post(f"/api/portal/links/{link}/revoke")
    assert a.post(f"/api/portal/bookings/{booking}/pay").status_code == 404
    assert a.get(f"/api/portal/links/{link}/slots").status_code == 404
    assert request(a, link, future_slot(alice)).status_code == 404


def test_guardian_scopes_view_versus_manage(app, integration_env):
    g, dependant, aid, rev = verified_authority(app, integration_env,
                                                scopes=["consent_management", "appointment_metadata", "booking_management"])
    alice = practitioner(app, integration_env)
    pid = practitioner_id(alice)
    code = invite(alice, athlete_record(alice))
    for purpose in ("CASE_LINK", "APPOINTMENT_BOOKING"):
        g.post("/api/portal/consents", json={"subject_person_id": dependant, "purpose": purpose,
                                             "practitioner_id": pid, "decision": "GRANTED"})
    link = g.post("/api/portal/links/redeem", json={"code": code, "subject_person_id": dependant}).json()["link_id"]
    alice.post(f"/api/portal-links/{link}/decision", json={"decision": "approve"})
    booking = request(g, link, future_slot(alice)).json()["booking_id"]
    view = [b for b in alice.get("/api/booking-requests").json() if b["booking_id"] == booking][0]
    assert view["via_guardian"] is True and view["subject_display_name"] == "Fictional Dependant"
    # A guardian with appointment_metadata only can see but not manage.
    g2, dep2, aid2, _ = verified_authority(app, integration_env, scopes=["appointment_metadata", "consent_management"])
    code2 = invite(alice, athlete_record(alice))
    for purpose in ("CASE_LINK", "APPOINTMENT_BOOKING"):
        g2.post("/api/portal/consents", json={"subject_person_id": dep2, "purpose": purpose,
                                              "practitioner_id": pid, "decision": "GRANTED"})
    link2 = g2.post("/api/portal/links/redeem", json={"code": code2, "subject_person_id": dep2}).json()["link_id"]
    alice.post(f"/api/portal-links/{link2}/decision", json={"decision": "approve"})
    assert request(g2, link2, future_slot(alice)).status_code == 404
    # Revoking authority immediately blocks the guardian's bookings view and actions.
    rev.post(f"/api/portal/review/authorities/{aid}/revoke", json={"reason": "Fictional authority ended"})
    assert booking not in [b["booking_id"] for b in g.get("/api/portal/bookings").json()]
    assert g.post(f"/api/portal/bookings/{booking}/cancel", json={}).status_code == 404


def test_completion_only_after_start(app, integration_env):
    alice, a, person, link, slot, booking = confirmed(app, integration_env)
    assert alice.post(f"/api/booking-requests/{booking}/complete").status_code == 409
    # Move into a unique past window (repeat runs share the database and history).
    hours = random.SystemRandom().randint(24, 175000)
    admin("UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-make_interval(hours=>:h), "
          "ends_at=now()-make_interval(hours=>:h)+interval '50 minutes' WHERE id=:b", {"b": booking, "h": hours})
    assert alice.post(f"/api/booking-requests/{booking}/complete").json()["status"] == "COMPLETED"
    assert a.post(f"/api/portal/bookings/{booking}/cancel", json={}).status_code == 409


def test_clinical_rls_slot_insert_and_withdraw_are_owner_only(app, integration_env):
    from types import SimpleNamespace

    import pytest
    from sqlalchemy.exc import DBAPIError

    from server.identity import bind_identity
    from tests_integration.test_shared_backend import identities
    alice, bob = identities(app)
    bob_client = practitioner(app, integration_env, "bob")
    bob_slot = future_slot(bob_client)
    with app.state.DB() as db:
        bind_identity(db, alice)
        with pytest.raises(DBAPIError) as failure:
            db.execute(text("INSERT INTO appointment_slots(practitioner_id,starts_at,duration_minutes,amount_paise,currency,available) "
                            "VALUES (:p, now()+interval '5 days', 50, 10000, 'INR', true)"), {"p": bob.id})
        assert failure.value.orig.sqlstate == "42501"
    with app.state.DB() as db:
        bind_identity(db, alice)
        assert db.execute(text("UPDATE appointment_slots SET available=false WHERE id=:s"), {"s": bob_slot}).rowcount == 0
        db.commit()
    with app.state.DB() as db:
        bind_identity(db, SimpleNamespace(id=alice.id, tenant_id=alice.tenant_id))
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM appointment_slots WHERE practitioner_id=:p"), {"p": alice.id})
