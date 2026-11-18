"""Phase 1 acceptance and negative tests: registration can never silently lose an application."""
from __future__ import annotations

import logging

from sqlalchemy import select

from server.models import Practitioner, PractitionerProfile
from server.v2.models import RegistrationApplication, RegistrationTransition

from .conftest import (APPLICATION, OWNER_LOGIN, PRACTITIONER_B, fresh_client, form_token, login, photo_b64)

EMAIL = "applicant.one@example.org"


def _sign_up_in_webview(app, fake, email=EMAIL):
    """Android WebView: fill the form, store the application on the server, then Supabase signUp with the claim."""
    webview = fresh_client(app)
    res = webview.post("/api/public/practitioner-applications/pending",
                       json={**APPLICATION, "email": email, "form_token": form_token(webview)})
    assert res.status_code == 201, res.text
    claim = res.json()["claim"]
    token = fake.account(email, confirmed=False, metadata={"spps_claim": claim})
    return webview, claim, token


def test_webview_signup_confirmed_in_chrome_reaches_admin_dashboard_without_retyping(app, fake_supabase):
    _webview, _claim, token = _sign_up_in_webview(app, fake_supabase)
    fake_supabase.confirm(token)
    chrome = fresh_client(app)  # a different browser: no cookies, no localStorage, only the e-mail link session
    link_session = fake_supabase.same_account_new_session(token, password=False)
    res = chrome.post("/api/public/practitioner-application/promote", json={"access_token": link_session})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "under_review" and body["promoted"] and body["needs_photo"]

    # The photo can be added after confirmation, from the same other browser.
    res = chrome.post("/api/public/practitioner-application/photo",
                      json={"access_token": link_session, "photo_base64": photo_b64()})
    assert res.status_code == 200 and res.json()["photo_saved"]

    owner = fresh_client(app)
    h = login(owner, OWNER_LOGIN)
    summary = owner.get("/api/admin/registrations/summary").json()
    assert summary["new_applications"] == 1
    queue = owner.get("/api/admin/practitioner-reviews").json()
    row = next(r for r in queue if r["email"] == EMAIL)
    assert row["qualifications"] == APPLICATION["qualifications"]  # typed once, in the WebView
    assert row["status"] == "pending_approval" and row["email_verified"]

    with app.state.DB() as db:
        app_row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.email == EMAIL))
        moves = [(t.from_state, t.to_state, t.actor_kind) for t in db.scalars(
            select(RegistrationTransition).where(RegistrationTransition.application_id == app_row.id))]
        assert app_row.claim_hash is None  # single use
        profile = db.scalar(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id == app_row.practitioner_id))
        assert profile.avatar_bytes
    assert moves == [("applied", "email_verified", "system"), ("email_verified", "under_review", "system")]
    t = owner.get(f"/api/admin/registrations/{app_row.id}/transitions")
    assert [x["to"] for x in t.json()] == ["email_verified", "under_review"]
    assert h


def test_first_password_sign_in_from_any_browser_also_promotes(app, fake_supabase):
    _w, _c, token = _sign_up_in_webview(app, fake_supabase)
    fake_supabase.confirm(token)
    chrome = fresh_client(app)
    signed_in = fake_supabase.same_account_new_session(token, password=True)
    res = chrome.post("/api/auth/supabase-exchange", json={"access_token": signed_in, "terms_accepted": True})
    assert res.status_code == 403 and "pending verification" in res.text  # promoted, now awaiting the owner
    with app.state.DB() as db:
        assert db.scalar(select(Practitioner).where(Practitioner.email == EMAIL))


def test_application_status_check_promotes_too(app, fake_supabase):
    _w, _c, token = _sign_up_in_webview(app, fake_supabase)
    fake_supabase.confirm(token)
    other = fresh_client(app)
    res = other.post("/api/public/practitioner-application/status",
                     json={"access_token": fake_supabase.same_account_new_session(token, password=True)})
    assert res.status_code == 200 and res.json()["phase"] == "pending"


def test_unverified_account_never_reaches_review(app, fake_supabase):
    _w, _c, token = _sign_up_in_webview(app, fake_supabase)  # never confirmed
    res = fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": token})
    assert res.status_code == 403
    res = fresh_client(app).post("/api/auth/supabase-exchange", json={"access_token": token, "terms_accepted": True})
    assert res.status_code == 403
    owner = fresh_client(app)
    login(owner, OWNER_LOGIN)
    assert all(r["email"] != EMAIL for r in owner.get("/api/admin/practitioner-reviews").json())
    summary = owner.get("/api/admin/registrations/summary").json()
    assert summary["new_applications"] == 0 and summary["state_counts"].get("applied") == 1
    assert summary["confirmed_no_application"] == []
    with app.state.DB() as db:
        assert not db.scalar(select(Practitioner).where(Practitioner.email == EMAIL))


def test_owner_cannot_decide_an_unverified_application(app, fake_supabase):
    owner = fresh_client(app)
    h = login(owner, OWNER_LOGIN)
    _w, _c, _token = _sign_up_in_webview(app, fake_supabase)
    # No practitioner exists for an unverified application, so there is nothing the owner can approve.
    with app.state.DB() as db:
        assert db.scalar(select(RegistrationApplication.practitioner_id).where(
            RegistrationApplication.email == EMAIL)) is None
    assert owner.post("/api/admin/practitioners/999999/approval", json={"decision": "approve", "note": "x" * 6},
                      headers=h).status_code == 404


def test_confirmed_account_without_application_is_visible_to_the_owner(app, fake_supabase):
    token = fake_supabase.account("no.form@example.org", confirmed=True)  # confirmed, never sent the form
    res = fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": token})
    assert res.status_code == 200 and res.json()["state"] == "email_verified" and not res.json()["has_application"]
    # Repeated calls do not duplicate the entry.
    fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": token})
    owner = fresh_client(app)
    login(owner, OWNER_LOGIN)
    listed = owner.get("/api/admin/registrations/summary").json()["confirmed_no_application"]
    assert [x["email"] for x in listed] == ["no.form@example.org"]

    # When that person later sends the form, the entry is resolved.
    claim = fresh_client(app).post("/api/public/practitioner-applications/pending",
                                   json={**APPLICATION, "email": "no.form@example.org",
                                         "form_token": form_token(owner)}).json()["claim"]
    fake_supabase.accounts[token]["metadata"] = {"spps_claim": claim}
    assert fresh_client(app).post("/api/public/practitioner-application/promote",
                                  json={"access_token": token}).json()["state"] == "under_review"
    assert owner.get("/api/admin/registrations/summary").json()["confirmed_no_application"] == []


def test_practitioner_cannot_read_any_pending_application(app, fake_supabase):
    _sign_up_in_webview(app, fake_supabase)
    other = fresh_client(app)
    login(other, PRACTITIONER_B)
    for path in ("/api/admin/registrations/summary", "/api/admin/registrations/1/transitions",
                 "/api/admin/practitioner-reviews"):
        res = other.get(path)
        assert res.status_code == 403, path
        assert EMAIL not in res.text and APPLICATION["qualifications"] not in res.text


def test_claim_is_bound_to_the_email_it_was_issued_for(app, fake_supabase):
    _w, claim, _token = _sign_up_in_webview(app, fake_supabase)
    thief = fake_supabase.account("someone.else@example.org", confirmed=True, metadata={"spps_claim": claim})
    res = fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": thief})
    assert res.json()["state"] == "email_verified" and not res.json()["has_application"]
    with app.state.DB() as db:
        assert not db.scalar(select(Practitioner).where(Practitioner.email == "someone.else@example.org"))
        row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.email == EMAIL))
        assert row.state == "applied" and row.claim_hash  # untouched, still waiting for its owner


def test_v1_carried_details_fallback_still_works(app, fake_supabase):
    token = fake_supabase.account("carried@example.org", confirmed=True,
                                  metadata={"application": {**APPLICATION, "name": "Dr Carried Applicant"}})
    res = fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": token})
    assert res.json()["state"] == "under_review"
    with app.state.DB() as db:
        assert db.scalar(select(Practitioner.name).where(Practitioner.email == "carried@example.org")) == \
            "Dr Carried Applicant"


def test_bot_check_and_rate_limits(app):
    c = fresh_client(app)
    body = {**APPLICATION, "email": "bot@example.org"}
    assert c.post("/api/public/practitioner-applications/pending", json=body).status_code == 400  # no token
    assert c.post("/api/public/practitioner-applications/pending",
                  json={**body, "form_token": "1.2.3"}).status_code == 400  # forged
    assert c.post("/api/public/practitioner-applications/pending",
                  json={**body, "form_token": form_token(c), "website": "spam"}).status_code == 400  # honeypot
    codes = [c.post("/api/public/practitioner-applications/pending",
                    json={**body, "form_token": form_token(c)}).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]  # per-e-mail limit: 3 per hour


def test_form_token_must_not_be_submitted_instantly(app, v2env):
    v2env.setenv("SPPS_BOT_MIN_FILL_SECONDS", "30")
    c = fresh_client(app)
    res = c.post("/api/public/practitioner-applications/pending",
                 json={**APPLICATION, "email": "fast@example.org", "form_token": form_token(c)})
    assert res.status_code == 400


def _promoted_applicant(app, fake):
    _w, _c, token = _sign_up_in_webview(app, fake)
    fake.confirm(token)
    pw = fake.same_account_new_session(token, password=True)
    fresh_client(app).post("/api/public/practitioner-application/promote", json={"access_token": pw})
    with app.state.DB() as db:
        pid = db.scalar(select(Practitioner.id).where(Practitioner.email == EMAIL))
        db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == pid))
    return pid, pw


def test_state_machine_follows_owner_decisions_and_is_audited(app, fake_supabase):
    pid, pw = _promoted_applicant(app, fake_supabase)
    owner = fresh_client(app)
    h = login(owner, OWNER_LOGIN)
    res = owner.post(f"/api/admin/practitioners/{pid}/approval",
                     json={"decision": "request_more_info", "note": "Please add your registration number"}, headers=h)
    assert res.status_code == 200, res.text
    res = fresh_client(app).post("/api/public/practitioner-application/update",
                                 json={"access_token": pw, "qualifications": "MSc Sport Psychology, reg. FICT-1",
                                       "experience_years": 5, "achievements": "", "specialisms": ""})
    assert res.status_code == 200, res.text
    with app.state.DB() as db:  # approval needs a photo-less profile to have verified e-mail; it does
        pass
    res = owner.post(f"/api/admin/practitioners/{pid}/approval",
                     json={"decision": "approve", "note": "Verified"}, headers=h)
    assert res.status_code == 200, res.text
    with app.state.DB() as db:
        row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.practitioner_id == pid))
        moves = [(t.from_state, t.to_state, t.actor_kind, t.actor_practitioner_id is not None) for t in db.scalars(
            select(RegistrationTransition).where(RegistrationTransition.application_id == row.id)
            .order_by(RegistrationTransition.id))]
    assert row.state == "approved"
    assert moves[2:] == [("under_review", "more_info_requested", "owner", True),
                         ("more_info_requested", "under_review", "applicant", False),
                         ("under_review", "approved", "owner", True)]


def test_email_failures_are_logged_shown_and_resendable(app, fake_supabase, v2env):
    pid, _pw = _promoted_applicant(app, fake_supabase)
    owner = fresh_client(app)
    h = login(owner, OWNER_LOGIN)
    res = owner.post(f"/api/admin/practitioners/{pid}/approval",
                     json={"decision": "reject", "note": "Qualification could not be verified"}, headers=h)
    assert res.status_code == 200 and res.json()["notification_sent"] is False
    assert res.json()["email_failure_reason"] == "mail_not_configured"
    failures = owner.get("/api/admin/registrations/summary").json()["email_failures"]
    assert len(failures) == 1 and failures[0]["kind"] == "rejected" and failures[0]["attempts"] == 1
    assert EMAIL not in str(failures)  # the owner sees the name and kind; the address is not repeated here
    fid = failures[0]["id"]
    again = owner.post(f"/api/admin/email-deliveries/{fid}/resend", headers=h)
    assert again.status_code == 200 and again.json()["status"] == "failed" and again.json()["attempts"] == 2

    import server.supabase_practitioner_auth as sbauth
    for k in ("SPPS_SMTP_HOST", "SPPS_SMTP_USERNAME", "SPPS_SMTP_PASSWORD", "SPPS_SMTP_FROM"):
        v2env.setenv(k, "fictional")
    sent = []
    v2env.setattr(sbauth, "send_review_email", lambda *a, **k: sent.append(a[2]) or True)
    ok = owner.post(f"/api/admin/email-deliveries/{fid}/resend", headers=h)
    assert ok.json()["status"] == "sent" and sent == ["rejected"]
    assert owner.get("/api/admin/registrations/summary").json()["email_failures"] == []
    assert owner.post(f"/api/admin/email-deliveries/{fid}/resend", headers=h).status_code == 404
    other = fresh_client(app)
    hb = login(other, PRACTITIONER_B)
    assert other.post(f"/api/admin/email-deliveries/{fid}/resend", headers=hb).status_code == 403


def test_request_id_on_every_response_and_log_line_without_personal_data(app, caplog):
    c = fresh_client(app)
    caplog.set_level(logging.INFO)
    res = c.get("/health/live", headers={"X-Request-ID": "abcdef12-3456"})
    assert res.headers["X-Request-ID"] == "abcdef12-3456"
    generated = c.get("/api/public/services").headers["X-Request-ID"]
    assert len(generated) == 32
    assert c.get("/health/live", headers={"X-Request-ID": "bad id!"}).headers["X-Request-ID"] != "bad id!"
    c.post("/api/public/practitioner-applications/pending",
           json={**APPLICATION, "email": "logcheck@example.org", "form_token": form_token(c)})
    records = [r for r in caplog.records if r.name.startswith("spps")]
    assert records and all(getattr(r, "request_id", None) not in (None, "") for r in records)
    text = "\n".join(r.getMessage() for r in records)
    assert "logcheck@example.org" not in text and APPLICATION["qualifications"] not in text
    assert any('"rid": "abcdef12-3456"' in r.getMessage() for r in records)


def test_log_redaction_masks_emails_and_tokens():
    from server.v2.reqlog import redact, safe_path
    assert redact("mail to a.b@example.org now") == "mail to [email] now"
    assert "[redacted]" in safe_path("/api/v2/report-links/" + "x" * 40)


def test_health_ready_requires_v2_tables(app):
    assert fresh_client(app).get("/health/ready").json()["status"] == "ready"
    from sqlalchemy import text
    with app.state.engine.begin() as conn:
        conn.execute(text("DROP TABLE email_deliveries"))
    assert fresh_client(app).get("/health/ready").status_code == 503
