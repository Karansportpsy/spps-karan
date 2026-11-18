"""Phase 8 athlete screens in real Chromium: pending invitation accepted from the account (no e-mailed link),
report-share approval preview, account deletion form; find page sends from the signed-in account."""
import json

import pytest
from playwright.sync_api import sync_playwright

from .browser_support import ORIGIN, WEB, chromium

INV = {"id": 7, "kind": "athlete", "practitioner_name": "Dr Fictional Listed", "athlete_label": "Fictional",
       "is_minor": False, "scope": ["Check-ins you submit"], "expires_at": "2026-12-01", "guardian_sees": "nothing"}
SHARE = {"id": 3, "report_id": 1, "title": "Fictional coach update", "audience": "coach", "recipient_label": "Club coach",
         "status": "pending", "approver_role": "athlete", "decided_at": None, "expires_at": None, "created_at": "",
         "practitioner_name": "Dr Fictional Listed", "athlete_label": "Fictional",
         "content": {"audience_label": "Coach", "title": "Fictional coach update", "athlete": "Fictional",
                     "sections": [{"label": "Summary", "kind": "narrative", "text": "COACH-LEVEL TEXT"}]}}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def _site(page, posts, extra=None):
    def answer(route):
        req = route.request
        path = req.url[len(ORIGIN):].split("?")[0]
        if path in ("/athlete", "/find"):
            name = "athlete.html" if path == "/athlete" else "find.html"
            return route.fulfill(body=(WEB / name).read_bytes(), content_type="text/html")
        if path.startswith("/assets/"):
            f = WEB / path.removeprefix("/assets/")
            if not f.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=f.read_bytes(), content_type={".css": "text/css", ".js": "application/javascript"}.get(f.suffix, "image/png"))
        if req.method == "POST":
            posts.append((path, json.loads(req.post_data or "{}"), req.headers.get("x-csrf-token")))
            body = {"data": {"status": "active", "url": "https://x.example/s/tok", "expires_at": "2026-12-01",
                             "mailed": True, "message": "Sent from your account."}}
            return route.fulfill(body=json.dumps(body), content_type="application/json")
        data = {"/api/v2/portal/me": {"data": {"email": "fictional@example.org", "display_name": "Fictional", "role": "athlete"}},
                "/api/v2/portal/crisis-contacts": {"data": []},
                "/api/v2/portal/invitations": {"data": [INV]},
                "/api/v2/portal/enquiries": {"data": []},
                "/api/v2/portal/report-shares": {"data": [SHARE]},
                "/api/v2/portal/links": {"data": []},
                "/api/public/form-token": {"form_token": "1.n.s"},
                "/api/v2/public/directory": {"results": [{"practitioner_id": 5, "display_name": "Dr Fictional Listed",
                    "qualifications": "", "sports": [], "athlete_levels": [], "age_groups": [], "languages": [],
                    "delivery": "online", "city": "", "taking_new_clients": True, "badges": [], "photo_url": None}],
                    "notice": "Not a crisis service."}}
        data.update(extra or {})
        if path in data:
            return route.fulfill(body=json.dumps(data[path]), content_type="application/json")
        route.fulfill(status=404, body=json.dumps({"detail": "Not found"}), content_type="application/json")
    page.route("**/*", answer)


def test_athlete_accepts_a_pending_invitation_and_previews_a_report_share(browser):
    posts = []
    page = browser.new_page()
    _site(page, posts)
    page.add_init_script("sessionStorage.setItem('spps.loop.csrf','csrf-1')")
    page.goto(ORIGIN + "/athlete")
    page.wait_for_selector("text=Invitation from Dr Fictional Listed")
    assert "COACH-LEVEL TEXT" in page.content()
    page.locator("form[data-f=inv-accept] [name=consent]").check()
    page.locator("form[data-f=inv-accept] button").click()
    page.wait_for_timeout(300)
    assert posts[0][0] == "/api/v2/portal/invitations/7/accept" and posts[0][1]["consent"] is True and posts[0][2] == "csrf-1"
    page.locator("form[data-f=share-approve] [name=recipient_email]").fill("coach@example.org")
    page.locator("form[data-f=share-approve] button[type=submit]").click()
    page.wait_for_selector("text=Copy this link now")
    assert posts[-1][0] == "/api/v2/portal/report-shares/3/approve"
    assert page.locator("form[data-f=delete]").count() == 1 and page.locator("form[data-f=deactivate]").count() == 1


def test_find_page_has_no_switch_or_enquiry_flow(browser):
    posts = []
    page = browser.new_page()
    _site(page, posts)
    page.goto(ORIGIN + "/find?switch=11")
    page.wait_for_selector("text=Dr Fictional Listed")
    page.wait_for_timeout(300)
    assert page.locator("[data-enquire]").count() == 0 and page.locator("form[data-f=account-enquiry]").count() == 0
    assert page.locator("a:has-text('Book appointment')").count() == 1
    assert posts == []