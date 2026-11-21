"""Guest booking on PostgreSQL (migration 0047): request without an account, e-mail confirmation, the account shell,
practitioner approval, the create-account link in the approval e-mail, and the claim.

Real identity functions and the real app with SPPS_GUEST_BOOKING_ENABLED on. Negative: the same answer for every
outcome of the request, expired/used/forged tokens, no sign-in before the claim, no booking without the e-mail
confirmation, a second claim, minors, clinical notes, the honeypot.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import TENANT, pg_login, q
from .test_booking_phase2_rls import PASSWORD, client, listed_practitioner


@pytest.fixture
def guest_app(pgenv, monkeypatch, tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    for k, v in {"MODEL_C_ENV": "local", "MODEL_C_INTEGRATION_DEV": "true", "SPPS_DEV_CLINICAL_PARITY": "true",
                 "SPPS_BETA_V1_FREE": "true", "SPPS_SUPABASE_AUTH_ENABLED": "false", "SPPS_BOT_MIN_FILL_SECONDS": "0",
                 "MODEL_C_PUBLIC_TENANT": TENANT, "SPPS_PORTAL_ENABLED": "true", "SPPS_DIRECTORY_ENABLED": "true",
                 "SPPS_DIRECTORY_BOOKING_ENABLED": "true", "SPPS_GUEST_BOOKING_ENABLED": "true",
                 "SPPS_NOTIFY_WORKER_ENABLED": "true",
                 "SPPS_CRISIS_CONTACTS_TEXT": "Fictional helpline 0000 (24/7).", "SPPS_PORTAL_DEV_OUTBOX": str(outbox),
                 "SPPS_FIELD_KEYS": "k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=", "SPPS_FIELD_KEY_CURRENT": "k1",
                 "DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_APP_URL"],
                 "SPPS_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_DEV_AUTH_URL"],
                 "SPPS_IDENTITY_AUTH_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_AUTH_URL"],
                 "SPPS_IDENTITY_PORTAL_DATABASE_URL": pgenv["SPPS_V2_PG_SPPS_IDENTITY_PORTAL_URL"]}.items():
        monkeypatch.setenv(k, v)
    from server.v2 import guards
    guards.reset()
    import server.main as main
    app = main.create_app(None, init_database=False)
    app.state.test_outbox = outbox
    app.state.mail_file = tmp_path / "mail.jsonl"
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()
    for name in ("portal_auth_engine", "portal_engine"):
        engine = getattr(app.state, name, None)
        if engine is not None:
            engine.dispose()


def outbox_rows(app, to, kind):
    path = Path(app.state.test_outbox)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["to"] == to and r["kind"] == kind]


def request_body(pr, service_id, slot_id, email, **over):
    return {"practitioner_id": pr["id"], "service_id": service_id, "slot_id": slot_id, "name": "Fictional Guest",
            "email": email, "note": "Fictional practical note: weekday evenings.", "adult_attested": True,
            "consent_accepted": True, "website": "", **over}


def post_request(g, body):
    body = {**body, "form_token": g.get("/api/public/form-token").json()["form_token"]}
    return g.post("/api/v2/public/guest-bookings", json=body)


def deliver(app):
    from server.notifications import FileDeliveryProvider, deliver_pending
    provider = FileDeliveryProvider(app.state.mail_file)
    for _ in range(40):
        result = deliver_pending(app.state.portal_auth_engine, provider, lambda pid: None,
                                 portal_url="https://x.example/portal", workspace_url="https://x.example/studio",
                                 guest_claims=True)
        if not result["claimed"]:
            break
    path = Path(app.state.mail_file)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_the_whole_guest_flow_from_request_to_a_working_account(guest_app, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(guest_app, superuser, tenant_id)
    g = client(guest_app)
    email = f"guest-{uuid.uuid4().hex[:10]}@example.invalid"
    # 1. A visitor sees the open times without signing in.
    res = g.get(f"/api/v2/public/directory/{pr['id']}/slots?service_id={service_id}")
    assert res.status_code == 200, res.text
    assert [s["slot_id"] for s in res.json()["slots"]] == [slot_id]
    # 2. The request stores nothing the practitioner can see and creates no account.
    r = post_request(g, request_body(pr, service_id, slot_id, email))
    assert r.status_code == 202, r.text
    assert "Check your mail" in r.json()["message"]
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.platform_accounts WHERE email_normalized=%s",
             (email,))[0][0] == 0
    assert p.get("/api/booking-requests").json() == []
    token = outbox_rows(guest_app, email, "guest-confirm")[-1]["token"]
    assert len(token) >= 20
    # 3. Confirming (the button on the e-mail page) creates the shell account and the usual pending request.
    c = g.post("/api/v2/public/guest-bookings/confirm", json={"token": token})
    assert c.status_code == 200, c.text
    assert c.json()["status"] == "submitted"
    acct = q(id_superuser, """SELECT status, guest_origin, claimed_at IS NULL, adult_attested_at IS NOT NULL
                              FROM spps_identity_dev.platform_accounts WHERE email_normalized=%s""", (email,))
    assert acct == [("VERIFIED", True, True, True)]
    purposes = {x[0] for x in q(id_superuser, """SELECT e.purpose FROM spps_identity_dev.platform_consent_events e
                  JOIN spps_identity_dev.platform_accounts a ON a.person_id=e.person_id
                  WHERE a.email_normalized=%s AND e.decision='GRANTED'""", (email,))}
    assert {"CASE_LINK", "APPOINTMENT_BOOKING", "VIDEO_CONSULTATION", "PLATFORM_TERMS", "PRIVACY_NOTICE"} <= purposes
    rows = p.get("/api/booking-requests").json()
    assert len(rows) == 1 and rows[0]["status"] == "PENDING_APPROVAL"
    assert rows[0]["subject_display_name"] == "Fictional Guest" and rows[0]["service_name"]
    assert email not in json.dumps(rows[0])
    # The link works once.
    assert g.post("/api/v2/public/guest-bookings/confirm", json={"token": token}).status_code == 410
    # 4. Nobody can sign in to the shell: the password is unknown to everyone.
    assert client(guest_app).post("/api/portal/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401
    # 5. The practitioner approves; the approval e-mail carries a create-account link and no details.
    booking = rows[0]["booking_id"]
    d = p.post(f"/api/booking-requests/{booking}/decision", json={"decision": "approve"})
    assert d.status_code == 200 and d.json()["status"] == "CONFIRMED", d.text
    mails = [m for m in deliver(guest_app) if m["to"] == email]
    claim_mail = [m for m in mails if "create your account" in m["subject"].lower()]
    assert len(claim_mail) == 1, mails
    body = claim_mail[0]["body"]
    match = re.search(r"\?claim=([A-Za-z0-9_-]+)", body)
    assert match, body
    assert "Fictional" not in body and pr["email"] not in body
    claim = match.group(1)
    # 6. Choosing a password turns the shell into a normal account; the token is single use.
    assert g.post("/api/v2/public/guest-claim", json={"token": claim, "password": "short"}).status_code == 422
    ok = g.post("/api/v2/public/guest-claim", json={"token": claim, "password": PASSWORD})
    assert ok.status_code == 200, ok.text
    assert g.post("/api/v2/public/guest-claim", json={"token": claim, "password": PASSWORD + "x"}).status_code == 410
    a = client(guest_app)
    login = a.post("/api/portal/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    a.headers["X-CSRF-Token"] = login.json()["csrf"]
    mine = [b for b in a.get("/api/portal/bookings").json() if b["booking_id"] == booking]
    assert mine and mine[0]["status"] == "CONFIRMED"
    links = [l for l in a.get("/api/portal/links").json() if l["practitioner_id"] == pr["id"]]
    assert [l["status"] for l in links] == ["ACTIVE"]
    assert q(id_superuser, "SELECT claimed_at IS NOT NULL FROM spps_identity_dev.platform_accounts "
                           "WHERE email_normalized=%s", (email,)) == [(True,)]
    # No second claim e-mail for an account that is already claimed.
    assert len([m for m in deliver(guest_app) if m["to"] == email and "create your account" in m["subject"].lower()]) == 1


def test_every_outcome_of_the_request_looks_the_same_and_existing_accounts_are_not_revealed(guest_app, superuser,
                                                                                            tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(guest_app, superuser, tenant_id)
    g = client(guest_app)
    new = f"guest-{uuid.uuid4().hex[:10]}@example.invalid"
    from .test_booking_phase2_rls import portal_account
    _, _, existing = portal_account(guest_app)
    first = post_request(g, request_body(pr, service_id, slot_id, new))
    second = post_request(client(guest_app), request_body(pr, service_id, slot_id, existing))
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert outbox_rows(guest_app, new, "guest-confirm") and not outbox_rows(guest_app, existing, "guest-confirm")
    assert outbox_rows(guest_app, existing, "existing-account-notice")
    # Nothing was created for the real account, and the practitioner sees nothing yet.
    assert p.get("/api/booking-requests").json() == []


def test_tokens_are_checked_and_unconfirmed_requests_do_not_book(guest_app, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(guest_app, superuser, tenant_id)
    g = client(guest_app)
    email = f"guest-{uuid.uuid4().hex[:10]}@example.invalid"
    assert post_request(g, request_body(pr, service_id, slot_id, email)).status_code == 202
    token = outbox_rows(guest_app, email, "guest-confirm")[-1]["token"]
    for bad in ("x" * 43, token[:-1] + ("A" if token[-1] != "A" else "B"), "short"):
        assert g.post("/api/v2/public/guest-bookings/confirm", json={"token": bad}).status_code in (410, 422)
    q(id_superuser, "UPDATE spps_identity_dev.guest_booking_requests SET expires_at=now()-interval '1 minute' "
                    "WHERE email_normalized=%s", (email,))
    assert g.post("/api/v2/public/guest-bookings/confirm", json={"token": token}).status_code == 410
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.platform_accounts WHERE email_normalized=%s",
             (email,))[0][0] == 0
    assert p.get("/api/booking-requests").json() == []


def test_form_rules_adult_note_honeypot_and_availability(guest_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(guest_app, superuser, tenant_id)
    g = client(guest_app)
    email = f"guest-{uuid.uuid4().hex[:10]}@example.invalid"
    assert post_request(g, request_body(pr, service_id, slot_id, email, adult_attested=False)).status_code == 422
    assert post_request(g, request_body(pr, service_id, slot_id, email, consent_accepted=False)).status_code == 422
    assert post_request(g, request_body(pr, service_id, slot_id, email,
                                        note="I have been diagnosed with depression")).status_code == 422
    assert post_request(g, request_body(pr, service_id, slot_id, email, website="http://spam.example")).status_code != 202
    assert post_request(g, request_body(pr, service_id, slot_id + 9999, email)).status_code == 409
    assert post_request(g, request_body(pr, service_id + 9999, slot_id, email)).status_code == 404
    assert not outbox_rows(guest_app, email, "guest-confirm")
    # The form token is required.
    body = request_body(pr, service_id, slot_id, email)
    assert g.post("/api/v2/public/guest-bookings", json=body).status_code in (400, 422)


def test_a_taken_slot_is_refused_at_confirmation(guest_app, superuser, tenant_id, id_superuser):
    pr, p, service_id, slot_id = listed_practitioner(guest_app, superuser, tenant_id)
    one, two = (f"guest-{uuid.uuid4().hex[:10]}@example.invalid" for _ in range(2))
    assert post_request(client(guest_app), request_body(pr, service_id, slot_id, one)).status_code == 202
    assert post_request(client(guest_app), request_body(pr, service_id, slot_id, two)).status_code == 202
    t1, t2 = (outbox_rows(guest_app, e, "guest-confirm")[-1]["token"] for e in (one, two))
    assert client(guest_app).post("/api/v2/public/guest-bookings/confirm", json={"token": t1}).status_code == 200
    late = client(guest_app).post("/api/v2/public/guest-bookings/confirm", json={"token": t2})
    assert late.status_code == 409 and "taken" in late.json()["detail"].lower()
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.platform_accounts WHERE email_normalized=%s",
             (two,))[0][0] == 0


def test_the_video_window_opens_fifteen_minutes_before_the_start(id_superuser):
    # The boundary itself is asserted in test_booking_phase3_video; here the function definition is pinned.
    src = q(id_superuser, "SELECT pg_get_functiondef('spps_identity_dev.video_booking_open(spps_identity_dev.platform_bookings)'::regprocedure)")[0][0]
    assert "15 minutes" in src and "10 minutes" not in src


def test_new_objects_are_closed_to_the_application_roles(id_superuser):
    rows = q(id_superuser, """SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef,
                                     has_function_privilege('spps_identity_portal', p.oid, 'EXECUTE'),
                                     has_function_privilege('spps_identity_auth', p.oid, 'EXECUTE')
                              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                              WHERE n.nspname='spps_identity_dev' AND p.proname IN ('guest_request_create',
                              'guest_request_peek','guest_request_confirm','guest_claim_issue','guest_claim_account',
                              'guest_requests_purge')""")
    assert len(rows) == 6, rows
    assert all(owner == "spps_identity_owner" and definer and not portal and auth
               for _, owner, definer, portal, auth in rows), rows
    for table in ("guest_booking_requests", "guest_claim_tokens"):
        flags = q(id_superuser, """SELECT c.relrowsecurity, c.relforcerowsecurity,
                     has_table_privilege('spps_identity_auth', c.oid, 'SELECT,INSERT,UPDATE,DELETE'),
                     has_table_privilege('spps_identity_portal', c.oid, 'SELECT,INSERT,UPDATE,DELETE')
                     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname='spps_identity_dev' AND c.relname=%s""", (table,))
        assert flags == [(True, True, False, False)], (table, flags)
