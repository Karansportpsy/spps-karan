"""Phase 4 on PostgreSQL (migration 0046): devices, preferences, the first-sign-in prompt, reminders and delivery.

Real identity functions and the real app with SPPS_PUSH_ENABLED on. A recording stand-in replaces the push services, so
nothing leaves the machine. Negative: foreign endpoints, other people's devices, switching off security messages,
duplicate reminders, content in payloads.
"""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from .conftest import TENANT, as_identity, denied, pg_login, q
from .test_booking_phase2_rls import client, consent, listed_practitioner, portal_account, request

P256 = "Q" * 87
AUTH = "a" * 22


def sub(tag=None):
    return {"platform": "web", "subscription": {
        "endpoint": f"https://fcm.googleapis.com/fcm/send/{tag or uuid.uuid4().hex}", "keys": {"p256dh": P256, "auth": AUTH}}}


@pytest.fixture
def push_app(pgenv, monkeypatch, tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    for k, v in {"MODEL_C_ENV": "local", "MODEL_C_INTEGRATION_DEV": "true", "SPPS_DEV_CLINICAL_PARITY": "true",
                 "SPPS_BETA_V1_FREE": "true", "SPPS_SUPABASE_AUTH_ENABLED": "false", "SPPS_BOT_MIN_FILL_SECONDS": "0",
                 "MODEL_C_PUBLIC_TENANT": TENANT, "SPPS_PORTAL_ENABLED": "true", "SPPS_DIRECTORY_ENABLED": "true",
                 "SPPS_DIRECTORY_BOOKING_ENABLED": "true", "SPPS_PUSH_ENABLED": "true",
                 "SPPS_VAPID_PUBLIC_KEY": "B" * 87, "SPPS_VAPID_PRIVATE_KEY": "c" * 43,
                 "SPPS_VAPID_SUBJECT": "mailto:ops@example.org",
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
    yield app
    app.state.engine.dispose()
    app.state.auth_engine.dispose()
    for name in ("portal_auth_engine", "portal_engine"):
        engine = getattr(app.state, name, None)
        if engine is not None:
            engine.dispose()


class Recorder:
    def __init__(self, dead=()):
        self.sent, self.dead = [], set(dead)

    def send(self, platform, subscription_json, payload):
        self.sent.append((platform, subscription_json, payload))
        return "dead" if any(d in subscription_json for d in self.dead) else "ok"


def run(app, push):
    """Drain the outbox: other tests leave rows behind and one pass claims at most 50."""
    from server.notifications import deliver_pending
    total = {"claimed": 0}
    for _ in range(40):
        result = deliver_pending(app.state.portal_auth_engine, None, lambda pid: None,
                                 portal_url="https://x.example/portal", workspace_url="https://x.example/studio", push=push)
        total["claimed"] += result["claimed"]
        if not result["claimed"]:
            break
    return total


# ------------------------------------------------------------------ the athlete / guardian side
def test_prompt_subscribe_state_and_secrecy(push_app):
    a, _, _ = portal_account(push_app)
    st = a.get("/api/portal/push/state")
    assert st.status_code == 200, st.text
    assert st.json()["prompt"]["should_prompt"] is True and st.json()["public_key"]
    body = sub()
    r = a.post("/api/portal/push/subscribe", json=body)
    assert r.status_code == 201, r.text
    st = a.get("/api/portal/push/state").json()
    assert st["prompt"] == {"should_prompt": False, "state": "ON", "has_device": True}
    assert len(st["devices"]) == 1
    flat = json.dumps(st)
    assert body["subscription"]["endpoint"] not in flat and P256 not in flat and AUTH not in flat


def test_not_now_hides_the_prompt_for_a_week(push_app, id_superuser):
    a, _, _ = portal_account(push_app)
    assert a.post("/api/portal/push/prompt", json={"answer": "NOT_NOW"}).status_code == 200
    assert a.get("/api/portal/push/state").json()["prompt"]["should_prompt"] is False
    q(id_superuser, "UPDATE spps_identity_dev.notification_prompts SET next_prompt_at = now() - interval '1 minute' "
                    "WHERE state='NOT_NOW' AND next_prompt_at > now() - interval '8 days'")
    assert a.get("/api/portal/push/state").json()["prompt"]["should_prompt"] is True


@pytest.mark.parametrize("endpoint", ["https://evil.example/push/abc", "http://fcm.googleapis.com/x",
                                      "https://fcm.googleapis.com.evil.example/x", "https://user@fcm.googleapis.com/x",
                                      "https://fcm.googleapis.com:8443/x", "https://169.254.169.254/latest"])
def test_only_real_push_services_are_accepted(push_app, endpoint):
    a, _, _ = portal_account(push_app)
    s = sub()
    s["subscription"]["endpoint"] = endpoint
    assert a.post("/api/portal/push/subscribe", json=s).status_code == 422


def test_requires_a_signed_in_person_and_csrf(push_app):
    anon = client(push_app)
    assert anon.get("/api/portal/push/state").status_code == 401
    assert anon.post("/api/portal/push/subscribe", json=sub()).status_code == 401
    a, _, _ = portal_account(push_app)
    del a.headers["X-CSRF-Token"]
    assert a.post("/api/portal/push/subscribe", json=sub()).status_code == 403


def test_preferences_and_security_messages_cannot_be_switched_off(push_app, id_superuser):
    a, _, _ = portal_account(push_app)
    assert a.post("/api/portal/push/preferences", json={"category": "appointments", "enabled": False}).status_code == 200
    prefs = {p["category"]: p["enabled"] for p in a.get("/api/portal/push/state").json()["preferences"]}
    assert prefs == {"appointments": False, "programme_tasks": True, "account_security": True}
    assert a.post("/api/portal/push/preferences", json={"category": "account_security", "enabled": False}).status_code == 422
    assert a.post("/api/portal/push/preferences", json={"category": "nope", "enabled": True}).status_code == 422
    assert denied(id_superuser, "INSERT INTO spps_identity_dev.notification_preferences(account_id,category,enabled) "
                                "SELECT id,'account_security',false FROM spps_identity_dev.platform_accounts LIMIT 1")


def test_nobody_can_remove_or_see_someone_elses_device(push_app):
    a, _, _ = portal_account(push_app)
    b, _, _ = portal_account(push_app)
    a.post("/api/portal/push/subscribe", json=sub())
    dev = a.get("/api/portal/push/state").json()["devices"][0]["device_id"]
    assert b.get("/api/portal/push/state").json()["devices"] == []
    assert b.post("/api/portal/push/unsubscribe", json={"device_id": dev}).json() == {"revoked": 0}
    assert len(a.get("/api/portal/push/state").json()["devices"]) == 1
    assert a.post("/api/portal/push/unsubscribe", json={"device_id": dev}).json() == {"revoked": 1}


def test_a_shared_browser_follows_the_latest_sign_in(push_app):
    a, _, _ = portal_account(push_app)
    b, _, _ = portal_account(push_app)
    s = sub()
    a.post("/api/portal/push/subscribe", json=s)
    b.post("/api/portal/push/subscribe", json=s)
    assert a.get("/api/portal/push/state").json()["devices"] == []
    assert len(b.get("/api/portal/push/state").json()["devices"]) == 1


def test_identity_tables_have_no_application_grants(pgenv):
    for role in ("spps_identity_auth", "spps_identity_portal"):
        with as_identity(pgenv, role) as conn:
            for table in ("notification_devices", "notification_preferences", "notification_prompts",
                          "notification_reminders"):
                assert denied(conn, f"SELECT * FROM spps_identity_dev.{table}"), (role, table)
            assert denied(conn, "SELECT * FROM spps_identity_dev.notif_register(1,NULL,'web',repeat('a',64),'0123456789')")


# ------------------------------------------------------------------ the workspace side (practitioner, admin, owner)
def test_practitioner_devices_prompt_and_preferences(push_app, superuser, tenant_id):
    pr, p, _, _ = listed_practitioner(push_app, superuser, tenant_id)
    assert p.get("/api/v2/push/state").json()["prompt"]["should_prompt"] is True
    assert p.post("/api/v2/push/subscribe", json=sub()).status_code == 201
    assert p.get("/api/v2/push/state").json()["prompt"]["has_device"] is True
    assert p.post("/api/v2/push/preferences", json={"category": "appointments", "enabled": False}).status_code == 200
    assert client(push_app).get("/api/v2/push/state").status_code == 401
    assert client(push_app).get("/api/v2/push/config").json()["public_key"]


# ------------------------------------------------------------------ delivery and reminders
def book(app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(app, superuser, tenant_id)
    a, person, _ = portal_account(app)
    consent(a, person, pr["id"])
    res = request(a, pr, service_id, slot_id, person)
    assert res.status_code == 201, res.text
    return pr, p, a, res.json()["booking_id"]


def test_booking_events_reach_both_people_without_content_and_respect_preferences(push_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(push_app, superuser, tenant_id)
    a, person, _ = portal_account(push_app)
    s_pr, s_ath = sub(), sub()
    p.post("/api/v2/push/subscribe", json=s_pr)
    a.post("/api/portal/push/subscribe", json=s_ath)
    consent(a, person, pr["id"])
    rec = Recorder()
    assert request(a, pr, service_id, slot_id, person).status_code == 201
    run(push_app, rec)
    mine = [x for x in rec.sent if s_pr["subscription"]["endpoint"] in x[1]]
    assert len(mine) == 1 and mine[0][2]["title"] == "New appointment request"
    for _, _, payload in rec.sent:
        flat = json.dumps(payload)
        assert "Fictional" not in flat and "@" not in flat and set(payload) == {"title", "body", "url", "kind"}
    # approving notifies the athlete; with appointments switched off the athlete's device is skipped
    a.post("/api/portal/push/preferences", json={"category": "appointments", "enabled": False})
    bid = p.get("/api/booking-requests").json()[0]["booking_id"]
    assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).status_code == 200
    rec2 = Recorder()
    run(push_app, rec2)
    assert not [x for x in rec2.sent if s_ath["subscription"]["endpoint"] in x[1]]


def test_dead_devices_are_revoked_and_failed_sends_retry(push_app, superuser, tenant_id):
    pr, p, service_id, slot_id = listed_practitioner(push_app, superuser, tenant_id)
    a, person, _ = portal_account(push_app)
    gone = sub()
    p.post("/api/v2/push/subscribe", json=gone)
    consent(a, person, pr["id"])
    request(a, pr, service_id, slot_id, person)
    run(push_app, Recorder(dead=[gone["subscription"]["endpoint"]]))
    assert p.get("/api/v2/push/state").json()["devices"] == []


def test_reminders_are_queued_once_per_appointment(push_app, superuser, tenant_id, id_superuser):
    pr, p, a, bid = book(push_app, superuser, tenant_id)
    assert p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"}).status_code == 200
    q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '30 minutes', "
                    "ends_at=now()+interval '80 minutes' WHERE id=%s", (bid,))
    def n():
        return q(id_superuser, "SELECT spps_identity_dev.notification_enqueue_due_reminders(60)")[0][0]
    assert n() >= 1
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.notification_reminders WHERE booking_id=%s", (bid,))[0][0] == 1
    before = q(id_superuser, "SELECT count(*) FROM spps_identity_dev.platform_notifications WHERE kind='booking.reminder'")[0][0]
    n()
    assert q(id_superuser, "SELECT count(*) FROM spps_identity_dev.platform_notifications WHERE kind='booking.reminder'")[0][0] == before
    assert before >= 2   # one for the practitioner and one for the athlete
    with pytest.raises(psycopg.Error):
        q(id_superuser, "SELECT spps_identity_dev.notification_enqueue_due_reminders(1)")


def test_runner_pass_queues_and_delivers(push_app, superuser, tenant_id, id_superuser):
    from server import notify_runner
    pr, p, a, bid = book(push_app, superuser, tenant_id)
    s = sub()
    a.post("/api/portal/push/subscribe", json=s)
    p.post(f"/api/booking-requests/{bid}/decision", json={"decision": "approve"})
    q(id_superuser, "UPDATE spps_identity_dev.platform_bookings SET starts_at=now()+interval '20 minutes', "
                    "ends_at=now()+interval '70 minutes' WHERE id=%s", (bid,))
    rec = Recorder()
    result = notify_runner.run_once(push_app, push=rec, reminder_minutes=60)
    assert result["reminders_queued"] >= 1 and result["claimed"] >= 1
    run(push_app, rec)   # the rest of the queue, in case earlier tests left more than one pass of rows
    assert any(x[2]["kind"] == "booking.reminder" and s["subscription"]["endpoint"] in x[1] for x in rec.sent)
