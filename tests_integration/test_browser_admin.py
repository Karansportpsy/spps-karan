"""13D browser acceptance (PostgreSQL profile): admin credential review + institutional licence seat."""
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from tests_integration.test_browser_portal import server  # noqa: F401  (fixture)
from tests_integration.test_browser_workflow import sign_in

ROOT = Path(__file__).resolve().parents[1]


def test_admin_reviews_and_licences_in_browser(server, integration_env):  # noqa: F811
    url = server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept("Fictional registry check") if d.type == "prompt" else d.accept())
        sign_in(page, url, integration_env, "carol")
        page.locator('[data-action="view"][data-view="overview"]:visible').first.click()
        page.locator('[data-action="practitioner-reviews"]').click()
        carol_row = page.locator("[data-review]").filter(has_text="Fictional Carol")
        expect(carol_row).to_contain_text("another administrator must review")
        bob_row = page.locator("[data-review]").filter(has_text="Fictional Bob")
        if bob_row.locator('[data-decision="verify"]').count():
            bob_row.locator('[data-decision="verify"]').click()
        expect(page.locator("[data-review]").filter(has_text="Fictional Bob")).to_contain_text("verified")
        org = "Fictional Academy " + uuid4().hex[:6]
        form = page.locator('form[data-form="licence-new"]')
        form.locator('[name="organisation_name"]').fill(org)
        form.locator('[name="seats"]').fill("3")
        form.locator('[type="submit"]').click()
        licence = page.locator("[data-licence]").filter(has_text=org)
        expect(licence).to_contain_text("0/3 seats")
        licence.locator('select[name="practitioner_id"]').select_option(label="Fictional Alice")
        licence.locator('form[data-form="licence-seat"] [type="submit"]').click()
        licence = page.locator("[data-licence]").filter(has_text=org)
        expect(licence).to_contain_text("1/3 seats")
        expect(licence).to_contain_text("Fictional Alice")
        page.screenshot(path=str(ROOT / "output" / "admin-reviews-licences.png"), full_page=True)
        licence.locator('[data-action="licence-revoke"]').first.click()
        expect(page.locator("[data-licence]").filter(has_text=org)).to_contain_text("0/3 seats")
        assert not errors, errors
        browser.close()
