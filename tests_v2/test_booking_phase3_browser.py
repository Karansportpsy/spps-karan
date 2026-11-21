"""Phase 3 real-browser acceptance: tabs, decisions, local time, proposals and clock-only video changes."""
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import MockSite, ORIGIN, WEB, chromium

START = datetime(2030, 1, 15, 12, tzinfo=timezone.utc)


def booking(bid=1, status="CONFIRMED", **extra):
    return {"booking_id": bid, "slot_id": bid, "status": status,
            "starts_at": START.isoformat(), "ends_at": (START + timedelta(minutes=50)).isoformat(),
            "service_name": "Fictional consultation", "subject_display_name": "Fictional Athlete",
            "mode": "online", "video_requested": True, "can_manage": True, **extra}


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def open_frozen(browser, rows, path="/studio", role="athlete", width=1280):
    site = MockSite({"/api/booking-requests": rows, "/api/portal/bookings": rows,
                     "/api/portal/links": [], "/api/portal/notifications": [],
                     "/api/portal/dependants": [], "/api/slots": [],
                     "/api/portal/programmes": {"programmes": [], "summaries": []},
                     "/api/portal/auth/me": {"account_id": 8, "person_id": 9, "roles": [role],
                                             "display_name": "Fictional Person", "email": "person@example.invalid",
                                             "csrf": "fictional"}})
    site.runtime["portal"] = True
    page = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Kolkata").new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    def route(r):
        if r.request.url == ORIGIN + "/portal":
            return r.fulfill(body=(WEB / "portal.html").read_bytes(), content_type="text/html")
        return site.answer(r)

    page.route("**/*", route)
    now = START - timedelta(minutes=16)
    page.clock.install(time=now)
    page.clock.pause_at(now + timedelta(seconds=1))
    page.goto(ORIGIN + path)
    if path == "/studio":
        page.locator("button.nav-link[data-view=bookings]").click()
        expect(page.locator("[data-appointments]")).to_be_visible()
    else:
        expect(page.get_by_role("heading", name="My appointments")).to_be_visible()
    return page, site, errors


@pytest.mark.parametrize("path", ["/studio", "/portal"])
def test_video_join_appears_and_disappears_without_server_data_changes(browser, path):
    page, _, errors = open_frozen(browser, [booking()], path)
    if path == "/studio":
        page.get_by_role("tab", name="Approved").click()
    join = page.get_by_role("button", name="Join video", exact=True)
    expect(join).to_have_count(0)
    page.clock.run_for(60_000)  # start - 15 min + 1 sec
    expect(join).to_be_visible()
    page.clock.fast_forward(80 * 60_000)  # end + 15 min + 1 sec
    expect(join).to_have_count(0)
    assert not errors, errors
    page.context.close()


@pytest.mark.parametrize("extra", [{"video_requested": False}, {"status": "PENDING_APPROVAL"}, {"video_closed": True}])
def test_portal_does_not_offer_video_for_ineligible_booking(browser, extra):
    page, _, errors = open_frozen(browser, [booking(**extra)], "/portal")
    page.clock.run_for(2 * 60_000)
    expect(page.get_by_role("button", name="Join video")).to_have_count(0)
    assert not errors, errors
    page.context.close()


def test_tabs_minimum_data_local_time_and_fixed_decline_reason(browser):
    rows = [booking(status="PENDING_APPROVAL", first_request=True, client_note="PRIVATE NOTE", via_guardian=True),
            booking(2), booking(3, "DECLINED"),
            booking(4, ends_at=(START - timedelta(days=1)).isoformat(), status="COMPLETED")]
    page, site, errors = open_frozen(browser, rows)
    row = page.locator('[data-booking-row="1"]')
    expect(row).to_contain_text("Fictional consultation")
    expect(row).to_contain_text("17:30")  # UTC noon displayed in the viewer's zone
    expect(row).not_to_contain_text("PRIVATE NOTE")
    expect(row).not_to_contain_text("guardian")
    expect(row).not_to_contain_text("Online")
    row.get_by_label("Decline reason").select_option("Unavailable at this time")
    site.post_answers["/api/booking-requests/1/decision"] = {"status": "DECLINED"}
    row.get_by_role("button", name="Decline", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("Booking updated")
    assert ("/api/booking-requests/1/decision", {"decision": "decline", "reason": "Unavailable at this time"}) in site.posts
    for name, bid in [("Approved", 2), ("Declined", 3), ("Past", 4)]:
        page.get_by_role("tab", name=name).click()
        expect(page.locator(f'[data-booking-row="{bid}"]')).to_be_visible()
    expect(page.locator('[data-booking-row="4"] button')).to_have_count(0)
    assert not errors, errors
    page.context.close()


@pytest.mark.parametrize("role,accept", [("athlete", True), ("guardian", True), ("athlete", False)])
def test_portal_shows_proposed_time_and_sends_response(browser, role, accept):
    proposal = booking(status="RESCHEDULE_PROPOSED")
    page, site, errors = open_frozen(browser, [proposal], "/portal", role, width=390)
    row = page.locator('[data-booking-id="1"]')
    expect(row).to_contain_text("New time proposed")
    expect(row).to_contain_text("17:30")
    control = row.get_by_role("button", name="Accept new time" if accept else "Decline new time")
    box = control.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390  # no sideways scroll needed

    def respond(body):
        assert body == {"accept": accept}
        site.data["/api/portal/bookings"] = [dict(proposal, status="PENDING_APPROVAL" if accept else "CANCELLED")]
        return {"status": "PENDING_APPROVAL" if accept else "DECLINED"}

    site.post_answers["/api/portal/bookings/1/reschedule-response"] = respond
    control.click()
    expect(row).to_contain_text("Waiting for practitioner approval" if accept else "Cancelled")
    assert ("/api/portal/bookings/1/reschedule-response", {"accept": accept}) in site.posts
    assert not errors, errors
    page.context.close()
