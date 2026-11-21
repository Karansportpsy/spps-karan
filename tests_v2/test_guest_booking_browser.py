"""Guest booking in a real browser: the find page (service, time, details, "check your mail" pop-up), the e-mail
confirmation page and the create-account form. The API is a fictional mock."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import MockSite, ORIGIN, WEB, chromium

CARD = {"practitioner_id": 5, "display_name": "Dr Fictional Practitioner", "qualifications": "MSc (fictional)",
        "sports": ["archery"], "athlete_levels": ["national"], "age_groups": ["18+"], "languages": ["English"],
        "delivery": "both", "city": "", "taking_new_clients": True, "badges": [], "photo_url": None,
        "services": [{"service_id": 11, "name": "Focus session", "short_description": "Routine planning.",
                      "duration_minutes": 50, "mode": "online"},
                     {"service_id": 12, "name": "Intake", "short_description": "", "duration_minutes": 60,
                      "mode": "in_person"}]}
SLOTS = {"practitioner_id": 5, "service_id": 11, "slots": [
    {"slot_id": 31, "starts_at": "2031-01-12T10:00:00+00:00", "duration_minutes": 50},
    {"slot_id": 32, "starts_at": "2031-01-12T12:00:00+00:00", "duration_minutes": 50}]}


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def open_page(browser, path, extra=None, *, signed_out=False):
    site = MockSite({"/api/v2/public/directory": {"results": [CARD], "notice": "Not an emergency service."},
                     "/api/v2/public/directory/5/slots": SLOTS, "/api/public/form-token": {"form_token": "tok"},
                     **(extra or {})})
    site.runtime["v2_flags"] = {"SPPS_DIRECTORY_BOOKING_ENABLED": True}
    page = browser.new_context(viewport={"width": 1000, "height": 1000}).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(r):
        url = r.request.url
        bare = url.split("?")[0]
        if bare == ORIGIN + "/find":
            return r.fulfill(body=(WEB / "find.html").read_bytes(), content_type="text/html")
        if bare == ORIGIN + "/portal":
            return r.fulfill(body=(WEB / "portal.html").read_bytes(), content_type="text/html")
        if signed_out and bare == ORIGIN + "/api/portal/auth/me":
            return r.fulfill(status=401, body='{"detail":"Sign in required"}', content_type="application/json")
        return site.answer(r)

    page.route("**/*", route)
    page.goto(ORIGIN + path)
    return page, site, errors


def test_guest_books_without_an_account_and_sees_check_your_mail(browser):
    page, site, errors = open_page(browser, "/find")
    site.post_answers["/api/v2/public/guest-bookings"] = {"status": "accepted", "message": "Request submitted."}
    page.get_by_role("button", name="Book appointment").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Book with Dr Fictional Practitioner")
    dialog.get_by_role("button", name="Focus session").click()
    expect(dialog.locator("[data-slots] button")).to_have_count(2)
    dialog.locator("[data-slots] button").first.click()
    dialog.locator("input[name=name]").fill("Fictional Guest")
    dialog.locator("input[name=email]").fill("guest@example.invalid")
    dialog.locator("textarea[name=note]").fill("Weekday evenings suit me.")
    dialog.locator("input[name=adult]").check()
    dialog.locator("input[name=consent]").check()
    expect(dialog).to_contain_text("live video consultation")  # the online service names the video consent
    dialog.get_by_role("button", name="Request appointment").click()
    expect(page.get_by_role("dialog")).to_contain_text("Request submitted")
    expect(page.get_by_role("dialog")).to_contain_text("Check your mail")
    path, body = next(p for p in site.posts if p[0] == "/api/v2/public/guest-bookings")
    assert body["practitioner_id"] == 5 and body["service_id"] == 11 and body["slot_id"] == 31
    assert body["email"] == "guest@example.invalid" and body["adult_attested"] is True
    assert body["consent_accepted"] is True and body["form_token"] == "tok" and body["website"] == ""
    page.get_by_role("button", name="OK", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert not errors, errors


def test_the_in_person_service_does_not_ask_for_video_consent_and_escape_closes(browser):
    page, _, _ = open_page(browser, "/find", {"/api/v2/public/directory/5/slots": SLOTS})
    page.get_by_role("button", name="Book appointment").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Intake").click()
    dialog.locator("[data-slots] button").first.click()
    expect(dialog.locator("input[name=consent]")).to_be_visible()
    assert "video consultation" not in dialog.inner_text()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_adult_and_consent_boxes_are_required_before_anything_is_sent(browser):
    page, site, _ = open_page(browser, "/find")
    page.get_by_role("button", name="Book appointment").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Focus session").click()
    dialog.locator("[data-slots] button").first.click()
    dialog.locator("input[name=name]").fill("Fictional Guest")
    dialog.locator("input[name=email]").fill("guest@example.invalid")
    dialog.get_by_role("button", name="Request appointment").click()
    page.wait_for_timeout(300)
    assert not any(p[0].endswith("/guest-bookings") for p in site.posts)


def test_confirmation_page_needs_a_button_press_and_cleans_the_address(browser):
    page, site, errors = open_page(browser, "/find?confirm=" + "A" * 43)
    site.post_answers["/api/v2/public/guest-bookings/confirm"] = {
        "status": "submitted", "message": "Your request has been sent to the practitioner.", "notice": "Not an emergency."}
    page.wait_for_timeout(300)
    assert not any(p[0].endswith("/confirm") for p in site.posts)   # opening the link alone sends nothing
    page.get_by_role("button", name="Confirm my request").click()
    expect(page.locator("#confirm-card")).to_contain_text("Request sent")
    assert ("/api/v2/public/guest-bookings/confirm", {"token": "A" * 43}) in site.posts
    assert "confirm=" not in page.url
    assert not errors, errors


def test_claim_form_sets_a_password_and_points_to_sign_in(browser):
    page, site, errors = open_page(browser, "/portal?claim=" + "B" * 43, signed_out=True)
    site.post_answers["/api/v2/public/guest-claim"] = {"status": "claimed"}
    form = page.locator("form[data-form=claim]")
    expect(form).to_contain_text("Create your account")
    form.locator("input[name=new_password]").fill("Fictional-Claim-Pass-2026")
    form.get_by_role("button", name="Create my account").click()
    expect(page.locator("[data-portal-message]")).to_contain_text("Your account is ready")
    path, body = next(p for p in site.posts if p[0] == "/api/v2/public/guest-claim")
    assert body == {"token": "B" * 43, "password": "Fictional-Claim-Pass-2026"}
    assert "claim=" not in page.url
    assert not errors, errors
