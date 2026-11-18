"""Stage 13A.8 browser acceptance: athlete/guardian portal + practitioner portal-access panel.

Real uvicorn backend on the ephemeral PostgreSQL, independent browser contexts
(desktop practitioner, mobile-viewport athlete, guardian, reviewer). The mobile
viewport is a browser test, not physical Android validation.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from tests_integration.test_browser_workflow import create_athlete_ui, sign_in
from tools.identity_ops import grant_reviewer
from tools.identity_review_db import EPHEMERAL_TARGET

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Fictional-Browser-Pass-2026"


@pytest.fixture
def server(integration_env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log = (ROOT / "output" / "browser-portal-backend.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
                                "--port", str(port), "--no-access-log"], cwd=ROOT,
                               env={**os.environ, **integration_env}, stdout=log, stderr=log)
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError("Backend exited; inspect output/browser-portal-backend.log")
            try:
                if httpx.get(url + "/health/ready", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.2)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=20)
        log.close()


def latest_token(env, kind, email):
    path = Path(env["SPPS_PORTAL_DEV_OUTBOX"])
    for _ in range(50):
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
        hits = [r for r in rows if r["kind"] == kind and r["to"] == email]
        if hits:
            return hits[-1]["token"]
        time.sleep(.1)
    raise AssertionError(f"no {kind} message for {email}")


def register_and_sign_in(page, url, env, role, name):
    email = f"browser-{role}-{uuid4().hex[:10]}@example.invalid"
    page.goto(url + "/portal")
    page.locator('[data-action="view"][data-view="register"]').click()
    form = page.locator('form[data-form="register"]')
    form.locator('[name="role"]').select_option(role)
    form.locator('[name="display_name"]').fill(name)
    form.locator('[name="email"]').fill(email)
    form.locator('[name="password"]').fill(PASSWORD)
    form.locator('[name="adult_attested"]').check()
    form.locator('[name="terms_accepted"]').check()
    form.locator('[type="submit"]').click()
    expect(page.locator('form[data-form="verify"]')).to_be_visible()
    page.locator('form[data-form="verify"] [name="token"]').fill(latest_token(env, "verify-email", email))
    page.locator('form[data-form="verify"] [type="submit"]').click()
    expect(page.locator("[data-portal-message]")).to_contain_text("Email verified")
    form = page.locator('form[data-form="signin"]')
    form.locator('[name="email"]').fill(email)
    form.locator('[name="password"]').fill(PASSWORD)
    form.locator('[type="submit"]').click()
    expect(page.locator("[data-portal-user]")).to_have_text(name)
    return email


def test_portal_link_and_guardian_journeys_in_browser(server, integration_env):
    url = server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path)
        desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        guardian_ctx, reviewer_ctx = browser.new_context(), browser.new_context()
        pages = [c.new_page() for c in (desktop, mobile, guardian_ctx, reviewer_ctx)]
        web, phone, guardian, reviewer = pages
        errors = []
        for p in pages:
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.on("dialog", lambda d: d.accept())

        # Practitioner issues an invitation code from the athlete overview.
        sign_in(web, url, integration_env)
        aid = create_athlete_ui(web, "Fictional Portal Athlete " + uuid4().hex[:6])
        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-invite"]').click()
        code = web.locator("[data-portal-code]").get_attribute("data-portal-code")
        assert code and len(code) == 14
        web.locator('[data-action="modal-close"]').click()

        # Athlete on a phone-sized viewport registers, verifies, previews code, consents and connects.
        register_and_sign_in(phone, url, integration_env, "athlete", "Fictional Phone Athlete")
        phone.locator('form[data-form="preview"] [name="code"]').fill(code)
        phone.locator('form[data-form="preview"] [type="submit"]').click()
        expect(phone.locator("[data-preview-practitioner]")).to_contain_text("Fictional Alice")
        phone.locator('form[data-form="connect"] [name="consent"]').check()
        phone.locator('form[data-form="connect"] [type="submit"]').click()
        row = phone.locator('[data-section="links"] tbody tr').first
        expect(row).to_contain_text("practitioner review")

        # Practitioner approves; athlete sees Active after reload (persistence).
        web.locator('[data-action="portal-access"]').click()
        web.locator('[data-action="portal-link-decision"][data-decision="approve"]').click()
        expect(web.locator(".dialog").locator("text=ACTIVE").first).to_be_visible()
        web.locator('[data-action="modal-close"]').click()
        phone.reload()
        expect(phone.locator('[data-section="links"] tbody tr').first).to_contain_text("Active")
        expect(phone.locator('[data-section="links"] tbody tr').first).to_contain_text("Fictional Alice")
        phone.screenshot(path=str(ROOT / "output" / "portal-athlete-mobile.png"), full_page=True)

        # Athlete consent history shows the CASE_LINK grant.
        phone.locator('[data-action="toggle-account"]').click()
        phone.locator('[data-section="account"] [data-action="show-consents"]').click()
        expect(phone.locator('[data-section="consents"]')).to_contain_text("Link with practitioner")

        # Guardian adds a dependant and requests authority with evidence.
        register_and_sign_in(guardian, url, integration_env, "guardian", "Fictional Guardian")
        guardian.locator('form[data-form="dependant"] [name="display_name"]').fill("Fictional Junior")
        guardian.locator('form[data-form="dependant"] [type="submit"]').click()
        guardian.locator('[data-action="show-authority"]').click()
        guardian.locator('form[data-form="authority"] [name="evidence_reference"]').fill("Fictional birth record FX-9")
        guardian.locator('form[data-form="authority"] [type="submit"]').click()
        expect(guardian.locator('[data-section="dependants"] tbody tr').first).to_contain_text("pending verification")

        # Independent reviewer verifies via the portal review queue.
        reviewer_email = register_and_sign_in(reviewer, url, integration_env, "athlete", "Fictional Reviewer")
        grant_reviewer(EPHEMERAL_TARGET.admin_url(), reviewer_email)
        reviewer.reload()
        queue_row = reviewer.locator('[data-section="review"] tbody tr').filter(has_text="Fictional Junior")
        queue_row.locator('[name="reason"]').fill("Fictional document checked in person")
        queue_row.locator('button[value="VERIFIED"]').click()
        expect(reviewer.locator('[data-section="review"]').filter(has_text="Fictional Junior")).to_have_count(0)
        guardian.reload()
        expect(guardian.locator('[data-section="dependants"] tbody tr').first).to_contain_text("verified")
        guardian.screenshot(path=str(ROOT / "output" / "portal-guardian-desktop.png"), full_page=True)

        # Sign out ends the session; reload stays signed out.
        phone.locator('[data-action="logout"]').click()
        expect(phone.locator('form[data-form="signin"]')).to_be_visible()
        phone.reload()
        expect(phone.locator('form[data-form="signin"]')).to_be_visible()

        assert not errors, errors
        browser.close()
