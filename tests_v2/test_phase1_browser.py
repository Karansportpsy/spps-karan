"""Phase 1 in a real browser: sign up in one browser (the Android WebView), confirm in another (Chrome).

Real Chromium and the real web/ sources; Supabase is the same fake library the v1 tests use and the API is mocked
with fictional data. Checks that the application text is stored on the server BEFORE sign-up, that the claim is
put into the Supabase account, and that a different browser only promotes and asks for the photo.
"""
import json

import pytest

from tests.test_confirmation_in_another_browser import (EMAIL, ROOT, Site, boot, browser, fill_form,  # noqa: F401
                                                       new_tab, open_link)

CLAIM = "fictional-claim-" + "c" * 30


class V2Site(Site):
    def __init__(self, promote):
        super().__init__()
        self.promote = promote

    def answer(self, route):
        req, url = route.request, route.request.url
        path = url.split("spps.test", 1)[-1].split("?")[0] if "spps.test" in url else ""
        if path == "/api/public/form-token":
            return route.fulfill(body=json.dumps({"form_token": "1.n.s", "min_fill_seconds": 0}),
                                 content_type="application/json")
        if req.method == "POST" and path == "/api/public/practitioner-applications/pending":
            self.posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(status=201, body=json.dumps({"claim": CLAIM, "state": "applied"}),
                                 content_type="application/json")
        if req.method == "POST" and path == "/api/public/practitioner-application/promote":
            self.posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(body=json.dumps(self.promote), content_type="application/json")
        if req.method == "POST" and path == "/api/public/practitioner-application/photo":
            self.posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(body=json.dumps({"photo_saved": True}), content_type="application/json")
        return super().answer(route)

    def paths(self):
        return [p for p, _ in self.posts]


def test_application_is_stored_on_the_server_before_sign_up(browser):  # noqa: F811
    site = V2Site({})
    _, page = new_tab(browser, site)
    boot(page, "/register")
    form = fill_form(page, password="Fictional-Only-2026")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=Confirm your email")
    pending = [b for p, b in site.posts if p == "/api/public/practitioner-applications/pending"]
    assert len(pending) == 1 and pending[0]["qualifications"] == "MSc Sport Psychology (fictional)"
    assert pending[0]["form_token"] == "1.n.s" and pending[0]["website"] == ""
    assert "password" not in json.dumps(pending[0]).lower()
    assert page.evaluate("window.__signupData")["spps_claim"] == CLAIM
    assert "already saved" in page.locator("body").inner_text()


def test_other_browser_promotes_and_only_asks_for_the_photo(browser):  # noqa: F811
    site = V2Site({"state": "under_review", "practitioner_id": 7, "has_application": True, "promoted": True,
                   "needs_photo": True})
    _, page = new_tab(browser, site)  # a different browser: nothing saved locally
    open_link(page, meta={"spps_claim": CLAIM})
    assert "/api/public/practitioner-application/promote" in site.paths()
    assert site.sent() == []  # the v1 form was not needed: no retyping
    page.wait_for_selector("[data-v2-photo]")
    page.locator("form[data-form=v2-application-photo] [name=photo]").set_input_files(
        str(ROOT / "test_support" / "avatars" / "portrait_01.webp"))
    page.locator("form[data-form=v2-application-photo] button[type=submit]").click()
    page.wait_for_timeout(1500)
    photo = [b for p, b in site.posts if p == "/api/public/practitioner-application/photo"]
    assert len(photo) == 1 and photo[0]["photo_base64"].startswith("data:image/")
    assert "Your request has been sent for approval" in page.locator("body").inner_text()


def test_other_browser_with_photo_already_present_goes_straight_to_sign_in(browser):  # noqa: F811
    site = V2Site({"state": "under_review", "practitioner_id": 7, "has_application": True, "promoted": False,
                   "needs_photo": False})
    _, page = new_tab(browser, site)
    open_link(page, meta={"spps_claim": CLAIM})
    assert "Your request has been sent for approval" in page.locator("body").inner_text()


def test_no_server_application_falls_back_to_the_v1_form(browser):  # noqa: F811
    site = V2Site({"state": "email_verified", "practitioner_id": None, "has_application": False, "promoted": False,
                   "needs_photo": False})
    _, page = new_tab(browser, site)
    open_link(page)
    assert "Your email is confirmed, but your application has not been sent yet" in page.locator("body").inner_text()
