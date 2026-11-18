"""Beta v1.0 approval-first free booking on disposable PostgreSQL only."""
from test_support.app import create_app
from tests_integration.test_portal_bookings import bookable, future_slot, request, status_of, admin


def test_free_booking_approval_without_payment(integration_env, monkeypatch):
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    application = create_app(init_database=False, seed_demo=False)
    try:
        alice, athlete, person, link = bookable(application, integration_env)
        slot = future_slot(alice, amount=0)
        result = request(athlete, link, slot, note="Fictional free consultation")
        assert result.status_code == 201, result.text
        booking = result.json()["booking_id"]
        assert result.json()["status"] == "PENDING_APPROVAL"
        assert athlete.post(f"/api/portal/bookings/{booking}/pay").status_code == 410
        assert athlete.post(f"/api/portal/bookings/{booking}/verify-payment",
                            json={"order_id": "fake123", "payment_id": "fake123",
                                  "signature": "not-valid-signature"}).status_code == 410
        decision = alice.post(f"/api/booking-requests/{booking}/decision",
                              json={"decision": "approve"})
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "CONFIRMED"
        assert status_of(athlete, booking) == "CONFIRMED"
        assert admin("SELECT count(*) FROM spps_identity_dev.platform_booking_payments "
                     "WHERE booking_id=:id", {"id": booking})[0][0] == 0
        cancelled = athlete.post(f"/api/portal/bookings/{booking}/cancel",
                                 json={"reason": "Fictional client cancellation"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
        assert status_of(athlete, booking) == "CANCELLED"
    finally:
        application.state.engine.dispose()
        application.state.auth_engine.dispose()
        for name in ("portal_auth_engine", "portal_engine"):
            engine = getattr(application.state, name, None)
            if engine is not None:
                engine.dispose()
