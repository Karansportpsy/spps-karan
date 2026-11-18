"""Stage 13C browser acceptance: programme assignment, athlete check-ins, feedback, released summary."""
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from tests_integration.test_browser_portal import register_and_sign_in, server  # noqa: F401  (fixture)
from tests_integration.test_browser_workflow import create_athlete_ui, sign_in

ROOT = Path(__file__).resolve().parents[1]


def test_programme_assign_accept_checkin_feedback_summary(server, integration_env):  # noqa: F811
    url = server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        web = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        phone = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        errors = []
        for p in (web, phone):
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.on("dialog", lambda d: d.accept())
        sign_in(web, url, integration_env)
        create_athlete_ui(web, "Fictional Programme Athlete " + uuid4().hex[:6])
        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-invite"]').click()
        code = web.locator("[data-portal-code]").get_attribute("data-portal-code")
        web.locator('[data-action="modal-close"]').click()
        register_and_sign_in(phone, url, integration_env, "athlete", "Fictional Programme Phone")
        phone.locator('form[data-form="preview"] [name="code"]').fill(code)
        phone.locator('form[data-form="preview"] [type="submit"]').click()
        phone.locator('form[data-form="connect"] [name="consent"]').check()
        phone.locator('form[data-form="connect"] [type="submit"]').click()
        expect(phone.locator('[data-section="links"] tbody tr').first).to_contain_text("practitioner review")
        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-link-decision"][data-decision="approve"]').click()
        expect(web.locator(".dialog").locator("text=ACTIVE").first).to_be_visible()
        web.locator('[data-action="modal-close"]').click()

        # Practitioner drafts a programme with one task, releases a summary, then assigns.
        web.locator('[data-action="participation"]').click()
        web.locator('form[data-form="programme-new"] [name="title"]').fill("Fictional serve routine")
        web.locator('form[data-form="programme-new"] [name="goal"]').fill("Fictional: calmer first serve")
        web.locator('form[data-form="programme-new"] [type="submit"]').click()
        task_form = web.locator('form[data-form="task-new"]').first
        task_form.locator('[name="title"]').fill("Box breathing before serve")
        task_form.locator('[name="instructions"]').fill("Four counts in, hold, out, hold")
        task_form.locator('[type="submit"]').click()
        expect(web.locator("[data-task]")).to_contain_text("Box breathing before serve")
        release = web.locator('form[data-form="summary-release"]')
        release.locator('[name="title"]').fill("Fictional week one summary")
        release.locator('[name="body"]').fill("Fictional: routine is becoming automatic.")
        release.locator('[type="submit"]').click()
        expect(web.locator("[data-summary]")).to_contain_text("Fictional week one summary")
        web.locator('[data-action="programme-status"][data-status="ASSIGNED"]').click()
        expect(web.locator("[data-programme]").first).to_contain_text("assigned")
        web.locator('[data-action="modal-close"]').click()

        # Athlete accepts (with participation consent) and logs a check-in.
        phone.reload()
        prog = phone.locator("[data-portal-programme]").first
        expect(prog).to_contain_text("Fictional serve routine")
        expect(phone.locator("[data-portal-summary]")).to_contain_text("Fictional week one summary")
        prog.locator('form[data-form="programme-respond"] [name="consent"]').check()
        prog.locator('form[data-form="programme-respond"] button[value="accept"]').click()
        expect(phone.locator("[data-portal-programme]").first).to_contain_text("accepted")
        check = phone.locator('form[data-form="task-submit"]').first
        check.locator('[name="self_rating"]').fill("8")
        check.locator('[name="reflection"]').fill("Fictional: felt steady")
        check.locator('[type="submit"]').click()
        expect(phone.locator("[data-portal-task]").first).to_contain_text("1 check-in")

        # Practitioner sees the source-tagged self-report and sends feedback.
        web.locator('[data-action="participation"]').click()
        sub = web.locator("[data-submission]").first
        expect(sub).to_contain_text("athlete self-report")
        expect(sub).to_contain_text("self-rating 8/10")
        sub.locator('[name="feedback"]').fill("Fictional: well done, keep the count")
        sub.locator('[type="submit"]').click()
        expect(web.locator("[data-submission]").first).to_contain_text("Your feedback")
        web.locator('[data-action="modal-close"]').click()
        phone.reload()
        expect(phone.locator("[data-portal-task]").first).to_contain_text("Fictional: well done, keep the count")
        phone.screenshot(path=str(ROOT / "output" / "portal-programme-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
