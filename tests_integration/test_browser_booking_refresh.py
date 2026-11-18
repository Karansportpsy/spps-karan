"""SPPS-ANDROID-001 on PostgreSQL: an open Client bookings dialog and the athlete portal show the
other side's booking changes without reopening, using fictional ephemeral records only."""
import pytest
from playwright.sync_api import expect, sync_playwright

@pytest.fixture(autouse=True)
def free_release(monkeypatch):
    monkeypatch.setenv('SPPS_BETA_V1_FREE', 'true')
    monkeypatch.setenv('MODEL_C_PAYMENT_TEST_MODE', 'false')

from tests_integration.test_browser_portal import server  # noqa: F401  (fixture)
from tests_integration.test_browser_workflow import sign_in
from tests_integration.test_portal_bookings import approve, bookable, future_slot, request


def test_booking_changes_reach_open_dialog_and_portal(server, integration_env, app):  # noqa: F811
    url = server
    alice, a, person, link = bookable(app, integration_env)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        errors = []
        doc = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        doc.on("pageerror", lambda e: errors.append(str(e)))
        sign_in(doc, url, integration_env, "alice")
        doc.locator('[data-action="view"][data-view="overview"]:visible').first.click()
        doc.locator('[data-action="client-bookings"]:visible').first.click()
        expect(doc.locator('[data-live="client-bookings"]')).to_be_visible()
        doc.locator('form[data-form="client-slot"] [name="starts_at"]').fill("2034-03-20T10:30")
        doc.locator("#modal-heading").click()

        # 1. The client requests while the practitioner's dialog stays open (timer refresh, <= 15 s).
        booking = request(a, link, future_slot(alice, amount=0)).json()["booking_id"]
        row = doc.locator(f'[data-booking-row="{booking}"]')
        expect(row).to_contain_text("Awaiting your approval", timeout=20000)
        assert doc.locator('form[data-form="client-slot"] [name="starts_at"]').input_value() == "2034-03-20T10:30"

        # 2. The athlete portal, opened before approval, shows the approval when it regains focus.
        ctx = browser.new_context()
        ctx.add_cookies([{"name": k, "value": v, "url": url} for k, v in a.cookies.items()])
        portal = ctx.new_page()
        portal.on("pageerror", lambda e: errors.append(str(e)))
        portal.goto(url + "/portal")
        expect(portal.locator(f'[data-booking-id="{booking}"]')).to_contain_text("Waiting for practitioner approval")
        assert approve(alice, booking) == "CONFIRMED"
        portal.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(portal.locator(f'[data-booking-id="{booking}"]')).to_contain_text("Confirmed")
        # A direct POST without the portal CSRF token must be rejected before
        # the free-mode 410 payment route (validated by authenticated API tests).
        assert portal.request.post(url+f"/api/portal/bookings/{booking}/pay").status==403
        # ... and the practitioner's open dialog follows on focus without reopening.
        doc.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(row).to_contain_text("Confirmed")
        assert not errors, errors
        browser.close()
