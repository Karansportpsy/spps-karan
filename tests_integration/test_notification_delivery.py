"""P1: outbox delivery worker — claim, content-free messages, backoff, dead letter, no double send."""
import json
import threading

from sqlalchemy import create_engine, select, text

from server.models import Practitioner
from server.notifications import FileDeliveryProvider, deliver_pending
from tests_integration.test_portal_bookings import admin, bookable, future_slot, request
from tools.identity_review_db import EPHEMERAL_TARGET


def engines(app):
    return app.state.portal_auth_engine


def practitioner_email_lookup(app):
    def lookup(pid):
        with app.state.AuthDB() as db:
            return db.scalar(select(Practitioner.email).where(Practitioner.id == pid))
    return lookup


class Flaky:
    def __init__(self, fail_times):
        self.fail_times, self.sent = fail_times, []

    def send(self, message):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("fictional provider outage")
        self.sent.append(message)


def drain(app, provider, **kw):
    return deliver_pending(engines(app), provider, practitioner_email_lookup(app),
                           portal_url="https://portal.example.invalid/portal",
                           workspace_url="https://portal.example.invalid/studio", **kw)


def test_delivery_is_content_free_and_reaches_both_parties(app, integration_env, tmp_path):
    alice, a, person, link = bookable(app, integration_env)
    request(a, link, future_slot(alice), note="FICTIONAL-SENSITIVE-NOTE")
    outbox = tmp_path / "mail.jsonl"
    total = 0
    for _ in range(20):  # earlier tests may have queued many notifications
        result = drain(app, FileDeliveryProvider(outbox), limit=500)
        total += result["claimed"]
        if result["claimed"] == 0:
            break
    rows = [json.loads(x) for x in outbox.read_text(encoding="utf-8").splitlines()]
    assert any(r["to"] == "alice@spps.test" and r["subject"] == "New appointment request" for r in rows)
    blob = json.dumps(rows)
    assert "FICTIONAL-SENSITIVE-NOTE" not in blob and "Fictional Portal User" not in blob and "₹" not in blob
    pending = admin("SELECT count(*) FROM spps_identity_dev.platform_notifications WHERE delivery_status IN ('PENDING','SENDING')")
    assert pending[0][0] == 0


def test_backoff_then_success_and_dead_letter(app, integration_env):
    nid = admin("INSERT INTO spps_identity_dev.platform_notifications(account_id,kind,object_type,object_id,created_at) "
                "SELECT id,'booking.confirmed','booking',1,now() FROM spps_identity_dev.platform_accounts "
                "WHERE status='VERIFIED' ORDER BY id DESC LIMIT 1 RETURNING id")[0][0]

    def make_due():
        admin("UPDATE spps_identity_dev.platform_notifications SET next_attempt_at=now()-interval '1 second' "
              "WHERE id=:i", {"i": nid})

    def state():
        return admin("SELECT delivery_status, delivery_attempts, next_attempt_at>now() FROM "
                     "spps_identity_dev.platform_notifications WHERE id=:i", {"i": nid})[0]

    admin("UPDATE spps_identity_dev.platform_notifications SET delivery_status='DELIVERED' "
          "WHERE delivery_status IN ('PENDING','SENDING') AND id<>:i", {"i": nid})
    flaky = Flaky(fail_times=2)
    drain(app, flaky)
    assert state() == ("PENDING", 1, True)  # backed off, not due yet
    assert drain(app, flaky)["claimed"] == 0
    make_due(); drain(app, flaky)
    assert state()[:2] == ("PENDING", 2)
    make_due(); drain(app, flaky)
    assert state()[:2] == ("DELIVERED", 3) and len(flaky.sent) == 1

    dead = admin("INSERT INTO spps_identity_dev.platform_notifications(practitioner_id,kind,object_type,object_id,created_at) "
                 "VALUES (99999999,'booking.requested','booking',1,now()) RETURNING id")[0][0]  # no such practitioner
    for _ in range(6):
        admin("UPDATE spps_identity_dev.platform_notifications SET next_attempt_at=now()-interval '1 second' WHERE id=:i",
              {"i": dead})
        drain(app, Flaky(0))
    assert admin("SELECT delivery_status, delivery_attempts FROM spps_identity_dev.platform_notifications WHERE id=:i",
                 {"i": dead})[0] == ("FAILED", 6)


def test_concurrent_workers_never_double_send(app, integration_env):
    admin("UPDATE spps_identity_dev.platform_notifications SET delivery_status='DELIVERED' "
          "WHERE delivery_status IN ('PENDING','SENDING')")
    tag = 700000 + threading.get_ident() % 100000
    inserted = len(admin("INSERT INTO spps_identity_dev.platform_notifications(account_id,kind,object_type,object_id,created_at) "
                         "SELECT a.id,'programme.feedback','programme',:t,now() FROM spps_identity_dev.platform_accounts a "
                         "CROSS JOIN generate_series(1,40) WHERE a.status='VERIFIED' LIMIT 40 RETURNING id", {"t": tag}))
    assert inserted == 40  # self-contained: does not depend on accounts created by other tests
    sent, lock = [], threading.Lock()

    class Recorder:
        def send(self, message):
            with lock:
                sent.append(message.to)

    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        drain(app, Recorder(), limit=15)
        drain(app, Recorder(), limit=15)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    delivered = admin("SELECT count(*) FROM spps_identity_dev.platform_notifications WHERE object_id=:t "
                      "AND delivery_status='DELIVERED' AND delivered_at IS NOT NULL", {"t": tag})[0][0]
    assert len(sent) == delivered == inserted  # every message exactly once
    engine = create_engine(EPHEMERAL_TARGET.admin_url())
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM spps_identity_dev.platform_notifications "
                                     "WHERE object_id=:t AND delivery_attempts>1"), {"t": tag}).scalar() == 0
    finally:
        engine.dispose()
