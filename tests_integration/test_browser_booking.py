"""Browser acceptance: free, consented, approval-first booking across two portals.

No money or refunds: historical sandbox checkout flow is retained separately
in the legacy backend integration suite, not in the Beta v1.0 browser contract.
"""
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from tests_integration.test_browser_portal import register_and_sign_in, server  # noqa: F401
from tests_integration.test_browser_workflow import create_athlete_ui, sign_in

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def free_release(monkeypatch):
    monkeypatch.setenv("SPPS_BETA_V1_FREE", "true")
    monkeypatch.setenv("MODEL_C_PAYMENT_TEST_MODE", "false")


def test_free_booking_request_approve_and_cancel_in_browser(server, integration_env):  # noqa: F811
    url = server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        web = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        phone = browser.new_context(viewport={"width": 390, "height": 844},
                                    is_mobile=True, has_touch=True).new_page()
        errors = []
        for p in (web, phone):
            p.on("pageerror", lambda error: errors.append(str(error)))
            p.on("dialog", lambda d: d.accept("Fictional schedule change") if d.type == "prompt" else d.accept())

        sign_in(web, url, integration_env)
        create_athlete_ui(web, "Fictional Booking Athlete " + uuid4().hex[:6])
        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-invite"]').click()
        code = web.locator("[data-portal-code]").get_attribute("data-portal-code")
        assert code and len(code) >= 8
        web.locator('[data-action="modal-close"]').click()

        register_and_sign_in(phone, url, integration_env, "athlete", "Fictional Booking Phone")
        phone.locator('form[data-form="preview"] [name="code"]').fill(code)
        phone.locator('form[data-form="preview"] [type="submit"]').click()
        phone.locator('form[data-form="connect"] [name="consent"]').check()
        phone.locator('form[data-form="connect"] [type="submit"]').click()
        expect(phone.locator('[data-section="links"] tbody tr').first).to_contain_text("practitioner review")

        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-link-decision"][data-decision="approve"]').click()
        expect(web.locator('.dialog').locator('text=ACTIVE').first).to_be_visible()
        web.locator('[data-action="modal-close"]').click()

        # All practitioner-published appointment times are free in v1.
        before={int(item["id"]) for item in web.request.get(url + "/api/slots").json()}
        web.locator('[data-action="view"][data-view="overview"]:visible').first.click()
        web.locator('[data-action="client-bookings"]').click()
        start=(datetime.now()+timedelta(days=9)).replace(hour=10,minute=0,second=0,microsecond=0)
        form=web.locator('form[data-form="client-slot"]')
        form.locator('[name="starts_at"]').fill(start.strftime("%Y-%m-%dT%H:%M"))
        expect(form).to_contain_text('Free appointments')
        with web.expect_response(lambda r:r.url.endswith('/api/slots') and r.request.method=='POST') as created:
            form.locator('[type="submit"]').click()
        assert created.value.status==201
        slot_id=int(created.value.json()['id'])
        assert slot_id not in before
        assert next(s for s in web.request.get(url + '/api/slots').json()
                    if int(s['id'])==slot_id)['amount_paise']==0
        expect(web.locator('.toast, #toast')).to_contain_text('Availability added')
        web.locator('[data-action="modal-close"]').click()

        phone.reload()
        phone.locator('form[data-form="load-slots"] [type="submit"]').click()
        req=phone.locator('form[data-form="request-booking"]')
        option=req.locator(f'[name="slot_id"] option[value="{slot_id}"]')
        expect(option).to_have_count(1)
        expect(option).to_contain_text('Free appointment')
        req.locator('[name="slot_id"]').select_option(str(slot_id))
        req.locator('[name="note"]').fill("Fictional note for free browser booking")
        req.locator('[name="booking_consent"]').check()
        with phone.expect_response(lambda r:r.url.endswith('/api/portal/bookings')
                                   and r.request.method=='POST') as booked:
            req.locator('[type="submit"]').click()
        assert booked.value.status==201
        booking_id=int(booked.value.json()['booking_id'])
        assert booked.value.json()['status']=='PENDING_APPROVAL'
        row=phone.locator(f'[data-booking-id="{booking_id}"]')
        expect(row).to_contain_text('Waiting for practitioner approval')
        expect(row.locator('[data-action="pay-booking"]')).to_have_count(0)

        web.locator('[data-action="client-bookings"]').click()
        booking=web.locator(f'[data-booking-row="{booking_id}"]')
        expect(booking).to_contain_text('Fictional note for free browser booking')
        expect(booking).to_contain_text('Awaiting your approval')
        booking.locator('[data-op="approve"]').click()
        expect(web.locator(f'[data-booking-row="{booking_id}"]')).to_contain_text('Confirmed')
        web.locator('[data-action="modal-close"]').click()

        phone.reload()
        row=phone.locator(f'[data-booking-id="{booking_id}"]')
        expect(row).to_contain_text('Confirmed')
        expect(row.locator('[data-action="pay-booking"]')).to_have_count(0)
        # Raw POST lacks portal CSRF token: authorization rejects it before the
        # retired payment handler. The authenticated API test separately checks 410.
        assert phone.request.post(url+f'/api/portal/bookings/{booking_id}/pay').status==403
        expect(phone.locator('[data-notifications]')).to_contain_text('confirmed')
        phone.screenshot(path=str(ROOT / 'output' / 'portal-booking-mobile.png'),full_page=True)

        phone.locator(f'[data-booking-id="{booking_id}"] [data-action="cancel-booking"]').click()
        expect(phone.locator(f'[data-booking-id="{booking_id}"]')).to_contain_text('Cancelled')
        assert not errors, errors
        browser.close()
