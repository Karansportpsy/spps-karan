"""P1 — notification delivery worker for the platform outbox (migration 0013).

Messages are deliberately content-free: they name the kind of update and ask the
recipient to sign in. No clinical text, names of other people, amounts or tokens are
ever placed in e-mail. Providers:
  * FileDeliveryProvider — local/CI only, appends JSON lines.
  * SmtpDeliveryProvider — any SMTP relay, STARTTLS/SMTPS required, credentials from env.
Selection: SPPS_NOTIFY_PROVIDER=file|smtp (file refused outside local mode).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import text

SUBJECTS = {
    "booking.requested": "New appointment request",
    "booking.approved_payment_due": "Your appointment was approved",
    "booking.confirmed": "Appointment confirmed",
    "booking.declined": "Appointment request update",
    "booking.expired": "Appointment request expired",
    "booking.reschedule_proposed": "A new appointment time was proposed",
    "booking.refunded": "Refund issued",
    "booking.reminder": "Appointment reminder",
    "booking.reschedule_declined": "Appointment time update",
    "programme.assigned": "You have a new programme",
    "programme.feedback": "New feedback from your practitioner",
    "summary.released": "Your practitioner shared an update",
}
GENERIC_SUBJECT = "You have an update in SPPS"


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


def render(kind: str, portal_url: str) -> tuple[str, str]:
    subject = SUBJECTS.get(kind, GENERIC_SUBJECT)
    body = (f"{subject}.\n\nFor your privacy this message contains no details. "
            f"Sign in to SPPS to view it: {portal_url}\n\n"
            "If you did not expect this message, you can ignore it. SPPS is not an emergency service.")
    return subject, body


class FileDeliveryProvider:
    name = "file"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def send(self, message: Message) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"to": message.to, "subject": message.subject, "body": message.body,
                                      "at": datetime.now(timezone.utc).isoformat()}) + "\n")


class SmtpDeliveryProvider:
    name = "smtp"

    def __init__(self, host: str, port: int, username: str, password: str, sender: str, implicit_tls: bool):
        if not host or not sender:
            raise RuntimeError("SMTP host and sender are required")
        self.host, self.port, self.username, self.password = host, port, username, password
        self.sender, self.implicit_tls = sender, implicit_tls

    def send(self, message: Message) -> None:
        email = EmailMessage()
        email["From"], email["To"], email["Subject"] = self.sender, message.to, message.subject
        email.set_content(message.body)
        context = ssl.create_default_context()
        if self.implicit_tls:
            with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=20) as smtp:
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                smtp.starttls(context=context)  # never send in cleartext
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(email)


def provider_from_env(mode: str):
    kind = os.getenv("SPPS_NOTIFY_PROVIDER", "file" if mode == "local" else "smtp")
    if kind == "file":
        if mode != "local":
            raise RuntimeError("File notification delivery is local-only")
        return FileDeliveryProvider(Path(os.getenv("SPPS_NOTIFY_OUTBOX_FILE", "output/notifications.jsonl")))
    if kind == "smtp":
        return SmtpDeliveryProvider(os.getenv("SPPS_SMTP_HOST", ""), int(os.getenv("SPPS_SMTP_PORT", "587")),
                                    os.getenv("SPPS_SMTP_USERNAME", ""), os.getenv("SPPS_SMTP_PASSWORD", ""),
                                    os.getenv("SPPS_SMTP_SENDER") or os.getenv("SPPS_SMTP_FROM", ""),
                                    os.getenv("SPPS_SMTP_IMPLICIT_TLS") == "true")
    raise RuntimeError("Unknown notification provider")


CLAIM_TTL_SECONDS = 14 * 24 * 3600


def render_claim(portal_url: str, token: str) -> tuple[str, str]:
    """The approval message for a person who booked as a guest: it carries the one-time create-account link."""
    from urllib.parse import quote
    subject = "Your appointment is confirmed - create your account"
    body = (f"Your appointment is confirmed.\n\nCreate your SPPS account to see the details, the video link and any "
            f"reminders:\n{portal_url}?claim={quote(token, safe='')}\n\nThe link works once and expires in 14 days. "
            "For your privacy this message contains no other details. If you did not expect it, you can ignore it. "
            "SPPS is not an emergency service.")
    return subject, body


def deliver_pending(identity_engine, provider, practitioner_email, *, portal_url: str, workspace_url: str,
                    limit: int = 50, lease_seconds: int = 120, push=None, guest_claims: bool = False) -> dict:
    """One worker pass. `practitioner_email(pid)` resolves practitioner recipients from the clinical database (auth
    role). `push` is an optional server.push_delivery.PushSender: registered devices get a content-free push too.
    A notification counts as delivered when any channel succeeded; if every attempted channel failed it is retried
    with backoff. Returns counts by outcome."""
    with identity_engine.begin() as conn:
        claimed = conn.execute(text("SELECT * FROM spps_identity_dev.notification_claim(:l,:s)"),
                               {"l": limit, "s": lease_seconds}).fetchall()
    outcome = {"DELIVERED": 0, "PENDING": 0, "FAILED": 0, "claimed": len(claimed), "pushed": 0}
    for row in claimed:
        to = row.account_email or (practitioner_email(row.practitioner_id) if row.practitioner_id else None)
        url = workspace_url if row.practitioner_id else portal_url
        delivered = False
        if to and provider is not None:
            subject, body = render(row.kind, url)
            if guest_claims and row.kind == "booking.confirmed" and not row.practitioner_id:
                # A guest has no account yet: the approval e-mail carries the one-time create-account link.
                claim = secrets.token_urlsafe(32)
                try:
                    with identity_engine.begin() as conn:
                        issued = conn.execute(text("SELECT spps_identity_dev.guest_claim_issue(:n,:h,:t)"),
                                              {"n": row.notification_id,
                                               "h": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
                                               "t": CLAIM_TTL_SECONDS}).scalar()
                except Exception:  # noqa: BLE001 — the ordinary message still goes out
                    issued = False
                if issued:
                    subject, body = render_claim(url, claim)
            try:
                provider.send(Message(to, subject, body))
                delivered = True
            except Exception:  # noqa: BLE001 — any provider failure is retried with backoff
                pass
        if push is not None:
            from .push_delivery import payload_for
            with identity_engine.begin() as conn:
                targets = conn.execute(text("SELECT * FROM spps_identity_dev.notification_push_targets(:i)"),
                                       {"i": row.notification_id}).fetchall()
            payload = payload_for(row.kind, url)
            for t in targets:
                result = push.send(t.platform, t.subscription, payload)
                if result == "ok":
                    delivered = True
                    outcome["pushed"] += 1
                elif result == "dead":
                    with identity_engine.begin() as conn:
                        conn.execute(text("SELECT spps_identity_dev.notification_device_dead(:d)"),
                                     {"d": t.device_id})
        with identity_engine.begin() as conn:
            status = conn.execute(text("SELECT spps_identity_dev.notification_result(:i,:d)"),
                                  {"i": row.notification_id, "d": delivered}).scalar()
        outcome[status] = outcome.get(status, 0) + 1
    return outcome
