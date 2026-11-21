"""Guest booking: flag dependencies, preflight rules, the default-deny allowlist and the e-mail pieces."""
import pytest

from .test_phase10_notifications import MAIL, _env

CHAIN = {"SPPS_PORTAL_ENABLED": "true", "SPPS_DIRECTORY_ENABLED": "true", "SPPS_DIRECTORY_BOOKING_ENABLED": "true",
         "SPPS_NOTIFY_WORKER_ENABLED": "true", "SPPS_CRISIS_CONTACTS_TEXT": "Fictional helpline 0000 (24/7)."}


def test_flag_defaults_off_and_needs_the_booking_chain(monkeypatch):
    from server.v2 import flags
    for k in list(flags.FLAGS):
        monkeypatch.delenv(k, raising=False)
    assert flags.FLAGS["SPPS_GUEST_BOOKING_ENABLED"][0] == "false"
    monkeypatch.setenv("SPPS_GUEST_BOOKING_ENABLED", "true")
    assert not flags.guest_booking_enabled()
    for k, v in CHAIN.items():
        monkeypatch.setenv(k, v)
    assert flags.guest_booking_enabled()


def test_preflight_requires_the_worker_the_directory_booking_flag_mail_and_the_migration():
    from server.v2 import preflight
    assert "0047_v2_guest_booking.sql" in preflight.FLAG_MIGRATIONS["SPPS_GUEST_BOOKING_ENABLED"]
    assert "0047_v2_guest_booking.sql" in preflight.MIGRATION_OBJECTS
    preflight.validate_env(_env(SPPS_GUEST_BOOKING_ENABLED="true", **CHAIN, **MAIL))
    no_worker = {k: v for k, v in CHAIN.items() if k != "SPPS_NOTIFY_WORKER_ENABLED"}
    with pytest.raises(preflight.PreflightError, match="SPPS_NOTIFY_WORKER_ENABLED"):
        preflight.validate_env(_env(SPPS_GUEST_BOOKING_ENABLED="true", **no_worker, **MAIL))
    no_booking = {k: v for k, v in CHAIN.items() if k != "SPPS_DIRECTORY_BOOKING_ENABLED"}
    with pytest.raises(preflight.PreflightError, match="SPPS_DIRECTORY_BOOKING_ENABLED"):
        preflight.validate_env(_env(SPPS_GUEST_BOOKING_ENABLED="true", **no_booking, **MAIL))
    with pytest.raises(preflight.PreflightError, match="SPPS_SMTP_HOST"):
        preflight.validate_env(_env(SPPS_GUEST_BOOKING_ENABLED="true", **CHAIN))


def test_guest_routes_are_allowed_only_with_their_flag(monkeypatch):
    from server.v2 import flags
    from server.v2.allowlist import v2_allowed
    routes = [("GET", "/api/v2/public/directory/5/slots"), ("POST", "/api/v2/public/guest-bookings"),
              ("POST", "/api/v2/public/guest-bookings/confirm"), ("POST", "/api/v2/public/guest-claim")]
    for k in list(flags.FLAGS):
        monkeypatch.delenv(k, raising=False)
    for method, path in routes:
        assert v2_allowed(method, path) is False
    for k, v in CHAIN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SPPS_GUEST_BOOKING_ENABLED", "true")
    for method, path in routes:
        assert v2_allowed(method, path) is True
    assert v2_allowed("GET", "/api/v2/public/guest-bookings") is not True
    assert v2_allowed("DELETE", "/api/v2/public/guest-claim") is not True


def test_claim_and_confirmation_mails_carry_only_a_link():
    from server.notifications import render_claim
    subject, body = render_claim("https://app.example.org/portal", "tok_en-123")
    assert "create your account" in subject.lower()
    assert body.count("https://app.example.org/portal?claim=tok_en-123") == 1
    assert "Dr" not in body and "@" not in body
    from server.portal_auth import ProviderOutbox

    class Capture:
        sent = []

        def send(self, message):
            self.sent.append(message)

    cap = Capture()
    ProviderOutbox(cap, "https://app.example.org").send("guest-confirm", "someone@example.invalid", "abc_DEF-123")
    assert cap.sent[0].body.count("https://app.example.org/find?confirm=abc_DEF-123") == 1
    assert "appointment" in cap.sent[0].subject.lower()
