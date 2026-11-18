"""Stage 13B — approval-first bookings for linked athletes and guardians.

State lives in the platform (identity) database (migration 0009); practitioner
slots stay in the clinical database. Reading a practitioner's slots on behalf of a
portal user is a deliberate, narrow server step: the identity database first
proves an ACTIVE link + booking_management authority (portal_booking_context),
then the clinical read runs under that practitioner's RLS identity, read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from .identity import bind_identity
from .models import AppointmentSlot, Booking
from .payments import PaymentError, active_provider_name, load_providers
from . import free_access
from .portal_auth import sha256_hex

LEGACY_HOLDING = ("awaiting_payment", "paid_pending_review", "approved", "completed", "refund_required")
ERRORS = {"28000": (401, "Session expired"), "42501": (403, "Not permitted"), "P0002": (404, "Not found"),
          "P0003": (409, "Record consent for this appointment type first"),
          "P0004": (409, "This booking cannot change in its current state"),
          "P0005": (409, "Payment does not match this booking"),
          "23P01": (409, "That time is no longer available"), "23505": (409, "A proposal is already open"),
          "22023": (422, "Invalid details")}


class BookingIn(BaseModel):
    link_id: int = Field(gt=0)
    slot_id: int = Field(gt=0)
    video_requested: bool = False
    note: str = Field(default="", max_length=300)


class CheckoutIn(BaseModel):
    order_id: str = Field(min_length=6, max_length=100)


class VerifyIn(BaseModel):
    order_id: str = Field(min_length=6, max_length=100)
    payment_id: str = Field(min_length=6, max_length=100)
    signature: str = Field(min_length=10, max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=240)


class RescheduleResponseIn(BaseModel):
    accept: bool


class DecisionIn(BaseModel):
    decision: Literal["approve", "decline"]
    reason: str = Field(default="", max_length=240)
    payment_window_hours: int = Field(default=24, ge=1, le=72)


class ProposeIn(BaseModel):
    slot_id: int = Field(gt=0)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def register_booking_routes(app: FastAPI, *, mode: str, db_session, get_current, event) -> None:
    engine = app.state.portal_auth_engine
    current_session = app.state.portal_current_session
    providers = {} if free_access.enabled() else load_providers(mode)
    if not free_access.enabled():
        provider_name = active_provider_name(mode)
        if provider_name not in providers:
            raise RuntimeError(f"Payment provider '{provider_name}' is not configured")
    app.state.payment_providers = providers

    def call(name, **params):
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine.begin() as conn:
                return conn.execute(text(f"SELECT * FROM spps_identity_dev.{name}({placeholders})"),
                                    params).fetchall()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", "")
            if code in ERRORS:
                raise HTTPException(*ERRORS[code]) from None
            raise

    def tok(session):
        return sha256_hex(session["token"])

    def iso(v):
        return v.isoformat() if v else None

    def held_slots(practitioner_id: int) -> dict[int, str]:
        return {r.slot_id: r.status for r in call("practitioner_held_slots", p_practitioner_id=practitioner_id)}

    app.state.platform_held_slots = held_slots  # used by /api/slots to show platform holds

    def available_slots(practitioner_id: int, tenant_id: int) -> list[AppointmentSlot]:
        held = held_slots(practitioner_id)
        with app.state.DB() as db:
            bind_identity(db, SimpleNamespace(id=practitioner_id, tenant_id=tenant_id))
            slots = db.scalars(select(AppointmentSlot).where(
                AppointmentSlot.practitioner_id == practitioner_id, AppointmentSlot.available == True)  # noqa: E712
                .order_by(AppointmentSlot.starts_at)).all()
            legacy = set(db.scalars(select(Booking.slot_id).where(
                Booking.practitioner_id == practitioner_id, Booking.status.in_(LEGACY_HOLDING))).all())
            # Plain snapshots: nothing ORM-bound leaves this read-only session.
            slots = [SimpleNamespace(id=s.id, starts_at=s.starts_at, duration_minutes=s.duration_minutes,
                                     amount_paise=s.amount_paise, currency=s.currency) for s in slots]
            db.rollback()
        floor = datetime.now(timezone.utc).timestamp() + 2 * 3600
        return [s for s in slots if s.id not in held and s.id not in legacy and _utc(s.starts_at).timestamp() > floor]

    def slot_view(s):
        return {"slot_id": s.id, "starts_at": iso(_utc(s.starts_at)), "duration_minutes": s.duration_minutes,
                "amount_paise": 0 if free_access.enabled() else s.amount_paise, "currency": s.currency}

    def process_refunds() -> int:
        if free_access.enabled():return 0
        done = 0
        for r in call("booking_refunds_due"):
            adapter = providers.get(r.provider)
            if adapter is None:
                continue
            try:
                refund_id = adapter.refund(r.provider_payment_id, r.amount_paise)
            except PaymentError:
                continue  # stays REFUND_PENDING; reconciled on the next attempt
            if call("booking_mark_refunded", p_provider_payment_id=r.provider_payment_id, p_refund_id=refund_id)[0][0]:
                done += 1
        return done

    app.state.process_refunds = process_refunds

    def record_capture(adapter, verified):
        row = call("booking_record_capture", p_provider=adapter.name, p_order_id=verified.order_id,
                   p_payment_id=verified.payment_id, p_amount_paise=verified.amount_paise)[0]
        if row.outcome == "REFUND_REQUIRED":
            process_refunds()
        return row

    # ------------------------------------------------------------ portal
    @app.get("/api/portal/links/{link_id}/slots")
    def portal_slots(link_id: int, session=Depends(current_session)):
        ctx = call("portal_booking_context", p_token_hash=tok(session), p_link_id=link_id)[0]
        return [slot_view(s) for s in available_slots(ctx.practitioner_id, ctx.tenant_id)]

    @app.post("/api/portal/bookings", status_code=201)
    def portal_book(payload: BookingIn, session=Depends(current_session)):
        ctx = call("portal_booking_context", p_token_hash=tok(session), p_link_id=payload.link_id)[0]
        slot = next((s for s in available_slots(ctx.practitioner_id, ctx.tenant_id) if s.id == payload.slot_id), None)
        if slot is None:
            raise HTTPException(409, "That time is no longer available")
        booking = call("free_portal_request_booking" if free_access.enabled() else "portal_request_booking", p_token_hash=tok(session), p_link_id=payload.link_id,
                       p_slot_id=slot.id, p_starts_at=_utc(slot.starts_at), p_duration_minutes=slot.duration_minutes,
                       p_amount_paise=0 if free_access.enabled() else slot.amount_paise, p_video=payload.video_requested, p_note=payload.note)[0][0]
        return {"booking_id": booking, "status": "PENDING_APPROVAL",
                "message": "Request sent for practitioner approval. No payment is required." if free_access.enabled() else "Request sent. Payment is only requested after your practitioner approves."}

    @app.get("/api/portal/bookings")
    def portal_bookings(session=Depends(current_session)):
        return [{"booking_id": r.booking_id, "practitioner_id": r.practitioner_id, "person_id": r.person_id,
                 "subject_display_name": r.subject_display_name, "starts_at": iso(r.starts_at), "ends_at": iso(r.ends_at),
                 "amount_paise": r.amount_paise, "currency": r.currency, "status": r.status,
                 "video_requested": r.video_requested, "action_deadline": iso(r.action_deadline),
                 "decision_reason": r.decision_reason, "rescheduled_from_id": r.rescheduled_from_id,
                 "can_manage": r.can_manage, "paid": r.paid,
                 "video_closed": bool(getattr(r, "video_closed", False))}
                for r in call("portal_list_bookings", p_token_hash=tok(session))]

    @app.post("/api/portal/bookings/{booking_id}/pay")
    def portal_pay(booking_id: int, session=Depends(current_session)):
        if free_access.enabled():raise HTTPException(410, "Payment is disabled in SPPS Version 1.0 - Free")
        target = call("portal_payment_target", p_token_hash=tok(session), p_booking_id=booking_id)[0]
        adapter = providers[target.provider or provider_name]
        order_id = target.provider_order_id
        if not order_id:
            try:
                created = adapter.create_order(target.amount_paise, f"spps-booking-{booking_id}")
            except PaymentError as exc:
                raise HTTPException(502, str(exc)) from None
            order_id = call("booking_attach_order", p_booking_id=booking_id, p_provider=adapter.name,
                            p_order_id=created)[0][0]
        return {"booking_id": booking_id, "order_id": order_id, "amount_paise": target.amount_paise,
                "currency": target.currency, "pay_by": iso(target.action_deadline), **adapter.public_config()}

    @app.post("/api/portal/bookings/{booking_id}/verify-payment")
    def portal_verify(booking_id: int, payload: VerifyIn, session=Depends(current_session)):
        if free_access.enabled():raise HTTPException(410, "Payment is disabled in SPPS Version 1.0 - Free")
        rows = [r for r in call("portal_list_bookings", p_token_hash=tok(session)) if r.booking_id == booking_id]
        if not rows or not rows[0].can_manage:
            raise HTTPException(404, "Not found")
        adapter = providers.get("razorpay")
        if adapter is None:
            raise HTTPException(409, "Payment provider unavailable")
        try:
            verified = adapter.verify_checkout(payload.order_id, payload.payment_id, payload.signature,
                                               rows[0].amount_paise)
        except PaymentError as exc:
            raise HTTPException(403, str(exc)) from None
        row = record_capture(adapter, verified)
        if row.booking_id != booking_id:
            raise HTTPException(409, "Payment belongs to a different booking")
        return {"booking_id": booking_id, "outcome": row.outcome}

    @app.post("/api/payments/webhook/{provider}")
    async def payment_webhook(provider: str, request: Request):
        if free_access.enabled():raise HTTPException(410, "Payment webhooks are disabled in SPPS Version 1.0 - Free")
        adapter = providers.get(provider)
        if adapter is None:
            raise HTTPException(404, "Unknown provider")
        body = await request.body()
        if len(body) > 50000:
            raise HTTPException(413, "Webhook too large")
        signature = request.headers.get("x-razorpay-signature")
        try:
            verified = adapter.verify_webhook(body, signature or "")
        except (PaymentError, ValueError, KeyError) as exc:
            raise HTTPException(403, str(exc) or "Invalid webhook") from None
        if verified is None:
            return {"ignored": True}
        return {"accepted": True, "outcome": record_capture(adapter, verified).outcome}

    @app.post("/api/portal/bookings/{booking_id}/cancel")
    def portal_cancel(booking_id: int, payload: ReasonIn, session=Depends(current_session)):
        status = call("portal_cancel_booking", p_token_hash=tok(session), p_booking_id=booking_id,
                      p_reason=payload.reason or "Cancelled by client")[0][0]
        if status == "REFUND_PENDING":
            process_refunds()
        return {"booking_id": booking_id, "status": status}

    @app.post("/api/portal/bookings/{booking_id}/reschedule-response")
    def portal_reschedule(booking_id: int, payload: RescheduleResponseIn, session=Depends(current_session)):
        status = call("free_portal_respond_reschedule" if free_access.enabled() else "portal_respond_reschedule", p_token_hash=tok(session), p_proposal_id=booking_id,
                      p_accept=payload.accept)[0][0]
        return {"booking_id": booking_id, "status": status}

    @app.get("/api/portal/notifications")
    def portal_notifications(mark_read: bool = False, session=Depends(current_session)):
        return [{"id": r.notification_id, "kind": r.kind, "object_type": r.object_type, "object_id": r.object_id,
                 "created_at": iso(r.created_at), "read": r.read}
                for r in call("portal_notifications", p_token_hash=tok(session), p_mark_read=mark_read)]

    # ------------------------------------------------------------ practitioner
    @app.get("/api/booking-requests")
    def practitioner_bookings(user=Depends(get_current)):
        return [{"booking_id": r.booking_id, "slot_id": r.slot_id, "starts_at": iso(r.starts_at), "ends_at": iso(r.ends_at),
                 "amount_paise": r.amount_paise, "status": r.status, "subject_display_name": r.subject_display_name,
                 "via_guardian": r.via_guardian, "video_requested": r.video_requested, "client_note": r.client_note,
                 "action_deadline": iso(r.action_deadline), "rescheduled_from_id": r.rescheduled_from_id,
                 "paid": r.paid, "payment_status": r.payment_status, "practitioner_paise": r.practitioner_paise,
                 "platform_paise": r.platform_paise,
                 "video_closed": bool(getattr(r, "video_closed", False))}
                for r in call("practitioner_list_bookings", p_practitioner_id=user.id)]

    @app.post("/api/booking-requests/{booking_id}/decision")
    def practitioner_decide(booking_id: int, payload: DecisionIn, user=Depends(get_current), db=Depends(db_session)):
        status = call("free_practitioner_decide_booking" if free_access.enabled() else "practitioner_decide_booking", p_practitioner_id=user.id, p_booking_id=booking_id,
                      p_decision=payload.decision.upper(), p_reason=payload.reason,
                      p_payment_window_hours=payload.payment_window_hours)[0][0]
        event(db, user, f"platform_booking.{payload.decision}", "platform_booking", booking_id)
        db.commit()
        return {"booking_id": booking_id, "status": status}

    @app.post("/api/booking-requests/{booking_id}/reschedule", status_code=201)
    def practitioner_reschedule(booking_id: int, payload: ProposeIn, user=Depends(get_current), db=Depends(db_session)):
        slot = db.scalar(select(AppointmentSlot).where(AppointmentSlot.id == payload.slot_id,
                                                       AppointmentSlot.practitioner_id == user.id,
                                                       AppointmentSlot.available == True))  # noqa: E712
        if slot is None or slot.id in held_slots(user.id):
            raise HTTPException(409, "That time is no longer available")
        proposal = call("free_practitioner_propose_reschedule" if free_access.enabled() else "practitioner_propose_reschedule", p_practitioner_id=user.id, p_booking_id=booking_id,
                        p_slot_id=slot.id, p_starts_at=_utc(slot.starts_at), p_duration_minutes=slot.duration_minutes,
                        p_amount_paise=0 if free_access.enabled() else slot.amount_paise)[0][0]
        event(db, user, "platform_booking.reschedule_proposed", "platform_booking", booking_id)
        db.commit()
        return {"proposal_booking_id": proposal, "status": "RESCHEDULE_PROPOSED"}

    @app.post("/api/booking-requests/{booking_id}/cancel")
    def practitioner_cancel(booking_id: int, payload: ReasonIn, user=Depends(get_current), db=Depends(db_session)):
        status = call("practitioner_cancel_booking", p_practitioner_id=user.id, p_booking_id=booking_id,
                      p_reason=payload.reason)[0][0]
        event(db, user, "platform_booking.cancelled", "platform_booking", booking_id)
        db.commit()
        if status == "REFUND_PENDING":
            process_refunds()
        return {"booking_id": booking_id, "status": status}

    @app.post("/api/booking-requests/{booking_id}/complete")
    def practitioner_complete(booking_id: int, user=Depends(get_current), db=Depends(db_session)):
        status = call("practitioner_complete_booking", p_practitioner_id=user.id, p_booking_id=booking_id)[0][0]
        event(db, user, "platform_booking.completed", "platform_booking", booking_id)
        db.commit()
        return {"booking_id": booking_id, "status": status}

    @app.get("/api/practitioner/notifications")
    def practitioner_notifications(mark_read: bool = False, user=Depends(get_current)):
        return [{"id": r.notification_id, "kind": r.kind, "object_type": r.object_type, "object_id": r.object_id,
                 "created_at": iso(r.created_at), "read": r.read}
                for r in call("practitioner_notifications", p_practitioner_id=user.id, p_mark_read=mark_read)]
