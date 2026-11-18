"""Phase 7 public page in real Chromium: results from the snapshot API, verified badges, enquiry with bot token."""
import json

import pytest
from playwright.sync_api import sync_playwright

from .browser_support import ORIGIN, WEB, chromium

RESULT = {"practitioner_id": 5, "display_name": "Dr Fictional Listed", "qualifications": "MSc (fictional)",
          "sports": ["Hockey"], "athlete_levels": ["club"], "age_groups": ["18-24"], "languages": ["English"],
          "delivery": "both", "city": "Patiala", "taking_new_clients": True, "about": "",
          "badges": [{"kind": "identity", "checked_on": "2026-11-01"}], "has_photo": False, "photo_url": None}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def test_find_page_lists_badges_and_offers_booking_not_an_enquiry(browser):
    posts, queries = [], []

    def answer(route):
        req = route.request
        path, _, query = req.url[len(ORIGIN):].partition("?")
        if path == "/find":
            return route.fulfill(body=(WEB / "find.html").read_bytes(), content_type="text/html")
        if path.startswith("/assets/"):
            f = WEB / path.removeprefix("/assets/")
            if not f.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=f.read_bytes(), content_type={".css": "text/css", ".js": "application/javascript"}.get(f.suffix, "image/png"))
        if path == "/api/public/form-token":
            return route.fulfill(body=json.dumps({"form_token": "1.n.s"}), content_type="application/json")
        if req.method == "POST":
            posts.append(json.loads(req.post_data))
            return route.fulfill(status=201, body=json.dumps({"accepted": True, "message": "Thank you. Not a crisis service."}),
                                 content_type="application/json")
        queries.append(query)
        route.fulfill(body=json.dumps({"results": [RESULT], "order": "alphabetical",
                                       "notice": "SPPS is not a crisis or emergency service."}), content_type="application/json")
    page = browser.new_page()
    page.route("**/*", answer)
    page.goto(ORIGIN + "/find")
    page.wait_for_selector("text=Dr Fictional Listed")
    assert "Verified identity" in page.locator("#results").inner_text()
    page.locator("[name=sport]").fill("Hockey")
    page.locator("form[data-f=filters] button").click()
    page.wait_for_timeout(300)
    assert "sport=Hockey" in queries[-1]
    assert page.locator("[data-enquire]").count() == 0 and page.locator("form[data-f=enquiry]").count() == 0
    link = page.locator("a:has-text('Book appointment')")
    assert link.count() == 1 and "/portal?practitioner=" in link.get_attribute("href")
    assert posts == [], "the find page must not post anything"
