"""Phase 4 trend chart in real Chromium: every dated point drawn, missing marked, no line across a gap."""
import pytest
from playwright.sync_api import sync_playwright

from .browser_support import MockSite, chromium, open_page

ATHLETE = {"id": 31, "code": "FIC-31", "display_name": "Fictional Athlete 31", "sport": "Rowing",
           "athlete_group": "senior", "active": True}
TREND = {"instrument": {"id": 2, "name": "Competitive State Anxiety Inventory-2", "short_name": "CSAI-2"},
         "dates": ["2026-01-10", "2026-03-02", "2026-09-15"], "interpolated": False,
         "series": [{"key": "self_confidence", "label": "Self-confidence", "min": 9, "max": 36, "bands": [],
                     "points": [{"date": "2026-01-10", "score": 20, "band": ""},
                                {"date": "2026-09-15", "score": 31, "band": ""}], "missing": ["2026-03-02"]},
                    {"key": "x", "label": "Consecutive", "min": 0, "max": 10, "bands": [],
                     "points": [{"date": "2026-01-10", "score": 2, "band": ""}, {"date": "2026-03-02", "score": 4,
                                                                                  "band": ""}], "missing": []}]}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def test_trend_marks_missing_and_never_joins_across_it(browser):
    site = MockSite({"/api/athletes": [ATHLETE], "/api/athletes/31": {"athlete": {**ATHLETE, "created_at": "2026-01-01",
                                                                                  "service_consent_recorded": True},
                                                                      "cases": []},
                     "/api/v2/instruments": [{"id": 2, "name": "Competitive State Anxiety Inventory-2",
                                              "short_name": "CSAI-2", "score_entry_only": True}],
                     "/api/v2/athletes/31/instrument-trends/2": TREND})
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=athletes]").click()
    page.locator("button[data-action='v2:instruments']").click()
    page.locator("button[data-action='v2:instrument-trend']").click()
    page.wait_for_selector("[data-v2-trend=self_confidence] svg")
    sc = page.locator("[data-v2-trend=self_confidence] svg")
    assert sc.locator("circle[fill='var(--teal)']").count() == 2
    assert sc.locator("line[stroke='var(--teal)']").count() == 0  # a missing date lies between the two points
    assert "missing" in sc.text_content()
    cons = page.locator("[data-v2-trend=x] svg")
    assert cons.locator("line[stroke='var(--teal)']").count() == 1
