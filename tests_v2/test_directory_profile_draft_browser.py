"""Directory profile form in real Chromium: unsaved text survives a redraw, and Submit saves the text first."""
import pytest
from playwright.sync_api import sync_playwright

from .browser_support import MockSite, chromium, open_page


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def _site():
    site = MockSite({"/api/athletes": [],
                     "/api/v2/directory/profile": {"display_name": "", "qualifications": "", "sports": "", "athlete_levels": "",
                                                   "age_groups": "", "languages": "", "delivery": "online", "city": "",
                                                   "taking_new_clients": True, "about": "", "status": "none",
                                                   "owner_note": "", "listed": False},
                     "/api/v2/practitioner-services": {"services": [], "listed": False, "profile_status": "none"}},
                    flags={"SPPS_DIRECTORY_ENABLED": True})
    site.post_answers["/api/v2/directory/profile/submit"] = {"status": "submitted"}
    return site


def _fill(page):
    page.fill("form[data-form='v2:dir-profile'] [name=display_name]", "Dr Fictional Listed")
    page.fill("form[data-form='v2:dir-profile'] [name=qualifications]", "MSc (fictional)")
    page.fill("form[data-form='v2:dir-profile'] [name=sports]", "squash, golf")
    page.select_option("form[data-form='v2:dir-profile'] [name=delivery]", "both")


def test_unsaved_profile_text_survives_a_redraw(browser):
    page = open_page(browser, _site())
    page.locator("button.nav-link[data-view=settings]").click()
    page.wait_for_selector("form[data-form='v2:dir-profile']")
    _fill(page)
    # Leave Settings and come back: the screen is rebuilt from the server copy, which is still empty.
    page.locator("button.nav-link[data-view=athletes]").click()
    page.locator("button.nav-link[data-view=settings]").click()
    page.wait_for_selector("form[data-form='v2:dir-profile']")
    f = "form[data-form='v2:dir-profile']"
    assert page.input_value(f + " [name=display_name]") == "Dr Fictional Listed"
    assert page.input_value(f + " [name=sports]") == "squash, golf"
    assert page.input_value(f + " [name=delivery]") == "both"
    assert page.input_value(f + " [name=qualifications]") == "MSc (fictional)"


def test_submit_saves_the_text_before_submitting(browser):
    site = _site()
    page = open_page(browser, site)
    page.locator("button.nav-link[data-view=settings]").click()
    page.wait_for_selector("form[data-form='v2:dir-profile']")
    _fill(page)
    page.locator("form[data-form='v2:dir-profile'] button", has_text="Submit for publication").click()
    page.wait_for_timeout(500)
    paths = [p for p, _ in site.posts]
    assert "/api/v2/directory/profile/submit" in paths
    saved = [b for p, b in site.posts if p == "/api/v2/directory/profile"]
    assert saved and saved[0]["display_name"] == "Dr Fictional Listed" and saved[0]["delivery"] == "both"
