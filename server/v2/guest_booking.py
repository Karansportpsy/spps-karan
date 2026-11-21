"""Guest booking: ask for an appointment from the public directory without an account (SPPS_GUEST_BOOKING_ENABLED, which
also needs the portal, the directory and directory booking).

Flow (identity migration 0047):
  1. POST /api/v2/public/guest-bookings          stores the request and e-mails a one-time confirmation link.
  2. POST /api/v2/public/guest-bookings/confirm  (the link was opened and the button pressed) creates the account shell,
                                                 records the consents and creates the usual PENDING_APPROVAL booking.
  3. The practitioner approves in Appointments. The delivery worker then e-mails the approval together with a link to
     create the account (server/notifications.py, ``claim_link``).
  4. POST /api/v2/public/guest-claim             the person chooses a password; from then on it is a normal account.

Rules: every outcome of step 1 looks the same to the caller, so the form never shows whether an address already has an
account. A visitor can book only through an address they can read. The note carries practical details only. Nothing
clinical is read: the handlers use the public directory snapshots and the narrow open-slot read, like the signed-in flow.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .. import free_access
from ..portal_auth import password_problem, sha256_hex
from ..portal_guardian import CURRENT_POLICY_VERSION
from ..security import make_password
from . import guards
from .context import Ctx
from .directory import CLINICAL_WORDS, NOT_CRISIS, crisis_text
from .models import DirectoryServiceSnapshot, DirectorySnapshot
from sqlalchemy import select

guards.LIMITS.setdefault("guest-booking", (10, 3600))
guards.LIMITS.setdefault("guest-booking-email", (3, 3600))
guards.LIMITS.setdefault("guest-confirm", (30, 3600))
guards.LIMITS.setdefault("guest-claim", (20, 3600))

CONFIRM_TTL_SECONDS = 24 * 3600
ACCEPTED = {"status": "accepted",
            "message": "Request submitted. Check your mail: we have sent a message to the address you gave. Open the "
                       "link in it to send your request to the practitioner."}


class GuestBookingIn(BaseModel):
    practitioner_id: int = Field(gt=0)
    service_id: int = Field(gt=0)
    slot_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=6, max_length=250)
    note: str = Field(default="", max_length=300)
    adult_attested: bool
    consent_accepted: bool
    form_token: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=200)  # honeypot: must stay empty


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ClaimIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=1, max_length=200)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, tenant_public = ctx.db_session, ctx.tenant_public

    def engine():
        e = getattr(app.state, "portal_auth_engine", None)
        if e is None:
            raise HTTPException(404, "Not found")
        return e

    def fn(name: str, **params):
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine().begin() as conn:
                return conn.execute(text(f"SELECT * FROM spps_identity_dev.{name}({placeholders})"),
                                    params).fetchall()
        except DBAPIError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", "") == "22023":
                raise HTTPException(422, "Please check the details and try again") from None
            raise

    def listed(db, pid: int) -> DirectorySnapshot:
        s = db.scalar(select(DirectorySnapshot).where(
            DirectorySnapshot.tenant_id == tenant_public(db).id, DirectorySnapshot.practitioner_id == pid,
            DirectorySnapshot.verified_until >= datetime.now(timezone.utc).date().isoformat()))
        if not s:
            raise HTTPException(404, "Practitioner not found")
        return s

    def service_of(db, pid: int, sid: int) -> DirectoryServiceSnapshot:
        s = db.scalar(select(DirectoryServiceSnapshot).where(DirectoryServiceSnapshot.service_id == sid,
                                                             DirectoryServiceSnapshot.practitioner_id == pid))
        if not s:
            raise HTTPException(404, "Service not found")
        return s

    def open_slots(snap: DirectorySnapshot, service: DirectoryServiceSnapshot):
        available = getattr(app.state, "portal_available_slots", None)
        if available is None:
            raise HTTPException(404, "Not found")
        return [s for s in available(snap.practitioner_id, snap.tenant_id)
                if s.duration_minutes >= service.duration_minutes]

    # ---------------------------------------------------------------- open times (public)
    @app.get("/api/v2/public/directory/{pid}/slots")
    def slots(pid: int, service_id: int, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "directory-slots")
        snap = listed(db, pid)
        service = service_of(db, pid, service_id)
        rows = open_slots(snap, service)
        db.rollback()
        return {"practitioner_id": pid, "service_id": service.service_id,
                "slots": [{"slot_id": s.id, "starts_at": _utc(s.starts_at).isoformat(),
                           "duration_minutes": service.duration_minutes} for s in rows]}

    # ---------------------------------------------------------------- 1. request
    @app.post("/api/v2/public/guest-bookings", status_code=202)
    def request_booking(body: GuestBookingIn, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "guest-booking")
        guards.check_bot(body.form_token, body.website)
        email = body.email.strip().lower()
        if not free_access.enabled():
            raise HTTPException(409, "Appointment requests from the directory are available in the free release only")
        if not body.adult_attested:
            raise HTTPException(422, "Booking here is for adults (18+). A parent or guardian can book for a minor "
                                     "after creating an account.")
        if not body.consent_accepted:
            raise HTTPException(422, "Please accept the terms, the privacy notice and the booking consent")
        note = " ".join(body.note.split())
        if CLINICAL_WORDS.search(note):
            raise HTTPException(422, "Please keep the note to practical details and leave out health or clinical "
                                     "information; you can discuss that with the practitioner. " + NOT_CRISIS + " "
                                     + crisis_text())
        snap = listed(db, body.practitioner_id)
        service = service_of(db, body.practitioner_id, body.service_id)
        if not snap.taking_new_clients:
            raise HTTPException(409, "This practitioner is not taking new clients at the moment")
        slot = next((s for s in open_slots(snap, service) if s.id == body.slot_id), None)
        db.rollback()
        if slot is None:
            raise HTTPException(409, "That time is no longer available")
        guards.hit("guest-booking-email", guards.email_key(email))  # counts only requests that are otherwise valid
        token = secrets.token_urlsafe(32)
        outcome = fn("guest_request_create", p_email=email, p_name=body.name.strip(),
                     p_practitioner_id=snap.practitioner_id, p_tenant_id=snap.tenant_id, p_slot_id=slot.id,
                     p_starts_at=_utc(slot.starts_at), p_duration_minutes=service.duration_minutes,
                     p_video=service.mode == "online", p_note=note, p_service_id=service.service_id,
                     p_service_name=service.name, p_token_hash=sha256_hex(token),
                     p_ttl_seconds=CONFIRM_TTL_SECONDS)[0][0]
        outbox = app.state.portal_outbox
        try:
            if outcome == "CREATED":
                outbox.send("guest-confirm", email, token)
            elif outcome == "EXISTING_ACCOUNT":
                outbox.send("existing-account-notice", email)
        except Exception:  # noqa: BLE001 — the provider's error text is not shown to a visitor
            raise HTTPException(503, "We could not send the e-mail just now. Please try again in a few minutes.") \
                from None
        # CREATED, EXISTING_ACCOUNT and LIMITED all answer the same way.
        return {**ACCEPTED, "notice": NOT_CRISIS + " " + crisis_text()}

    # ---------------------------------------------------------------- 2. confirm
    @app.post("/api/v2/public/guest-bookings/confirm")
    def confirm(body: TokenIn, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "guest-confirm")
        if not free_access.enabled():
            raise HTTPException(409, "Appointment requests from the directory are available in the free release only")
        token_hash = sha256_hex(body.token)
        peek = fn("guest_request_peek", p_token_hash=token_hash)
        if not peek:
            raise HTTPException(410, "This link is not valid any more. Please request the appointment again.")
        want = peek[0]
        snap = listed(db, want.practitioner_id)
        service = service_of(db, want.practitioner_id, want.service_id)
        slot = next((s for s in open_slots(snap, service) if s.id == want.slot_id), None)
        db.rollback()
        if slot is None:
            raise HTTPException(409, "That time was taken before you confirmed. Please choose another time.")
        row = fn("guest_request_confirm", p_token_hash=token_hash, p_policy_version=CURRENT_POLICY_VERSION)[0]
        if row.outcome == "OK":
            return {"status": "submitted",
                    "message": "Your request has been sent to the practitioner. When they approve it, you will get "
                               "an e-mail with a link to create your account and see your appointment.",
                    "notice": NOT_CRISIS + " " + crisis_text()}
        messages = {
            "INVALID": (410, "This link is not valid any more. Please request the appointment again."),
            "TOO_SOON": (409, "That time is too close now. Please choose another time."),
            "SLOT_TAKEN": (409, "That time was taken before you confirmed. Please choose another time."),
            "ALREADY": (409, "You already have a request waiting for this practitioner."),
            "EXISTING_ACCOUNT": (409, "This address already has an SPPS account. Sign in and request the "
                                      "appointment from your account."),
        }
        raise HTTPException(*messages.get(row.outcome, (409, "This request could not be completed")))

    # ---------------------------------------------------------------- 4. claim
    @app.post("/api/v2/public/guest-claim")
    def claim(body: ClaimIn, request: Request):
        guards.limit_ip(request, "guest-claim")
        problem = password_problem(body.password, "")
        if problem:
            raise HTTPException(422, problem)
        ok = fn("guest_claim_account", p_token_hash=sha256_hex(body.token),
                p_new_hash=make_password(body.password))[0][0]
        if not ok:
            raise HTTPException(410, "This link is not valid any more. Use \"Forgot password\" on the sign-in page "
                                     "to set a password for the address you booked with.")
        return {"status": "claimed", "message": "Your account is ready. Sign in to see your appointment."}
