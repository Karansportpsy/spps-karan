"""Phase 5 report editor in real Chromium: clinical-only fields are labelled and never marked as shared."""
import pytest
from playwright.sync_api import sync_playwright

from .browser_support import MockSite, chromium, open_page

ATHLETE = {"id": 41, "code": "FIC-41", "display_name": "Fictional Athlete 41", "sport": "Cycling",
           "athlete_group": "senior", "active": True}
REPORT = {"id": 7, "athlete_id": 41, "title": "Fictional update", "audience": "coach", "status": "draft",
          "revision": 1, "signed_at": None, "values": {},
          "fields": [{"key": "summary", "label": "Summary", "kind": "narrative", "audiences": ["coach"],
                      "included": True, "clinical_only": False},
                     {"key": "clinical", "label": "Formulation", "kind": "narrative", "audiences": [],
                      "included": False, "clinical_only": True}]}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def test_report_editor_marks_clinical_only_fields_and_signs(browser):
    site = MockSite({"/api/athletes": [ATHLETE], "/api/athletes/41": {"athlete": {**ATHLETE, "created_at": "2026-01-01",
                     "service_consent_recorded": True}, "cases": []},
                     "/api/v2/athletes/41/v2-reports": [{"id": 7, "title": "Fictional update", "audience": "coach",
                                                         "status": "draft", "signed_at": None}],
                     "/api/v2/report-templates": [{"id": 1, "name": "T", "fields": []}],
                     "/api/v2/athletes/41/report-consents": [], "/api/v2/v2-reports/7": REPORT})
    site.post_answers["/api/v2/v2-reports/7/sign"] = {**REPORT, "status": "signed"}
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=athletes]").click()
    page.locator("button[data-action='v2:reports']").click()
    page.locator("button[data-action='v2:report-open']").click()
    page.wait_for_selector("form[data-form='v2:report-save']")
    text = page.locator(".dialog").inner_text()
    assert "Clinical-only: never shared" in text and "Included" in text
    page.locator("textarea[name='f:summary']").fill("Fictional coach summary")
    page.locator("button[data-action='v2:report-sign']").click()
    page.wait_for_timeout(800)
    paths = [p for p, _ in site.posts]
    assert "/api/v2/v2-reports/7" in paths and "/api/v2/v2-reports/7/sign" in paths
    saved = next(b for p, b in site.posts if p == "/api/v2/v2-reports/7")
    assert saved["values"]["summary"] == "Fictional coach summary"
