"""Phase 6 athlete screen in real Chromium: consent screen from an invite link, crisis notice on every screen."""
import json

import pytest
from playwright.sync_api import sync_playwright

from .browser_support import ORIGIN, WEB, chromium

CRISIS = {"notice": "SPPS is not a crisis or emergency service. If you are in danger or need urgent help, use the contacts below.",
          "crisis_contacts": [{"name": "Fictional Helpline", "phone": "0000", "hours": "24/7"}]}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def test_invite_opened_in_any_browser_shows_consent_scope_and_crisis_contacts(browser):
    posts = []

    def answer(route):
        url, req = route.request.url, route.request
        path = url[len(ORIGIN):].split("?")[0]
        if path == "/athlete":
            return route.fulfill(body=(WEB / "athlete.html").read_bytes(), content_type="text/html")
        if path.startswith("/assets/"):
            f = WEB / path.removeprefix("/assets/")
            return route.fulfill(body=f.read_bytes(), content_type="text/css" if f.suffix == ".css" else "application/javascript")
        if req.method != "GET":
            posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(body=json.dumps({**CRISIS, "data": {"status": "active"}}), content_type="application/json")
        data = {"/api/v2/portal/crisis-contacts": CRISIS,
                "/api/v2/portal/me": {**CRISIS, "data": {"email": "a@example.org", "display_name": "Fictional", "role": "athlete"}},
                "/api/v2/portal/links": {**CRISIS, "data": []}}.get(path)
        if path.startswith("/api/v2/portal/invites/"):
            data = {**CRISIS, "data": {"kind": "athlete", "email": "a@example.org", "practitioner_name": "Dr Fictional",
                                       "athlete_label": "Fictional", "is_minor": False,
                                       "scope": ["Your check-in answers and when a check-in was missed"],
                                       "guardian_sees": "participation"}}
        route.fulfill(body=json.dumps(data or {}), content_type="application/json")
    page = browser.new_page()
    page.route("**/*", answer)
    page.goto(ORIGIN + "/athlete?invite=" + "t" * 43)
    page.wait_for_selector("form[data-f=accept]")
    body = page.locator("body").inner_text()
    assert "Dr Fictional" in body and "check-in answers" in body
    assert "not a crisis or emergency service" in page.locator("#crisis").inner_text()
    assert "Fictional Helpline" in page.locator("#crisis").inner_text()
    page.locator("[name=consent]").check()
    page.locator("form[data-f=accept] button").click()
    page.wait_for_timeout(500)
    assert posts and posts[0][0].endswith("/accept") and posts[0][1]["consent"] is True
