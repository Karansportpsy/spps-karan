"""Booking-only directory, phase 2: request an appointment from a directory card (SPPS_DIRECTORY_BOOKING_ENABLED,
which also needs SPPS_PORTAL_ENABLED and SPPS_DIRECTORY_ENABLED).

Runs on the 13A booking path (server/bookings.py, identity migrations 0009/0018/0043), not on the v2 loop tables.

* The caller must hold an e-mail-verified portal session (the identity database refuses any other session).
* What the handlers read in the clinical schema is limited to the public read models: ``directory_snapshot`` (the
  practitioner must be listed) and ``directory_service_snapshot`` (the service must be active and theirs). Open
  times come from the same narrow, read-only slot read the 13A portal uses (bookings.available_slots). No clinical
  base table is read for a portal user, and nothing clinical is returned.
* The identity function creates the minimal PRACTITIONER_REVIEW link on a first request; it becomes ACTIVE only when
  the practitioner approves the booking. Under-18s are booked by a guardian with VERIFIED authority
  (booking_management); consents CASE_LINK, APPOINTMENT_BOOKING (and VIDEO_CONSULTATION for online) come first.
* Rate limits per IP and per account, plus the signed form token and honeypot (guards.check_bot).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import free_access
from ..portal_auth import sha256_hex
from . import guards
from .context import Ctx
from .directory import CLINICAL_WORDS, NOT_CRISIS, crisis_text
from .models import DirectoryServiceSnapshot, DirectorySnapshot


class DirectoryBookingIn(BaseModel):
    practitioner_id: int = Field(gt=0)
    service_id: int = Field(gt=0)
    slot_id: int = Field(gt=0)
    subject_person_id: int = Field(gt=0)
    note: str = Field(default="", max_length=300)
    form_token: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=200)  # honeypot: must stay empty


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session = ctx.db_session

    def portal_session(request: Request):
        # bookings/portal_auth register after the v2 groups, so resolve the dependency per request.
        current = getattr(request.app.state, "portal_current_session", None)
        if current is None:
            raise HTTPException(404, "Not found")
        return current(request)

    def listed(db, pid: int) -> DirectorySnapshot:
        s = db.scalar(select(DirectorySnapshot).where(DirectorySnapshot.practitioner_id == pid,
                                                      DirectorySnapshot.verified_until >= datetime.now(
                                                          timezone.utc).date().isoformat()))
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

    def call(name, **params):
        fn = getattr(app.state, "identity_call", None)
        if fn is None:
            raise HTTPException(404, "Not found")
        try:
            return fn(name, **params)
        except HTTPException as exc:
            # P0002 (404) from these two functions only ever means "this account may not book for that person".
            if exc.status_code == 403 or (exc.status_code == 404 and name.startswith("portal_")):
                raise HTTPException(403, "Only an adult account, or a guardian with verified authority for this "
                                         "athlete, can request this appointment") from None
            if exc.status_code == 409 and exc.detail == "A proposal is already open":
                raise HTTPException(409, "You already have a request waiting for this practitioner") from None
            raise

    @app.get("/api/portal/directory/{pid}/slots")
    def slots(pid: int, service_id: int, request: Request, session=Depends(portal_session), db=Depends(db_session)):
        guards.limit_ip(request, "directory-slots")
        snap = listed(db, pid)
        service = service_of(db, pid, service_id)
        rows = open_slots(snap, service)
        db.rollback()
        return {"practitioner_id": pid, "service_id": service.service_id,
                "slots": [{"slot_id": s.id, "starts_at": _utc(s.starts_at).isoformat(),
                           "duration_minutes": service.duration_minutes} for s in rows]}

    @app.post("/api/portal/directory-bookings", status_code=201)
    def request_booking(body: DirectoryBookingIn, request: Request, session=Depends(portal_session),
                        db=Depends(db_session)):
        guards.limit_ip(request, "directory-booking")
        guards.check_bot(body.form_token, body.website)
        guards.hit("directory-booking-account", str(session["account_id"]))
        if not free_access.enabled():
            raise HTTPException(409, "Appointment requests from the directory are available in the free release only")
        note = " ".join(body.note.split())
        if CLINICAL_WORDS.search(note):
            raise HTTPException(422, "Please keep the note to practical details and leave out health or clinical "
                                     "information; you can discuss that with the practitioner. " + NOT_CRISIS + " "
                                     + crisis_text())
        snap = listed(db, body.practitioner_id)
        service = service_of(db, body.practitioner_id, body.service_id)
        token = sha256_hex(session["token"])
        context = call("portal_directory_booking_context", p_token_hash=token, p_practitioner_id=snap.practitioner_id,
                       p_subject_person_id=body.subject_person_id)[0]
        if context.link_status != "ACTIVE" and not snap.taking_new_clients:
            raise HTTPException(409, "This practitioner is not taking new clients at the moment")
        slot = next((s for s in open_slots(snap, service) if s.id == body.slot_id), None)
        db.rollback()
        if slot is None:
            raise HTTPException(409, "That time is no longer available")
        row = call("portal_request_directory_booking", p_token_hash=token, p_practitioner_id=snap.practitioner_id,
                   p_tenant_id=snap.tenant_id, p_subject_person_id=body.subject_person_id, p_slot_id=slot.id,
                   p_starts_at=_utc(slot.starts_at), p_duration_minutes=service.duration_minutes,
                   p_video=service.mode == "online", p_note=note, p_service_id=service.service_id,
                   p_service_name=service.name)[0]
        return {"booking_id": row.booking_id, "status": "PENDING_APPROVAL", "service": service.name,
                "starts_at": _utc(slot.starts_at).isoformat(), "duration_minutes": service.duration_minutes,
                "message": "Request sent. The practitioner sees your display name, the service, the time and your "
                           "note, and approves or declines it. No payment is required.",
                "notice": NOT_CRISIS + " " + crisis_text()}
