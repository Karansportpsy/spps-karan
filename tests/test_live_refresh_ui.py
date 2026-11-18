"""SPPS-ANDROID-001: open Client bookings dialog and athlete portal refresh without a reload,
and never overwrite unsaved input. Real Chromium, real web/ sources, API answered by an in-test
mock (no server, no database, fictional data). The PostgreSQL end-to-end journey is in
tests_integration/test_browser_booking_refresh.py."""
import json
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

WEB = Path(__file__).resolve().parents[1] / "web"
ORIGIN = "https://spps.test"


def iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def booking(bid, status, **extra):
    return {"booking_id": bid, "starts_at": iso(60 * 24), "ends_at": iso(60 * 24 + 50), "status": status,
            "subject_display_name": f"Fictional Client {bid}", "via_guardian": False, "video_requested": False,
            "client_note": "", "paid": status == "CONFIRMED", "practitioner_paise": 135000,
            "payment_status": "captured" if status == "CONFIRMED" else "", "amount_paise": 150000,
            "can_manage": True, **extra}


class Backend:
    def __init__(self):
        self.requests = [booking(1, "PENDING_APPROVAL")]
        self.slots = [{"id": 7, "starts_at": iso(60 * 48), "status": "available"}]
        self.calls = []

    def answer(self, route):
        url = route.request.url[len(ORIGIN):]
        path = url.split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/studio": "index.html", "/portal": "portal.html"}.get(path)
            file = WEB / (name or path.removeprefix("/assets/"))
            if not file.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=file.read_bytes(),
                                 content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        self.calls.append(path)
        data = {
            # integration_development opens the studio directly for a signed-in fictional practitioner.
            "/api/runtime": {"integration_development": True, "clinical_parity": False, "portal": True},
            "/api/auth/me": {"id": 1, "name": "Fictional Alice", "email": "alice@spps.test", "csrf": "fictional",
                             "tier": "core"},
            "/api/dashboard": {},
            "/api/public/services": {"services": []},
            "/api/booking-requests": self.requests,
            "/api/slots": self.slots,
            "/api/portal/auth/me": {"account_id": 5, "person_id": 9, "display_name": "Fictional Athlete",
                                    "email": "athlete@spps.test", "roles": ["athlete"], "csrf": "fictional"},
            "/api/portal/bookings": self.requests,
            "/api/portal/programmes": {"programmes": [], "summaries": []},
            "/api/portal/links": [{"link_id": 3, "active": True, "practitioner_name": "Fictional Alice",
                                   "status": "ACTIVE"}],
        }.get(path, [])
        route.fulfill(body=json.dumps(data), content_type="application/json")


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        yield b
        b.close()


def page_for(browser, backend):
    page = browser.new_context(viewport={"width": 1200, "height": 900}).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(ORIGIN + "/**", backend.answer)
    return page, errors


def open_bookings(page):
    page.goto(ORIGIN + "/studio")
    page.locator('[data-action="client-bookings"]:visible').first.click()
    expect(page.locator('[data-live="client-bookings"]')).to_be_visible()


def refocus(page):
    page.evaluate("window.dispatchEvent(new Event('focus'))")


def test_open_dialog_shows_client_changes_and_keeps_typed_availability(browser):
    backend = Backend()
    page, errors = page_for(browser, backend)
    open_bookings(page)
    row1 = page.locator('[data-booking-row="1"]')
    expect(row1).to_contain_text("Awaiting your approval")
    form = page.locator('form[data-form="client-slot"]')
    form.locator('[name="starts_at"]').fill("2034-03-20T10:30")
    page.locator("#modal-heading").click()  # typed but not focused
    backend.requests = [booking(1, "CONFIRMED"), booking(2, "PENDING_APPROVAL")]
    backend.slots = []
    refocus(page)
    expect(row1).to_contain_text("Confirmed")
    expect(page.locator('[data-booking-row="2"]')).to_contain_text("Awaiting your approval")
    expect(page.locator("[data-live-open]")).to_have_text("0")
    assert form.locator('[name="starts_at"]').input_value() == "2034-03-20T10:30"
    assert not errors, errors


def test_chosen_reschedule_time_blocks_refresh_until_cleared(browser):
    backend = Backend()
    page, errors = page_for(browser, backend)
    open_bookings(page)
    select = page.locator('[data-booking-row="1"] select[name="reschedule_slot"]')
    select.select_option("7")
    page.locator("#modal-heading").click()
    backend.requests = [booking(1, "CONFIRMED")]
    refocus(page)
    page.wait_for_timeout(600)
    expect(page.locator('[data-booking-row="1"]')).to_contain_text("Awaiting your approval")
    assert select.input_value() == "7"  # not overwritten
    select.select_option("")
    page.locator("#modal-heading").click()
    refocus(page)
    expect(page.locator('[data-booking-row="1"]')).to_contain_text("Confirmed")
    assert not errors, errors


def test_open_dialog_refreshes_on_its_timer(browser):
    backend = Backend()
    page, errors = page_for(browser, backend)
    page.clock.install()
    open_bookings(page)
    backend.requests = [booking(1, "CONFIRMED")]
    page.clock.run_for(16000)
    expect(page.locator('[data-booking-row="1"]')).to_contain_text("Confirmed")
    assert not errors, errors


def test_athlete_portal_shows_approval_and_respects_unsaved_input(browser):
    backend = Backend()
    page, errors = page_for(browser, backend)
    page.goto(ORIGIN + "/portal")
    row = page.locator('[data-booking-id="1"]')
    expect(row).to_contain_text("Waiting for practitioner approval")
    page.locator('[data-action="toggle-account"]').click()
    page.locator('[name="current_password"]').fill("typed-but-unsaved")
    page.locator("h1").first.click()
    backend.requests = [booking(1, "APPROVED_AWAITING_PAYMENT")]
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(600)
    expect(row).to_contain_text("Waiting for practitioner approval")  # dirty form: no re-render
    assert page.locator('[name="current_password"]').input_value() == "typed-but-unsaved"
    page.locator('[name="current_password"]').fill("")
    page.locator("h1").first.click()
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator('[data-booking-id="1"]')).to_contain_text("Approved")
    expect(page.locator('[data-booking-id="1"]')).to_contain_text("Pay")
    assert not errors, errors


def test_join_video_appears_in_window_and_disappears_after_room_closed(browser):
    backend = Backend()
    joinable = dict(starts_at=iso(5), ends_at=iso(55), video_requested=True)
    backend.requests = [booking(1, "CONFIRMED", **joinable)]
    page, errors = page_for(browser, backend)
    open_bookings(page)
    row = page.locator('[data-booking-row="1"]')
    expect(row.locator('[data-op="video"]')).to_have_count(1)
    backend.requests = [booking(1, "CONFIRMED", video_closed=True, **joinable)]  # practitioner pressed End video
    refocus(page)
    expect(row.locator("[data-video-ended]")).to_have_text("Video ended")
    expect(row.locator('[data-op="video"]')).to_have_count(0)
    portal, perrors = page_for(browser, backend)
    portal.goto(ORIGIN + "/portal")
    prow = portal.locator('[data-booking-id="1"]')
    expect(prow.locator("[data-video-ended]")).to_have_text("Video ended")
    expect(prow.locator('[data-action="join-video"]')).to_have_count(0)
    assert not errors and not perrors, (errors, perrors)
