"""Phase 2 screens in real Chromium: Your data, export, access log; no AI wording while the switch is off."""
import re

import pytest
from playwright.sync_api import sync_playwright

from .browser_support import MockSite, chromium, open_page

ATHLETE = {"id": 11, "code": "FIC-11", "display_name": "Fictional Athlete Eleven", "sport": "Squash",
           "athlete_group": "senior", "active": True}


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def _site(**kw):
    return MockSite({"/api/athletes": [ATHLETE],
                     "/api/athletes/11": {"athlete": {**ATHLETE, "created_at": "2026-11-01", "service_consent_recorded": True},
                                          "cases": []},
                     "/api/v2/athletes/11/access-log": [{"at": "2026-11-17T10:00:00Z", "action": "read",
                                                         "resource": "/api/athletes/{athlete_id}", "actor_id": 2,
                                                         "request_id": "r1"}]}, **kw)


def test_profile_and_privacy_shows_your_data_and_export_without_ai_wording(browser):
    site = _site()
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=settings]").click()
    page.wait_for_selector("[data-v2=export]")
    body = page.locator("body").inner_text()
    assert "Your data" in body and "Download all my data" in body
    assert page.locator("[data-v2=ai]").count() == 0
    assert not re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", body)
    assert page.locator("a[href='/your-data']").count() == 1


def test_ai_card_appears_only_when_the_global_switch_is_on(browser):
    site = _site(ai=True)
    site.data["/api/v2/settings"] = {"ai_available": True, "ai_opt_in": False, "ai_notice": "Optional drafts."}
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=settings]").click()
    page.wait_for_selector("[data-v2=ai]")


def test_athlete_access_log_dialog(browser):
    site = _site()
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=athletes]").click()
    page.wait_for_selector("text=Access log")
    page.locator("button[data-action='v2:access-log']").click()
    page.wait_for_selector(".dialog")
    text = page.locator(".dialog").inner_text()
    assert "never contains what the record says" in text and "read" in text
