"""Phase 4 real-browser acceptance: the first-sign-in pop-up and the Settings card, with fake browser push support."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import MockSite, ORIGIN, WEB, chromium

FAKE_BROWSER = """
(() => {
  let permission = 'default';
  window.__pushSubscribes = 0;
  const sub = { endpoint: 'https://fcm.googleapis.com/fcm/send/fictional-endpoint',
                toJSON() { return { endpoint: this.endpoint, keys: { p256dh: 'Q'.repeat(87), auth: 'a'.repeat(22) } }; },
                async unsubscribe() { current = null; return true; } };
  let current = null;
  const reg = { pushManager: { async getSubscription() { return current; },
                               async subscribe(o) { window.__pushSubscribes++; window.__serverKey = o.applicationServerKey.length; current = sub; return sub; } } };
  Object.defineProperty(navigator, 'serviceWorker', { value: { register: async () => reg, ready: Promise.resolve(reg),
                        getRegistration: async () => reg }, configurable: true });
  window.PushManager = function () {};
  window.Notification = { get permission() { return permission; }, requestPermission: async () => { window.__asked = true; permission = window.__answer || 'granted'; return permission; } };
})();
"""

STATE = {"enabled": True, "public_key": "B" * 87,
         "prompt": {"should_prompt": True, "state": None, "has_device": False}, "devices": [],
         "preferences": [{"category": "account_security", "enabled": True, "locked": True},
                         {"category": "appointments", "enabled": True, "locked": False},
                         {"category": "programme_tasks", "enabled": True, "locked": False}]}


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = chromium(pw)
        yield b
        b.close()


def open_portal(browser, state=None, *, support=True, answer=None):
    state = json.loads(json.dumps(state or STATE))
    site = MockSite({"/api/portal/links": [], "/api/portal/bookings": [], "/api/portal/notifications": [],
                     "/api/portal/dependants": [], "/api/portal/programmes": {"programmes": [], "summaries": []},
                     "/api/portal/push/state": state,
                     "/api/portal/auth/me": {"account_id": 8, "person_id": 9, "roles": ["athlete"],
                                             "display_name": "Fictional Person", "email": "person@example.invalid",
                                             "csrf": "fictional-csrf"}})
    site.runtime["portal"] = True
    site.post_answers["/api/portal/push/subscribe"] = {"device_id": 1}
    page = browser.new_context(viewport={"width": 1000, "height": 900}).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(FAKE_BROWSER if support else "delete window.PushManager")
    if answer:
        page.add_init_script(f"window.__answer = '{answer}'")

    def route(r):
        if r.request.url == ORIGIN + "/portal":
            return r.fulfill(body=(WEB / "portal.html").read_bytes(), content_type="text/html")
        return site.answer(r)

    page.route("**/*", route)
    page.goto(ORIGIN + "/portal")
    return page, site, errors


def test_prompt_shows_once_signed_in_and_turn_on_subscribes_without_content(browser):
    page, site, errors = open_portal(browser)
    dialog = page.get_by_role("dialog", name="Turn on notifications?")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("never show names, times or health details")
    page.get_by_role("button", name="Turn on notifications").click()
    expect(dialog).to_have_count(0)
    path, body = next(p for p in site.posts if p[0] == "/api/portal/push/subscribe")
    assert body["platform"] == "web" and body["subscription"]["endpoint"].startswith("https://fcm.googleapis.com/")
    assert page.evaluate("window.__serverKey") == 65   # the public key decoded to the uncompressed P-256 point
    assert not errors, errors


def test_not_now_is_recorded_and_the_dialog_closes_with_escape(browser):
    page, site, _ = open_portal(browser)
    dialog = page.get_by_role("dialog", name="Turn on notifications?")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    assert ("/api/portal/push/prompt", {"answer": "NOT_NOW"}) in site.posts
    assert not any(p[0].endswith("/subscribe") for p in site.posts)


def test_blocked_permission_shows_a_helpful_message_and_no_subscription(browser):
    page, site, _ = open_portal(browser, answer="denied")
    page.get_by_role("button", name="Turn on notifications").click()
    expect(page.get_by_role("alert").first).to_contain_text("blocked")
    assert not any(p[0].endswith("/subscribe") for p in site.posts)


def test_no_prompt_when_the_server_says_not_to_or_the_browser_cannot(browser):
    answered = json.loads(json.dumps(STATE))
    answered["prompt"] = {"should_prompt": False, "state": "NOT_NOW", "has_device": False}
    page, _, _ = open_portal(browser, answered)
    page.wait_for_timeout(400)
    expect(page.get_by_role("dialog")).to_have_count(0)
    page2, _, errors = open_portal(browser, support=False)
    page2.wait_for_timeout(400)
    expect(page2.get_by_role("dialog")).to_have_count(0)
    assert not errors, errors


def test_settings_card_lists_preferences_and_locks_security_messages(browser):
    page, site, errors = open_portal(browser, support=True)
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_role("button", name="Account").click()
    card = page.locator("[data-push-card]")
    expect(card).to_contain_text("Appointments and reminders")
    locked = card.get_by_label("Account and security (always on)")
    expect(locked).to_be_disabled()
    card.get_by_label("Appointments and reminders").uncheck()
    page.wait_for_timeout(200)
    assert ("/api/portal/push/preferences", {"category": "appointments", "enabled": False}) in site.posts
    assert not errors, errors


def test_workspace_sign_in_uses_the_practitioner_endpoints_and_settings_shows_the_card(browser):
    site = MockSite({"/api/v2/push/state": json.loads(json.dumps(STATE))})
    site.post_answers["/api/v2/push/subscribe"] = {"device_id": 3}
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    context.add_init_script(FAKE_BROWSER)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", site.answer)
    page.goto(ORIGIN + "/studio")
    dialog = page.get_by_role("dialog", name="Turn on notifications?")
    expect(dialog).to_be_visible()
    page.get_by_role("button", name="Turn on notifications").click()
    expect(dialog).to_have_count(0)
    assert any(p[0] == "/api/v2/push/subscribe" for p in site.posts)
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.locator("[data-push-card]")).to_contain_text("What to be told about")
    assert not errors, errors
