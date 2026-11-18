"""Phase 3 screens in real Chromium: spreadsheet import, note autosave and the encrypted offline draft."""
import base64
import json

import pytest
from playwright.sync_api import sync_playwright

from .browser_support import ROOT, MockSite, chromium, open_page

ATHLETE = {"id": 21, "code": "FIC-21", "display_name": "Fictional Athlete 21", "sport": "Hockey",
           "athlete_group": "senior", "active": True}
DETAIL = {"athlete": {**ATHLETE, "created_at": "2026-11-01", "service_consent_recorded": True},
          "cases": [{"id": 5, "title": "Fictional case", "category": "x", "goal": "", "status": "active",
                     "next_review": None, "created_at": "2026-11-01"}]}
KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def _site():
    s = MockSite({"/api/athletes": [ATHLETE], "/api/athletes/21": DETAIL, "/api/v2/note-templates": [
        {"id": 1, "name": "SOAP", "body": "S:\nO:\nA:\nP:"}], "/api/v2/offline-key": {"kid": "k1", "key": KEY}})
    s.post_answers["/api/v2/imports"] = {"id": 9, "columns": ["Athlete ID", "Name", "Sport", "Level"], "sample": [],
                                         "rows_total": 2, "suggested_mapping": {"code": "Athlete ID",
                                                                                "display_name": "Name", "sport": "Sport"},
                                         "targets": ["code", "display_name", "sport", "level"],
                                         "required": ["code", "display_name", "sport"], "levels": ["club"],
                                         "age_groups": ["18-24"]}
    s.post_answers["/api/v2/imports/9/preview"] = {"rows_total": 2, "valid_rows": 1, "can_commit": False,
                                                   "errors": [{"row": 3, "field": "level",
                                                               "message": "Unknown level"}], "preview": []}
    return s


def test_import_shows_every_problem_before_anything_is_saved(browser, tmp_path):
    site = _site()
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=settings]").click()
    page.locator("button[data-action='v2:import']").click()
    f = tmp_path / "squad.csv"
    f.write_text("Athlete ID,Name,Sport,Level\nA-1,Fictional One,Hockey,club\nA-2,Fictional Two,Hockey,galactic\n")
    page.locator("form[data-form='v2:import-upload'] [name=file]").set_input_files(str(f))
    page.locator("form[data-form='v2:import-upload'] button[type=submit]").click()
    page.wait_for_selector("form[data-form='v2:import-preview']")
    assert page.locator("select[name=code]").input_value() == "Athlete ID"
    page.locator("form[data-form='v2:import-preview'] button[type=submit]").click()
    page.wait_for_selector("text=nothing will be imported")
    assert "Row 3 · level · Unknown level" in page.locator(".dialog").inner_text()
    assert not any(p.endswith("/commit") for p, _ in site.posts)
    upload = next(b for p, b in site.posts if p == "/api/v2/imports")
    assert base64.b64decode(upload["content_base64"]).startswith(b"Athlete ID,")


def test_note_autosaves_and_keeps_an_encrypted_offline_copy(browser):
    site = _site()
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=athletes]").click()
    page.locator("button[data-action='v2:compose']").click()
    page.wait_for_selector("form[data-form='v2:compose']")
    # The server cannot be reached: draft saves fail at the network level.
    page.route("**/api/v2/note-drafts/**", lambda route: route.abort())
    page.locator("[data-v2-template]").select_option("1")
    page.locator("[data-v2-autosave]").fill("S: Fictional worry before the final.\nP: breathing.")
    page.wait_for_selector("text=saved encrypted in this browser", timeout=8000)
    stored = page.evaluate("localStorage.getItem('spps.v2.offline-drafts')")
    assert stored and "Fictional worry" not in stored and "breathing" not in stored
    entry = next(iter(json.loads(stored).values()))
    assert entry["kid"] == "k1" and entry["ct"] and entry["iv"]
    # Back online: the encrypted copy is decrypted with the server-held key and sent.
    page.unroute("**/api/v2/note-drafts/**")
    site.post_answers_put = True
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(1500)
    sent = [b for p, b in site.posts if p.startswith("/api/v2/note-drafts/")]
    assert sent and sent[-1]["content"].startswith("S: Fictional worry") and sent[-1]["case_id"] == 5
    assert page.evaluate("localStorage.getItem('spps.v2.offline-drafts')") == "{}"
