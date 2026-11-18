"""Real Chromium, real web/ sources, mocked API (fictional data), served with the exact Content-Security-Policy
strings from server/main.py: a practitioner can register with the required profile photo, and the photo reaches
the API as a data URL. Guards against any client code path the policy blocks (the page then says "Invalid image").
Set SPPS_CHROMIUM to use a specific browser binary."""
import json
import mimetypes
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ORIGIN = "https://spps.test"
POLICIES = re.findall(r"\['Content-Security-Policy'\]=\"([^\"]+)\"", (ROOT / "server" / "main.py").read_text(encoding="utf-8"))


def test_policies_were_found():
    assert len(POLICIES) >= 2 and all("img-src" in p for p in POLICIES)


@pytest.mark.parametrize("policy", POLICIES)
def test_registration_with_photo_succeeds_under_the_served_policy(policy):
    posts, violations, errors = [], [], []

    def answer(route):
        req, url = route.request, route.request.url
        if not url.startswith(ORIGIN):
            return route.abort()
        path = url[len(ORIGIN):].split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/register": "index.html", "/studio": "index.html"}.get(path)
            file = WEB / (name or path.removeprefix("/assets/"))
            if not file.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=file.read_bytes(), headers={"Content-Security-Policy": policy},
                                 content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        if req.method != "GET":
            posts.append((path, json.loads(req.post_data or "{}")))
            return route.fulfill(status=201, body=json.dumps({"status": "pending_approval"}), content_type="application/json")
        data = {
            "/api/runtime": {"integration_development": False, "free_access": True, "portal": False, "clinical_parity": False},
            "/api/public/supabase-auth-config": {"enabled": False},
            "/api/public/services": {"services": []},
        }.get(path)
        if path == "/api/auth/me":
            return route.fulfill(status=401, body=json.dumps({"detail": "Not signed in"}), content_type="application/json")
        route.fulfill(body=json.dumps(data if data is not None else []), content_type="application/json")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("SPPS_CHROMIUM") or pw.chromium.executable_path)
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
        page.route("**/*", answer)
        page.goto(ORIGIN + "/register")
        form = page.locator("form[data-form=practitioner-register]")
        form.wait_for()
        form.locator("[name=name]").fill("Dr Fictional Applicant")
        form.locator("[name=email]").fill("applicant@example.com")
        form.locator("[name=password]").fill("Fictional-Only-2026")
        form.locator("[name=photo]").set_input_files(str(ROOT / "test_support" / "avatars" / "portrait_01.webp"))
        form.locator("[name=experience_years]").fill("6")
        form.locator("[name=qualifications]").fill("MSc Sport Psychology (fictional)")
        form.locator("[name=terms_accepted]").check()
        form.locator("button[type=submit]").click()
        page.wait_for_timeout(1500)
        toast = page.locator("body").inner_text()
        browser.close()

    assert violations == [], violations
    assert "Invalid image" not in toast
    sent = [body for path, body in posts if path == "/api/public/practitioner-registration"]
    assert len(sent) == 1, posts
    assert sent[0]["photo_base64"].startswith("data:image/") and len(sent[0]["photo_base64"]) > 200
    assert sent[0]["plan"] == "free" and sent[0]["terms_accepted"] is True
    assert errors == []
