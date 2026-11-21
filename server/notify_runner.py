"""Phase 4 — in-process delivery loop (SPPS_NOTIFY_WORKER_ENABLED).

Render runs the API as a single web service, so the delivery loop runs inside it as a daemon thread: every
``SPPS_NOTIFY_INTERVAL_SECONDS`` (default 30) it queues due appointment reminders and delivers the outbox
(e-mail, Web Push and Android push). It is safe next to ``tools/notify_worker.py`` or a second instance: queued rows are
claimed with a lease and reminders are guarded by a ledger row, so nothing is sent twice for the same appointment.
Errors never stop the loop and never carry message text into the logs.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from sqlalchemy import select, text

log = logging.getLogger("spps.notify")


def run_once(app, *, push, reminder_minutes: int = 60) -> dict:
    from .notifications import deliver_pending, provider_from_env
    engine = app.state.portal_auth_engine
    base = os.getenv("SPPS_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    with engine.begin() as conn:
        queued = conn.execute(text("SELECT spps_identity_dev.notification_enqueue_due_reminders(:m)"),
                              {"m": reminder_minutes}).scalar()
    provider = None
    try:
        provider = provider_from_env(os.getenv("MODEL_C_ENV", "local").lower())
    except Exception:  # noqa: BLE001 — push can still go out without e-mail
        pass

    def practitioner_email(pid):
        from .models import Practitioner
        auth_engine = getattr(app.state, "auth_engine", None)
        if auth_engine is None:
            return None
        from sqlalchemy.orm import Session
        with Session(auth_engine) as db:
            return db.scalar(select(Practitioner.email).where(Practitioner.id == pid))

    result = deliver_pending(engine, provider, practitioner_email, portal_url=base + "/portal",
                             workspace_url=base + "/studio", push=push)
    result["reminders_queued"] = queued
    return result


def start(app, *, push_on: bool) -> threading.Thread | None:
    interval = max(10, int(os.getenv("SPPS_NOTIFY_INTERVAL_SECONDS", "30")))
    reminder_minutes = int(os.getenv("SPPS_REMINDER_MINUTES", "60"))
    push = None
    if push_on:
        from .push_delivery import PushSender
        push = PushSender()
    stop = threading.Event()
    app.state.notify_stop = stop

    def loop():
        while not stop.is_set():
            try:
                result = run_once(app, push=push, reminder_minutes=reminder_minutes)
                if result.get("claimed") or result.get("reminders_queued"):
                    log.info(json.dumps({"event": "notify_pass", **result}))
            except Exception as exc:  # noqa: BLE001
                log.warning(json.dumps({"event": "notify_pass_failed", "error": type(exc).__name__}))
            stop.wait(interval)

    thread = threading.Thread(target=loop, name="spps-notify", daemon=True)
    thread.start()
    return thread
