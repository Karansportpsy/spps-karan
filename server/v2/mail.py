"""Review e-mail delivery with a persistent delivery log.

The v1 sender (supabase_practitioner_auth.send_review_email) is kept as the single place that builds and sends the
message. This wrapper records every attempt in ``email_deliveries`` so a failure is visible to the owner and can be
resent. Only the failure *class* is stored: never the address, subject, body or the SMTP server's reply.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select

from .. import supabase_practitioner_auth as sbauth
from ..models import now
from .models import EmailDelivery

LOG = logging.getLogger("spps.mail")
KINDS = {"approved", "rejected", "request_more_info"}


def configured() -> bool:
    return all(os.getenv(k, "") for k in ("SPPS_SMTP_HOST", "SPPS_SMTP_USERNAME", "SPPS_SMTP_PASSWORD", "SPPS_SMTP_FROM"))


def _send(destination: str, name: str, kind: str, note: str) -> tuple[bool, str]:
    if not configured():
        return False, "mail_not_configured"
    ok = sbauth.send_review_email(destination, name, kind, note)
    return ok, "" if ok else "smtp_error"


def deliver_review(db, *, tenant_id: int, actor_id: int | None, person, kind: str, note: str) -> EmailDelivery:
    """Send one review e-mail and record the outcome. Older failed rows of the same kind are superseded."""
    if kind not in KINDS:
        raise ValueError("unknown review e-mail kind")
    for old in db.scalars(select(EmailDelivery).where(EmailDelivery.practitioner_id == person.id,
                                                      EmailDelivery.status == "failed")):
        old.status = "superseded"
    row = EmailDelivery(tenant_id=tenant_id, practitioner_id=person.id, kind=kind, created_by=actor_id,
                        attempts=0, status="failed", failure_reason="")
    db.add(row)
    db.flush()  # the id is logged with any failure
    _attempt(row, person, note)
    db.commit()
    return row


def _attempt(row: EmailDelivery, person, note: str) -> None:
    ok, reason = _send(person.email, person.name, row.kind, note)
    row.attempts += 1
    row.last_attempt_at = now()
    row.status = "sent" if ok else "failed"
    row.failure_reason = reason
    if not ok:
        LOG.warning("review e-mail not delivered delivery_id=%s kind=%s reason=%s", row.id, row.kind, reason)


def resend(db, row: EmailDelivery, person, note: str) -> EmailDelivery:
    if row.status != "failed":
        raise ValueError("only a failed delivery can be resent")
    _attempt(row, person, note)
    db.commit()
    return row
