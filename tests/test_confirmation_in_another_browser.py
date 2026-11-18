"""A confirmed applicant whose application was not sent must be told so and be able to finish from any browser.

Real Chromium, real web/ sources, a fake Supabase sign-in library and a mocked API (fictional data only).

Background: the application is kept in the browser that filled in the form (localStorage) and is sent when the
email link is clicked. When the link opens somewhere else (another browser or profile, a mail app's own viewer)
nothing used to be sent and the page said only "Email verified ... or sign in if already registered". These tests
pin the behaviour that replaced it:

* same browser: the application is sent automatically (unchanged);
* another browser: a clear "not sent yet" notice and a form that sends the application;
* a confirmed account that signs in without an application is taken to the form, not shown a dead-end error;
* the owner account is never routed to the applicant form.
"""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ORIGIN = "https://spps.test"
SDK = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.118.0/dist/umd/supabase.js"
EMAIL = "applicant@example.com"
OWNER = "karansportpsy@gmail.com"
NO_APPLICATION = "No practitioner application was found for this email. Register first."

# Minimal stand-in for supabase-js. A "#access_token=..." address is the confirmation link; the session lives in
# sessionStorage like the real client. window.__signInAs lets a test choose who signInWithPassword signs in.
STUB = r"""
window.supabase={createClient:function(url,key,opts){
  const S=opts.auth.storage;let session=null;
  try{session=JSON.parse(S.getItem('fake-session')||'null')}catch{}
  const h=new URLSearchParams(location.hash.slice(1));
  const make=(email,method)=>({access_token:'h.'+btoa(JSON.stringify({sub:'u1',amr:[{method}]}))+'.s',
    user:{email,email_confirmed_at:'2026-11-16T15:46:34Z',user_metadata:JSON.parse(S.getItem('fake-meta')||'{}')}});
  if(h.get('access_token')){if(h.get('meta'))S.setItem('fake-meta',h.get('meta'));session=make(h.get('email'),'otp');S.setItem('fake-session',JSON.stringify(session))}
  return {auth:{
    onAuthStateChange(){return {data:{subscription:{unsubscribe(){}}}}},
    async getUser(){return {data:{user:session?session.user:null},error:null}},
    async getSession(){return {data:{session}}},
    async signUp(a){window.__signupData=(a.options&&a.options.data)||null;return {data:{user:{identities:[{}]},session:null},error:null}},
    async updateUser(a){window.__updated=a;return {data:{},error:null}},
    async signOut(){session=null;S.removeItem('fake-session');return {error:null}},
    async signInWithPassword(c){session=make(c.email,'password');S.setItem('fake-session',JSON.stringify(session));return {data:{session},error:null}},
  }};
}};
"""


class Site:
    def __init__(self):
        self.posts = []

    def answer(self, route):
        req, url = route.request, route.request.url
        if url == SDK:
            return route.fulfill(body=STUB, content_type="application/javascript")
        if not url.startswith(ORIGIN):
            return route.abort()
        path = url[len(ORIGIN):].split("?")[0]
        if not path.startswith("/api/"):
            name = {"/": "index.html", "/register": "index.html", "/studio": "index.html"}.get(path)
            file = WEB / (name or path.removeprefix("/assets/"))
            if not file.is_file():
                return route.fulfill(status=404, body="")
            return route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        if req.method != "GET":
            body = json.loads(req.post_data or "{}")
            self.posts.append((path, body))
            if path == "/api/auth/supabase-exchange":
                return route.fulfill(status=403, body=json.dumps({"detail": NO_APPLICATION}), content_type="application/json")
            if path == "/api/public/practitioner-application/status":
                return route.fulfill(body=json.dumps({"phase": "none", "message": NO_APPLICATION}), content_type="application/json")
            return route.fulfill(status=201, body=json.dumps({"status": "pending_approval"}), content_type="application/json")
        data = {"/api/runtime": {"integration_development": False, "free_access": True, "portal": False, "clinical_parity": False},
                "/api/public/supabase-auth-config": {"enabled": True, "url": "https://x.supabase.co", "publishable_key": "pk"},
                "/api/public/services": {"services": []}}.get(path)
        if path == "/api/auth/me":
            return route.fulfill(status=401, body=json.dumps({"detail": "Not signed in"}), content_type="application/json")
        route.fulfill(body=json.dumps(data if data is not None else []), content_type="application/json")

    def sent(self):
        return [body for path, body in self.posts if path == "/api/public/practitioner-registration"]


@pytest.fixture()
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get("SPPS_CHROMIUM") or pw.chromium.executable_path)
        yield b
        b.close()


def new_tab(browser_or_context, site, *, context=None):
    context = context or browser_or_context.new_context(viewport={"width": 1280, "height": 1000})
    page = context.new_page()
    page.route("**/*", site.answer)
    return context, page


def fill_form(page, *, password=None):
    form = page.locator("form[data-form=practitioner-register]")
    form.wait_for()
    form.locator("[name=name]").fill("Dr Fictional Applicant")
    if form.locator("[name=email]").input_value() == "":
        form.locator("[name=email]").fill(EMAIL)
    if password:
        form.locator("[name=password]").fill(password)
    form.locator("[name=photo]").set_input_files(str(ROOT / "test_support" / "avatars" / "portrait_01.webp"))
    form.locator("[name=experience_years]").fill("6")
    form.locator("[name=qualifications]").fill("MSc Sport Psychology (fictional)")
    form.locator("[name=terms_accepted]").check()
    return form


def boot(page, path):
    """Open a page and wait until the start-up requests have finished, so the screen is no longer being redrawn."""
    page.goto(ORIGIN + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def open_link(page, email=EMAIL, meta=None):
    """The confirmation link; `meta` is the user_metadata the account was created with (what Supabase hands back)."""
    extra = "&meta=" + quote(json.dumps(meta)) if meta else ""
    boot(page, f"/register#access_token=h.p.s&email={email}&type=signup{extra}")
    page.wait_for_timeout(1200)


def test_same_browser_still_sends_the_application_automatically(browser):
    site = Site()
    context, page = new_tab(browser, site)
    boot(page, "/register")
    form = fill_form(page, password="Fictional-Only-2026")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=Confirm your email")
    dialog = page.locator("body").inner_text()
    assert "Your application is not sent until you confirm" in dialog
    assert "this same browser" in dialog
    carried = page.evaluate("window.__signupData")
    assert carried["application"]["name"] == "Dr Fictional Applicant"
    assert "Fictional-Only-2026" not in json.dumps(carried) and "photo" not in json.dumps(carried)
    site.posts.clear()
    _, tab = new_tab(None, site, context=context)  # the link opens a new tab of the same browser profile
    open_link(tab)
    assert len(site.sent()) == 1
    assert "Your request has been sent for approval" in tab.locator("body").inner_text()


def test_link_opened_in_another_browser_shows_a_clear_notice_and_the_form_sends_the_application(browser):
    site = Site()
    _, page = new_tab(browser, site)  # fresh profile: no saved application
    open_link(page)
    assert site.sent() == []
    text = page.locator("body").inner_text()
    assert "Your email is confirmed, but your application has not been sent yet" in text
    assert "or sign in if already registered" not in text
    form = page.locator("form[data-form=practitioner-register]")
    assert form.locator("button[type=submit]").inner_text().startswith("Send application for approval")
    assert form.locator("[name=email]").input_value() == EMAIL
    fill_form(page).locator("button[type=submit]").click()  # no password: the confirmed email session is enough to apply
    page.wait_for_timeout(1500)
    sent = site.sent()
    assert len(sent) == 1, site.posts
    assert sent[0]["email"] == EMAIL and sent[0]["supabase_access_token"].count(".") == 2
    assert sent[0]["plan"] == "free" and sent[0]["terms_accepted"] is True
    assert "Your request has been sent for approval" in page.locator("body").inner_text()


def test_confirmed_account_without_application_is_taken_to_the_form_when_it_signs_in(browser):
    site = Site()
    _, page = new_tab(browser, site)
    boot(page, "/studio")
    login = page.locator("form[data-form=login]")
    login.wait_for()
    login.locator("[name=email]").fill(EMAIL)
    login.locator("[name=password]").fill("Fictional-Only-2026")
    login.locator("[name=terms_accepted]").check()
    page.evaluate("""sessionStorage.setItem('fake-meta', JSON.stringify({application:{name:'Dr Carried Name',
        qualifications:'Carried qualifications',experience_years:3,achievements:'',specialisms:'Focus'}}))""")
    login.locator("button[type=submit]").click()
    page.wait_for_selector("form[data-form=practitioner-register]")
    text = page.locator("body").inner_text()
    assert "Your email is confirmed, but your application has not been sent yet" in text
    assert page.locator("form[data-form=practitioner-register] [name=name]").input_value() == "Dr Carried Name"
    assert "(HTTP 403)" not in text, "the dead-end error must not be what the person is left with"
    assert page.url.endswith("/register")
    form = page.locator("form[data-form=practitioner-register]")
    assert form.locator("[name=email]").input_value() == EMAIL
    fill_form(page).locator("button[type=submit]").click()  # the password session is already open: no password needed
    page.wait_for_timeout(1500)
    sent = site.sent()
    assert len(sent) == 1, site.posts
    assert sent[0]["email"] == EMAIL
    assert "Your request has been sent for approval" in page.locator("body").inner_text()


def test_owner_is_never_routed_to_the_applicant_form(browser):
    site = Site()
    _, page = new_tab(browser, site)
    boot(page, "/studio")
    login = page.locator("form[data-form=login]")
    login.wait_for()
    login.locator("[name=email]").fill(OWNER)
    login.locator("[name=password]").fill("Fictional-Only-2026")
    login.locator("[name=terms_accepted]").check()
    login.locator("button[type=submit]").click()
    page.wait_for_timeout(1500)
    assert page.locator("form[data-form=practitioner-register]").count() == 0
    assert site.sent() == []
    assert "No practitioner application was found" in page.locator("body").inner_text()


def test_android_case_details_travel_with_the_account_and_only_the_photo_is_added(browser):
    """Sign up inside the app's WebView, open the link in Chrome (a separate browser profile): the details follow."""
    site = Site()
    _, app = new_tab(browser, site)  # the Android app's WebView
    boot(app, "/register")
    fill_form(app, password="Fictional-Only-2026").locator("button[type=submit]").click()
    app.wait_for_selector("text=Confirm your email")
    assert "your details are carried over" in app.locator("body").inner_text()
    carried = app.evaluate("window.__signupData")
    site.posts.clear()
    _, chrome = new_tab(browser, site)  # Chrome opening the mail link: separate storage, no saved form
    open_link(chrome, meta=carried)
    assert site.sent() == []
    text = chrome.locator("body").inner_text()
    assert "Your details from sign-up are filled in below" in text
    form = chrome.locator("form[data-form=practitioner-register]")
    assert form.locator("[name=name]").input_value() == "Dr Fictional Applicant"
    assert form.locator("[name=qualifications]").input_value() == "MSc Sport Psychology (fictional)"
    assert form.locator("[name=experience_years]").input_value() == "6"
    form.locator("[name=photo]").set_input_files(str(ROOT / "test_support" / "avatars" / "portrait_01.webp"))
    form.locator("[name=terms_accepted]").check()
    form.locator("button[type=submit]").click()
    chrome.wait_for_timeout(1500)
    sent = site.sent()
    assert len(sent) == 1, site.posts
    assert sent[0]["name"] == "Dr Fictional Applicant" and sent[0]["qualifications"] == "MSc Sport Psychology (fictional)"
    assert sent[0]["experience_years"] == 6 and sent[0]["photo_base64"].startswith("data:image/")
    assert chrome.evaluate("window.__updated") == {"data": {"application": None}}, "the carried copy must be removed from the account"
    assert "Your request has been sent for approval" in chrome.locator("body").inner_text()


def test_text_too_large_for_the_account_is_not_carried_and_the_person_is_told(browser):
    """user_metadata rides inside every access token; the server rejects tokens over 8192 bytes, so it stays small."""
    site = Site()
    _, page = new_tab(browser, site)
    boot(page, "/register")
    form = fill_form(page, password="Fictional-Only-2026")
    form.locator("[name=qualifications]").fill("अ" * 1000)
    form.locator("[name=achievements]").fill("अ" * 1000)
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=Confirm your email")
    assert page.evaluate("window.__signupData") in (None, {}), "oversized text must not be put into the account"
    assert "you will be asked to fill in the form again" in page.locator("body").inner_text()


def test_carried_details_are_shown_as_text_never_as_markup(browser):
    site = Site()
    _, page = new_tab(browser, site)
    hostile = '"><b id=pwn>x</b><img src=x onerror=window.__pwned=1>'
    open_link(page, meta={"application": {"name": hostile, "qualifications": "</textarea><b id=pwn2>y</b>",
                                          "experience_years": 99999, "achievements": "", "specialisms": hostile}})
    assert page.locator("#pwn, #pwn2").count() == 0
    assert page.evaluate("window.__pwned") is None
    form = page.locator("form[data-form=practitioner-register]")
    assert form.locator("[name=name]").input_value() == hostile[:120]
    assert form.locator("[name=qualifications]").input_value() == "</textarea><b id=pwn2>y</b>"
    assert form.locator("[name=experience_years]").input_value() == "70", "years are clipped to the form's own range"
