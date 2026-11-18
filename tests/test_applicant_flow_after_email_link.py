"""Applicant flow: details -> confirm email -> application sent automatically -> sign-in page with message.

The browser keeps the application (never the password) between pressing "Create free account" and
clicking the email link, submits it once the address is confirmed, and shows the practitioner sign-in
page with a "request sent for approval" message. The approval email carries a sign-in link.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

HARNESS = r"""
const vm=require('vm');let input='';
process.stdin.on('data',d=>input+=d).on('end',async()=>{
 const {src,scenario}=JSON.parse(input);
 const store=new Map(),calls={api:[],signOut:[],nav:[]};
 const ctx={
  localStorage:{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)},
  state:{supabaseEmail:'a@example.org',emailVerifiedNotice:true},
  navigateSection:(m,p)=>calls.nav.push([m,p]),
  requireSupabase:async()=>({auth:{
    getSession:async()=>({data:{session:scenario.token?{access_token:scenario.token}:null}}),
    signOut:async o=>{calls.signOut.push(o)}}}),
  api:async(path,opts)=>{calls.api.push([path,opts]);if(scenario.apiError)throw Error(scenario.apiError);return {}},
 };
 vm.createContext(ctx);
 vm.runInContext(src+';this.__t={save:saveApplicationDraft,load:loadApplicationDraft,submit:submitSavedApplication,KEY:APPLICATION_DRAFT_KEY,MSG:LINK_SESSION_MESSAGE};',ctx);
 const t=ctx.__t,out={};
 const draft={email:'A@Example.org',name:'Dr Test Name',qualifications:'MSc Sport Psychology',experience_years:4,achievements:'',specialisms:'Anxiety',photo_base64:'data:image/webp;base64,'+'A'.repeat(40)};
 if(scenario.name==='roundtrip'){
  t.save(draft);
  out.same=t.load('a@example.org');out.other=t.load('b@example.org');out.stored=store.get(t.KEY);
 }
 if(scenario.name==='expired'){
  store.set(t.KEY,JSON.stringify({...draft,saved_at:Date.now()-25*3600*1000}));
  out.loaded=t.load('a@example.org');out.stillStored=store.has(t.KEY);
 }
 if(scenario.name==='corrupt'){store.set(t.KEY,'{not json');out.loaded=t.load('a@example.org')}
 if(scenario.name.startsWith('submit')){
  t.save(draft);
  try{out.result=await t.submit(t.load('a@example.org'))}catch(e){out.error=e.message}
  out.stillStored=store.has(t.KEY);
 }
 out.calls=calls;out.state=ctx.state;
 console.log(JSON.stringify(out));
});
"""


def _source() -> str:
    start = APP.index("const APPLICATION_DRAFT_KEY")
    end = APP.index("function sessionUsedPassword(session){")
    return APP[start:end]


def _run(name: str, **extra) -> dict:
    payload = json.dumps({"src": _source(), "scenario": {"name": name, **extra}})
    done = subprocess.run(["node", "-e", HARNESS], input=payload, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@NODE
def test_draft_round_trip_is_case_insensitive_and_never_stores_a_password():
    out = _run("roundtrip")
    assert out["same"]["name"] == "Dr Test Name"
    assert out["other"] is None
    assert "password" not in out["stored"].lower()


@NODE
def test_expired_and_corrupt_drafts_are_ignored_and_removed():
    expired = _run("expired")
    assert expired["loaded"] is None and expired["stillStored"] is False
    assert _run("corrupt")["loaded"] is None


@NODE
def test_confirmed_applicant_is_submitted_then_sent_to_the_sign_in_page():
    out = _run("submit_ok", token="a.b.c")
    path, opts = out["calls"]["api"][0]
    body = opts["body"]
    assert out["result"] is True and path == "/api/public/practitioner-registration" and opts["method"] == "POST"
    assert body["supabase_access_token"] == "a.b.c"
    assert body["password"] is None and body["plan"] == "free"
    assert body["consultation_fee_paise"] == 0 and body["terms_accepted"] is True
    assert body["name"] == "Dr Test Name" and body["email"] == "A@Example.org"
    assert out["stillStored"] is False                      # the draft is removed once submitted
    assert out["calls"]["signOut"] == [{"scope": "local"}]  # the link session is not kept
    assert out["calls"]["nav"][-1] == ["studio", "/studio"]
    assert out["state"]["loginNotice"]["title"] == "Your request has been sent for approval"
    assert out["state"]["emailVerifiedNotice"] is False


@NODE
def test_already_registered_counts_as_submitted():
    out = _run("submit_409", token="a.b.c", apiError="The verified account is already registered (HTTP 409)")
    assert out["result"] is True and out["stillStored"] is False
    assert out["state"]["loginNotice"]["title"] == "Your request has been sent for approval"


@NODE
def test_other_failures_keep_the_draft_so_the_applicant_can_retry():
    out = _run("submit_fail", token="a.b.c", apiError="Registration email must match the verified Supabase account (HTTP 403)")
    assert "must match" in out["error"]
    assert out["stillStored"] is True and out["calls"]["signOut"] == []
    assert out["state"].get("loginNotice") is None


@NODE
def test_no_session_means_nothing_is_sent():
    out = _run("submit_no_token", token="")
    assert out["result"] is False and out["calls"]["api"] == [] and out["stillStored"] is True


def test_signup_saves_the_application_before_the_dialog_and_never_the_password():
    branch = APP.split("if(type==='practitioner-register'){", 1)[1].split("if(type==='login'){", 1)[0]
    assert branch.index("profilePhotoBase64(") < branch.index("sb.auth.signUp(")        # photo checked before the email is sent
    assert branch.index("saveApplicationDraft(") < branch.index("openDialog('Confirm your email'")
    saved = branch.split("saveApplicationDraft(", 1)[1].split(");", 1)[0]
    assert "password" not in saved
    assert "isOwner" in branch and "identities.length===0" in branch


def test_boot_submits_the_saved_application_and_the_owner_is_sent_to_sign_in():
    boot = APP.split("(async()=>{try{await initSupabaseAuth()", 1)[1]
    assert "submitSavedApplication(draft)" in boot and "loadApplicationDraft(state.supabaseEmail)" in boot
    assert "state.loginNotice={title:'Email confirmed'" in boot and "LINK_SESSION_MESSAGE" in boot


def test_sign_in_page_shows_the_notice_escaped():
    page = APP.split("function loginPage(){", 1)[1].split("\nfunction ", 1)[0]
    assert "login-notice" in page and "esc(state.loginNotice.title)" in page and "esc(state.loginNotice.text)" in page


def test_notice_style_exists_and_served_copies_match():
    assert ".login-notice" in (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        assert (ROOT / "www" / "assets" / name).read_bytes() == (ROOT / "web" / name).read_bytes()


def test_approval_email_carries_a_sign_in_link(monkeypatch):
    from server import supabase_practitioner_auth as auth

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=10): self.host, self.port = host, port
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def starttls(self): pass
        def login(self, user, password): pass
        def send_message(self, msg): sent.append(msg)

    for key, value in {"SPPS_SMTP_HOST": "smtp.example.org", "SPPS_SMTP_USERNAME": "u", "SPPS_SMTP_PASSWORD": "p",
                       "SPPS_SMTP_FROM": "SPPS <noreply@example.org>", "SPPS_PUBLIC_BASE_URL": "https://app.example.org/"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)

    assert auth.send_review_email("dr@example.org", "Dr Test", "approved") is True
    message = sent[0]
    body = message.get_content()
    assert message["Subject"] == "Your SPPS practitioner profile has been approved"
    assert "https://app.example.org/studio" in body and "//studio" not in body.replace("https://", "")
    assert "approved" in body.lower() and "password" in body.lower()
