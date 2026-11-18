"""Real Chromium, real web/ sources, mocked API (fictional data): the public directory pins and labels the owner,
falls back to a local portrait when a photo cannot load, and the owner's Admin Dashboard shows waiting counts,
phase labels and sends the decisions the server accepts. Set SPPS_CHROMIUM to use a specific browser binary."""
import json
import mimetypes
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / "web"
ORIGIN = "https://spps.test"
OWNER_PHOTO = "https://www.winmindperform.com/assets/images/dr-karanbir-singh-portrait.jpg"


def _queue():
    base = dict(status="pending_approval", email_verified=True, is_owner_account=False, self=False,
                more_info_requested=False, listed=False, qualifications="MSc Sport Psychology", phase="pending")
    return [
        {**base, "practitioner_id": 2, "name": "Dr Applicant One", "email": "one@example.com"},
        {**base, "practitioner_id": 3, "name": "Dr Applicant Two", "email": "two@example.com", "phase": "responded"},
        {**base, "practitioner_id": 4, "name": "Dr Waiting Three", "email": "three@example.com",
         "phase": "more_info", "more_info_requested": True},
        {**base, "practitioner_id": 5, "name": "Dr Approved Four", "email": "four@example.com",
         "status": "approved", "phase": "approved", "listed": True},
        {**base, "practitioner_id": 1, "name": "Owner", "email": "owner@example.com", "status": "approved",
         "phase": "approved", "is_owner_account": True, "self": True, "listed": True},
    ]


class Backend:
    def __init__(self):
        self.posts = []

    def answer(self, route):
        req, url = route.request, route.request.url
        if not url.startswith(ORIGIN):
            return route.abort()  # the owner photo hotlink and any CDN are unreachable on purpose
        path = url[len(ORIGIN):].split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/studio": "index.html"}.get(path)
            file = WEB / (name or path.removeprefix("/assets/"))
            if not file.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        if req.method != "GET":
            self.posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(body=json.dumps({"status": "approved", "notification_sent": False}), content_type="application/json")
        data = {
            "/api/runtime": {"integration_development": False, "free_access": True, "portal": False, "clinical_parity": False},
            "/api/public/supabase-auth-config": {"enabled": False},
            "/api/auth/me": {"id": 1, "name": "Fictional Owner", "email": "owner@example.com", "csrf": "sp1_x",
                             "tier": "core", "is_admin": True},
            "/api/dashboard": {"stats": {}, "enquiries": []},
            "/api/admin/practitioner-reviews": _queue(),
            "/api/public/services": {"services": []},
            "/api/public/practitioners": [
                {"id": 1, "name": "Dr Fictional Owner", "qualifications": "PhD", "experience_years": 12, "specialisms": "x",
                 "achievements": "x", "featured_owner": True, "credentials_verified": True, "photo_url": OWNER_PHOTO},
                {"id": 2, "name": "Dr Applicant One", "qualifications": "MSc", "experience_years": 5, "specialisms": "y",
                 "achievements": "y", "featured_owner": False, "credentials_verified": True,
                 "photo_url": "/api/public/practitioners/2/photo"}],
        }.get(path, [])
        route.fulfill(body=json.dumps(data), content_type="application/json")


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("SPPS_CHROMIUM") or pw.chromium.executable_path)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
        p = ctx.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.backend = Backend()
        p.route("**/*", p.backend.answer)
        yield p
        browser.close()


def test_directory_pins_the_owner_and_survives_a_missing_photo(page):
    page.goto(ORIGIN + "/")
    page.wait_for_selector(".v1-practitioner")
    cards = page.locator(".v1-practitioner")
    assert cards.count() == 2
    assert "Owner-managed listing" in cards.nth(0).inner_text()
    assert "Owner-managed listing" not in cards.nth(1).inner_text()
    page.wait_for_timeout(800)
    owner_img = cards.nth(0).locator("img")
    assert "winmindperform.com" not in owner_img.get_attribute("src")
    assert page.evaluate("()=>{const i=document.querySelector('.v1-practitioner img');return i.complete&&i.naturalWidth>0}")
    assert page.errors == []


def test_owner_admin_dashboard_counts_labels_and_decisions(page):
    page.goto(ORIGIN + "/studio")
    page.wait_for_selector(".spps-dashboard-tab")
    assert page.locator("button.spps-dashboard-tab", has_text="Practitioner Workspace").count() == 1
    tab = page.locator("button.spps-dashboard-tab", has_text="Admin Dashboard")
    assert "2 waiting" in tab.inner_text(), "new + replied count; the one awaiting the applicant's reply does not"
    tab.click()
    page.wait_for_selector("table.data-table")
    table = page.locator("table.data-table").inner_text()
    assert "Dr Applicant One" in table and "Dr Applicant Two" in table
    assert "Dr Approved Four" not in table and "Owner" not in table.replace("Owner-", "")
    for label in ("New application", "Applicant replied - ready to review", "Waiting for applicant reply"):
        assert label in table
    page.get_by_role("button", name="Request more info").first.click()
    note = page.locator("form[data-form=spps-admin-decision] textarea")
    assert note.get_attribute("required") is not None and note.get_attribute("minlength") == "5"
    note.fill("Please send your accreditation certificate")
    page.locator("form[data-form=spps-admin-decision] button[type=submit]").click()
    page.wait_for_timeout(500)
    path, body = page.backend.posts[-1]
    assert path.endswith("/approval") and body == {"decision": "request_more_info", "note": "Please send your accreditation certificate"}
    page.get_by_role("button", name="Approve", exact=True).first.click()
    page.locator("form[data-form=spps-admin-decision] button[type=submit]").click()
    page.wait_for_timeout(500)
    _, body = page.backend.posts[-1]
    assert body["decision"] == "approve" and len(body["note"]) >= 5, "the server rejects notes shorter than five characters"
    assert page.errors == []
