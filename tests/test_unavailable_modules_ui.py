"""Real Chromium, real web/ sources, mocked API (fictional data).

The hosted free release answers 503 "outside the isolated development milestone" for modules that are switched off
(institutional quotes, client booking requests). The owner's workspace must not show a red error for the background
load of such a module, and a click that asks for one directly must say plainly that it is not available.
Set SPPS_CHROMIUM to use a specific browser binary."""
import json
import mimetypes
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / "web"
ORIGIN = "https://spps.test"
BLOCKED = {"detail": "This workflow is outside the isolated development milestone"}


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_quotes_with = None  # (status, body) to simulate a real fault instead of the milestone block

    def answer(self, route):
        req, url = route.request, route.request.url
        if not url.startswith(ORIGIN):
            return route.abort()
        path = url[len(ORIGIN):].split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/studio": "index.html"}.get(path)
            file = WEB / (name or path.removeprefix("/assets/"))
            if not file.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        self.calls.append((req.method, path))
        if path == "/api/admin/organisation-quotes":
            status, body = self.fail_quotes_with or (503, BLOCKED)
            return route.fulfill(status=status, body=json.dumps(body), content_type="application/json")
        if path == "/api/booking-requests":
            return route.fulfill(status=503, body=json.dumps(BLOCKED), content_type="application/json")
        data = {
            "/api/runtime": {"integration_development": False, "free_access": True, "portal": False, "clinical_parity": False},
            "/api/public/supabase-auth-config": {"enabled": False},
            "/api/auth/me": {"id": 1, "name": "Fictional Owner", "email": "owner@example.com", "csrf": "sp1_x",
                             "tier": "core", "is_admin": True},
            "/api/dashboard": {"stats": {}, "enquiries": []},
            "/api/account/tier": {"tier": "core"},
            "/api/account/lifecycle": BLOCKED,
            "/api/admin/practitioner-reviews": [],
            "/api/public/services": {"services": []},
            "/api/public/practitioners": [],
        }.get(path, [])
        status = 503 if path == "/api/account/lifecycle" else 200
        route.fulfill(status=status, body=json.dumps(data), content_type="application/json")


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


def _toast(page):
    return page.evaluate("()=>{const t=document.getElementById('toast');return t.classList.contains('visible')?t.textContent:''}")


def _open_workspace(page):
    page.goto(ORIGIN + "/studio")
    page.wait_for_selector("[data-action=view][data-view=athletes]")
    page.wait_for_timeout(400)


def test_owner_tab_clicks_back_to_back_show_no_error_for_switched_off_modules(page):
    _open_workspace(page)
    assert "organisation-quotes" in " ".join(p for _, p in page.backend.calls), "the owner load does ask for the module"
    assert _toast(page) == ""
    for view in ("athletes", "bookings", "settings", "athletes", "bookings"):
        page.locator(f"[data-action=view][data-view={view}]").first.click()
        page.wait_for_timeout(250)
        text = _toast(page)
        assert "503" not in text and "isolated development milestone" not in text and "Some modules are unavailable" not in text, (view, text)
    assert page.errors == []


def test_a_real_fault_is_still_reported(page):
    page.backend.fail_quotes_with = (500, {"detail": "Database timeout"})
    page.goto(ORIGIN + "/studio")
    page.wait_for_selector("[data-action=view][data-view=athletes]")
    page.wait_for_timeout(600)
    text = _toast(page)
    assert "Some modules are unavailable" in text and "Database timeout" in text and "HTTP 500" in text


def test_asking_for_a_switched_off_module_directly_says_so_plainly(page):
    _open_workspace(page)
    page.locator("[data-action=view][data-view=bookings]").first.click()
    page.wait_for_selector("[data-action=client-bookings]")
    page.wait_for_timeout(5600)  # let any earlier toast expire
    assert _toast(page) == ""
    page.locator("[data-action=client-bookings]").first.click()
    page.wait_for_timeout(400)
    assert ("GET", "/api/booking-requests") in page.backend.calls
    assert _toast(page) == "This feature is not available in this release."
