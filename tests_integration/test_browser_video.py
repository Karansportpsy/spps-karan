"""Phase 9 browser acceptance: two-party WebRTC call for a confirmed platform video booking.

Chromium fake camera/microphone on loopback (host ICE candidates, no STUN/TURN). This
proves ticketing, consent gating, signaling and peer connection in real browsers; it is
NOT a cross-network or physical-device test (TURN/NAT traversal remains a hosting gate).
"""
import random

from playwright.sync_api import expect, sync_playwright

from tests_integration.test_browser_portal import server  # noqa: F401  (fixture)
from tests_integration.test_portal_auth import client
from tests_integration.test_portal_bookings import admin, approve, bookable, future_slot, pay, request

FAKE_MEDIA = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]


def test_practitioner_and_athlete_connect_in_video_room(server, integration_env, app):  # noqa: F811
    url = server
    alice, a, person, link = bookable(app, integration_env, video=True)
    booking = request(a, link, future_slot(alice), video_requested=True).json()["booking_id"]
    approve(alice, booking)
    assert pay(a, booking)[0] == "CONFIRMED"
    admin("UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '5 minutes', "
          "ends_at=now()+interval '55 minutes' WHERE id=:b", {"b": booking})
    try:
        doc_url = alice.post(f"/api/booking-requests/{booking}/video-ticket").json()["room_url"]
        guest_url = a.post(f"/api/portal/bookings/{booking}/video-ticket").json()["room_url"]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=pw.chromium.executable_path, args=FAKE_MEDIA)
            pages = []
            for room in (doc_url, guest_url):
                ctx = browser.new_context(permissions=["camera", "microphone"])
                page = ctx.new_page()
                page.goto(url + room)
                expect(page.locator("#phase-invitation")).to_contain_text("Verified")
                assert "ticket=" not in page.url  # bearer removed from the address bar
                page.locator("#video-optin").check()
                page.locator("#accept-consent").click()
                expect(page.locator("#room-call")).to_be_visible()
                pages.append(page)
            for page in pages:
                page.locator("#start-camera").click()
            for page in pages:
                expect(page.locator("#phase-peer")).to_have_text("connected", timeout=30000)
                expect(page.locator("#phase-participant")).to_contain_text("Both connected")
            # Practitioner ends the session: tickets revoked; a reload cannot rejoin.
            alice.post(f"/api/booking-requests/{booking}/end-video")
            with client(app) as probe:
                assert probe.get(f"/api/video/{booking}/status?ticket={guest_url.split('ticket=')[1]}").status_code == 403
            browser.close()
    finally:
        hours = random.SystemRandom().randint(24, 175000)
        admin("UPDATE spps_identity_dev.platform_bookings SET starts_at=now()-make_interval(hours=>:h), "
              "ends_at=now()-make_interval(hours=>:h)+interval '50 minutes' WHERE id=:b", {"b": booking, "h": hours})
