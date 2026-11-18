"""Shared real-Chromium harness for v2 screens: real web/ files, a mocked API answering from a dict (fictional)."""
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ORIGIN = "https://spps.test"

PRACTITIONER_ME = {"id": 2, "name": "Dr Fictional Practitioner", "email": "fictional@example.org", "csrf": "sp1_x",
                   "tier": "free", "is_admin": False}


class MockSite:
    def __init__(self, data: dict, *, me=PRACTITIONER_ME, flags=None, ai=False):
        self.data = dict(data)
        self.posts: list[tuple[str, dict]] = []
        self.me = me
        self.runtime = {"integration_development": False, "free_access": True, "portal": False,
                        "clinical_parity": False, "ai": ai, "v2_flags": flags or {}}
        self.post_answers: dict = {}

    def answer(self, route):
        req, url = route.request, route.request.url
        if not url.startswith(ORIGIN):
            return route.abort()
        path = url[len(ORIGIN):].split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/studio": "index.html", "/register": "index.html"}.get(path)
            f = WEB / (name or path.removeprefix("/assets/"))
            if not f.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=f.read_bytes(), content_type=mimetypes.guess_type(f.name)[0] or "text/plain")
        if req.method != "GET":
            body = json.loads(req.post_data or "{}") if req.post_data else {}
            self.posts.append((path, body))
            ans = self.post_answers.get(path, {"ok": True})
            return route.fulfill(body=json.dumps(ans(body) if callable(ans) else ans), content_type="application/json")
        base = {"/api/runtime": self.runtime, "/api/public/supabase-auth-config": {"enabled": False},
                "/api/auth/me": self.me, "/api/dashboard": {"stats": {}, "enquiries": []},
                "/api/public/services": {"services": []}, "/api/v2/settings": {"ai_available": ai_flag(self)},
                "/api/v2/account/exports": []}
        value = self.data.get(path, base.get(path, []))
        route.fulfill(body=json.dumps(value), content_type="application/json")


def ai_flag(site):
    return bool(site.runtime.get("ai"))


def open_page(browser, site, path="/studio"):
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    page = context.new_page()
    page.route("**/*", site.answer)
    page.goto(ORIGIN + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    return page


def chromium(pw):
    return pw.chromium.launch(executable_path=os.environ.get("SPPS_CHROMIUM") or pw.chromium.executable_path)
