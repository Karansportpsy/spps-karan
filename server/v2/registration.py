"""Phase 1: server-side pending application and the registration state machine.

Flow (works when the confirmation link is opened in a different browser, or in Chrome from the Android app):

1. The sign-up form posts the application text to ``POST /api/public/practitioner-applications/pending`` (rate
   limited, bot-checked). The server stores it as ``applied`` (unverified, expires after 7 days) and returns a
   random *claim*. Only the claim's SHA-256 is stored.
2. The browser passes the claim to Supabase ``signUp`` as ``user_metadata.spps_claim``. The metadata travels with
   the account, so any browser or the app that later holds a session for this account presents it.
3. The first authenticated call from ANY browser (``/promote``, the sign-in exchange or the application status
   check) verifies the Supabase access token with GoTrue (confirmed e-mail required), reads ``spps_claim`` from the
   verified user record, and promotes the matching row: applied -> email_verified -> under_review, creating the
   pending practitioner record. Nothing is typed twice.
4. If no claim row matches (older client, expired row), the v1 carried-details fallback in
   ``user_metadata.application`` is used. If there is no text at all, the account is recorded as
   ``email_verified`` with ``has_application=false`` and appears in the owner's "Confirmed, no application yet"
   list.

An unverified account never reaches review: the review queue lists practitioner profiles, and a profile is only
created by promotion, which requires GoTrue to report a confirmed e-mail for the same subject.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .. import supabase_practitioner_auth as sbauth
from ..models import Audit, Practitioner, PractitionerProfile, TermsAcceptance, Tenant, now
from ..security import make_password, new_token
from . import guards
from .context import Ctx, is_pg, require_admin
from .models import (REGISTRATION_TRANSITIONS, EmailDelivery, RegistrationApplication, RegistrationTransition)

LOG = logging.getLogger("spps.registration")
PENDING_TTL = timedelta(days=7)
CLAIM_KEY = "spps_claim"
CARRIED_KEY = "application"


def owner_email() -> str:
    from ..owner_profile import OWNER_EMAIL  # single definition of the platform owner's address
    return (os.getenv("SPPS_OWNER_EMAIL", "") or OWNER_EMAIL).strip().lower()


def claim_hash(claim: str) -> str:
    return hashlib.sha256(("spps-claim:" + claim).encode()).hexdigest()


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class PendingApplicationIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=3, max_length=120)
    qualifications: str = Field(min_length=4, max_length=1000)
    experience_years: int = Field(ge=0, le=70)
    achievements: str = Field(default="", max_length=1500)
    specialisms: str = Field(default="", max_length=250)
    terms_accepted: bool
    form_token: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=200)  # honeypot


class AccessTokenIn(BaseModel):
    access_token: str = Field(min_length=100, max_length=8192)


class PhotoIn(AccessTokenIn):
    photo_base64: str = Field(min_length=40, max_length=950000)


def record_transition(db, app_row, to_state: str, *, actor_kind: str, actor_id: int | None = None) -> None:
    """Move an application to ``to_state`` and append the audit row. Invalid moves raise 409."""
    current = app_row.state
    if current == to_state:
        return
    if to_state not in REGISTRATION_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Registration cannot move from {current} to {to_state}")
    app_row.state = to_state
    app_row.updated_at = now()
    db.add(RegistrationTransition(application_id=app_row.id, tenant_id=app_row.tenant_id, from_state=current,
                                  to_state=to_state, actor_kind=actor_kind, actor_practitioner_id=actor_id))


def carried_from_metadata(metadata: dict) -> dict | None:
    """The v1 fallback: application text carried in user_metadata. Untrusted; clipped to the form limits."""
    raw = metadata.get(CARRIED_KEY) if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return None
    out = {"name": str(raw.get("name") or "").strip()[:120],
           "qualifications": str(raw.get("qualifications") or "").strip()[:1000],
           "achievements": str(raw.get("achievements") or "")[:1500],
           "specialisms": str(raw.get("specialisms") or "")[:250]}
    try:
        out["experience_years"] = min(70, max(0, int(raw.get("experience_years") or 0)))
    except (TypeError, ValueError):
        out["experience_years"] = 0
    if len(out["name"]) < 3 or len(out["qualifications"]) < 4:
        return None
    return out


# ------------------------------------------------------------------ promotion (SQLite/ORM path)
def _promote_orm(db, tenant: Tenant, identity: dict, claim: str | None, carried: dict | None) -> dict:
    uid, email = identity["uid"], identity["email"]
    existing = db.scalar(select(PractitionerProfile).where(PractitionerProfile.supabase_auth_uid == uid))
    if existing:
        row = db.scalar(select(RegistrationApplication).where(
            RegistrationApplication.practitioner_id == existing.practitioner_id))
        return {"state": row.state if row else "under_review", "practitioner_id": existing.practitioner_id,
                "has_application": True, "promoted": False}
    row = None
    if claim:
        row = db.scalar(select(RegistrationApplication).where(
            RegistrationApplication.claim_hash == claim_hash(claim), RegistrationApplication.email == email,
            RegistrationApplication.state == "applied", RegistrationApplication.tenant_id == tenant.id))
        if row and _aware(row.expires_at) and _aware(row.expires_at) < now():
            row = None
    if row is None and carried:
        row = RegistrationApplication(tenant_id=tenant.id, email=email, state="applied", has_application=True,
                                      **carried)
        db.add(row)
        db.flush()
    if row is None:
        sighting = db.scalar(select(RegistrationApplication).where(
            RegistrationApplication.auth_uid == uid, RegistrationApplication.has_application == False))  # noqa: E712
        if not sighting:
            sighting = RegistrationApplication(tenant_id=tenant.id, email=email, auth_uid=uid, state="applied",
                                               has_application=False, email_verified_at=now())
            db.add(sighting)
            db.flush()
            record_transition(db, sighting, "email_verified", actor_kind="system")
        db.commit()
        return {"state": "email_verified", "practitioner_id": None, "has_application": False, "promoted": False}
    if db.scalar(select(Practitioner.id).where(Practitioner.email == email)):
        raise HTTPException(409, "The verified account is already registered")
    row.auth_uid = uid
    row.email_verified_at = now()
    row.claim_hash = None  # single use
    record_transition(db, row, "email_verified", actor_kind="system")
    person = Practitioner(tenant_id=tenant.id, email=email, name=row.name.strip(),
                          password_hash=make_password(new_token()), tier="free", active=False, is_admin=False)
    db.add(person)
    db.flush()
    db.add(PractitionerProfile(practitioner_id=person.id, qualifications=row.qualifications.strip(),
                               experience_years=row.experience_years, achievements=row.achievements,
                               specialisms=row.specialisms, avatar_bytes=None, consultation_fee_paise=0,
                               public_approved=False, credentials_verified=False, status="pending_approval",
                               supabase_auth_uid=uid, email_verified_at=now()))
    db.add(TermsAcceptance(practitioner_id=person.id, version="v1.0-free"))
    db.add(Audit(tenant_id=tenant.id, actor_id=None, action="registration.free_pending_credential_review",
                 object_type="practitioner", object_id=person.id))
    row.practitioner_id = person.id
    record_transition(db, row, "under_review", actor_kind="system")
    # A sighting for the same account is now resolved.
    for s in db.scalars(select(RegistrationApplication).where(RegistrationApplication.auth_uid == uid,
                                                              RegistrationApplication.has_application == False)):  # noqa: E712
        db.delete(s)
    db.commit()
    return {"state": "under_review", "practitioner_id": person.id, "has_application": True, "promoted": True}


# ------------------------------------------------------------------ promotion (PostgreSQL path)
def _promote_pg(db, tenant_slug: str, identity: dict, claim: str | None, carried: dict | None) -> dict:
    c = carried or {}
    try:
        res = db.execute(text("""SELECT * FROM spps_dev.v2_promote_application(
              CAST(:uid AS uuid), :email, :tenant, :claim, :name, :q, :y, :a, :s, :hash)"""), {
            "uid": identity["uid"], "email": identity["email"], "tenant": tenant_slug,
            "claim": claim_hash(claim) if claim else None, "name": c.get("name"), "q": c.get("qualifications"),
            "y": c.get("experience_years"), "a": c.get("achievements"), "s": c.get("specialisms"),
            "hash": make_password(new_token())}).mappings().one()
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(409, "The verified account could not be registered") from exc
    return {"state": res["state"], "practitioner_id": res["practitioner_id"],
            "has_application": res["has_application"], "promoted": res["promoted"]}


def promote(db, tenant, identity: dict) -> dict:
    """Idempotent: promote the verified account's application, or record a confirmed account without one."""
    if identity["email"] == owner_email():
        return {"state": "owner", "practitioner_id": None, "has_application": False, "promoted": False}
    metadata = identity.get("user_metadata") or {}
    claim = metadata.get(CLAIM_KEY) if isinstance(metadata.get(CLAIM_KEY), str) else None
    if claim and not (20 <= len(claim) <= 120):
        claim = None
    carried = carried_from_metadata(metadata)
    if is_pg(db):
        return _promote_pg(db, tenant.slug, identity, claim, carried)
    return _promote_orm(db, tenant, identity, claim, carried)


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, tenant_public = ctx.db_session, ctx.get_current, ctx.tenant_public
    app.state.v2_promote = promote  # used by the sign-in exchange and the application status check

    @app.get("/api/public/form-token")
    def form_token(request: Request):
        guards.limit_ip(request, "form-token")
        return {"form_token": guards.issue_form_token(), "min_fill_seconds": guards.min_fill_seconds()}

    @app.post("/api/public/practitioner-applications/pending", status_code=201)
    def pending_application(body: PendingApplicationIn, request: Request, db=Depends(db_session)):
        guards.check_bot(body.form_token, body.website)
        guards.limit_ip(request, "pending-application")
        email = str(body.email).strip().lower()
        guards.hit("pending-application-email", guards.email_key(email))
        if not body.terms_accepted:
            raise HTTPException(422, "Accept the terms and privacy notice")
        if email == owner_email():
            raise HTTPException(409, "The platform owner signs in rather than applying")
        tenant = tenant_public(db)
        claim = secrets.token_urlsafe(32)
        values = {"email": email, "name": body.name.strip(), "qualifications": body.qualifications.strip(),
                  "experience_years": body.experience_years, "achievements": body.achievements,
                  "specialisms": body.specialisms}
        if is_pg(db):
            try:
                db.execute(text("""SELECT spps_dev.v2_submit_pending_application(:tenant, :email, :claim, :name,
                                   :qualifications, :experience_years, :achievements, :specialisms)"""),
                           {"tenant": tenant.slug, "claim": claim_hash(claim), **values}).scalar_one()
                db.commit()
            except DBAPIError as exc:
                db.rollback()
                raise HTTPException(422, "The application could not be stored") from exc
        else:
            # Never overwrite an earlier pending row: a stranger could otherwise replace someone's text.
            db.add(RegistrationApplication(tenant_id=tenant.id, claim_hash=claim_hash(claim), state="applied",
                                           has_application=True, expires_at=now() + PENDING_TTL, **values))
            db.commit()
        # The claim is shown once; only its hash is kept. It is bound to the Supabase account at sign-up.
        return {"claim": claim, "state": "applied", "expires_in_days": PENDING_TTL.days}

    @app.post("/api/public/practitioner-application/promote")
    def promote_route(body: AccessTokenIn, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "application-promote")
        identity = sbauth.verify_access_token(body.access_token)  # confirmed e-mail required; link session is fine
        result = promote(db, tenant_public(db), identity)
        needs_photo = False
        if result["practitioner_id"]:
            needs_photo = _needs_photo(db, identity["uid"])
        return {**result, "needs_photo": needs_photo}

    def _needs_photo(db, uid: str) -> bool:
        if is_pg(db):
            return bool(db.execute(text("SELECT spps_dev.v2_application_needs_photo(CAST(:uid AS uuid))"),
                                   {"uid": uid}).scalar())
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.supabase_auth_uid == uid))
        return bool(profile and profile.avatar_bytes is None)

    @app.post("/api/public/practitioner-application/photo")
    def application_photo(body: PhotoIn, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "application-photo")
        identity = sbauth.verify_access_token(body.access_token)
        from ..subscription_routes import read_avatar
        avatar = read_avatar(body.photo_base64)
        if is_pg(db):
            try:
                ok = db.execute(text("SELECT spps_dev.v2_set_application_photo(CAST(:uid AS uuid), :avatar)"),
                                {"uid": identity["uid"], "avatar": avatar}).scalar()
                db.commit()
            except DBAPIError:
                db.rollback()
                ok = False
        else:
            profile = db.scalar(select(PractitionerProfile).where(
                PractitionerProfile.supabase_auth_uid == identity["uid"]))
            ok = bool(profile and profile.status == "pending_approval")
            if ok:
                profile.avatar_bytes = avatar
                db.commit()
        if not ok:
            raise HTTPException(409, "There is no pending application for this account")
        return {"photo_saved": True}

    # ------------------------------------------------------------------ owner views
    def _application_rows(db, user):
        return db.scalars(select(RegistrationApplication).where(
            RegistrationApplication.tenant_id == user.tenant_id)).all()

    @app.get("/api/admin/registrations/summary")
    def registration_summary(user=Depends(get_current), db=Depends(db_session), auth=Depends(ctx.auth_session)):
        require_admin(user)
        rows = _application_rows(db, user)
        confirmed_no_app = [{"id": r.id, "email": r.email, "confirmed_at": r.email_verified_at}
                            for r in rows if r.state == "email_verified" and not r.has_application]
        under_review = [r for r in rows if r.state == "under_review"]
        failures = db.scalars(select(EmailDelivery).where(EmailDelivery.tenant_id == user.tenant_id,
                                                          EmailDelivery.status == "failed")
                              .order_by(EmailDelivery.id.desc())).all()
        # Practitioner names come from the auth connection (the clinical role has no practitioners grant).
        names = {p.id: p for p in auth.scalars(select(Practitioner).where(
            Practitioner.id.in_([f.practitioner_id for f in failures] or [0])))} if failures else {}
        counts = {}
        for r in rows:
            counts[r.state] = counts.get(r.state, 0) + 1
        return {"new_applications": len(under_review),
                "confirmed_no_application": sorted(confirmed_no_app, key=lambda x: str(x["confirmed_at"] or "")),
                "state_counts": counts,
                "email_failures": [{"id": f.id, "practitioner_id": f.practitioner_id,
                                    "name": names[f.practitioner_id].name if f.practitioner_id in names else "",
                                    "kind": f.kind, "attempts": f.attempts, "reason": f.failure_reason,
                                    "last_attempt_at": f.last_attempt_at} for f in failures]}

    @app.get("/api/admin/registrations/{app_id}/transitions")
    def registration_transitions(app_id: int, user=Depends(get_current), db=Depends(db_session)):
        require_admin(user)
        row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.id == app_id,
                                                              RegistrationApplication.tenant_id == user.tenant_id))
        if not row:
            raise HTTPException(404, "Application not found")
        return [{"from": t.from_state, "to": t.to_state, "actor": t.actor_kind,
                 "actor_practitioner_id": t.actor_practitioner_id, "at": t.happened_at}
                for t in db.scalars(select(RegistrationTransition).where(
                    RegistrationTransition.application_id == row.id).order_by(RegistrationTransition.id))]

    @app.post("/api/admin/email-deliveries/{delivery_id}/resend")
    def resend_delivery(delivery_id: int, user=Depends(get_current), db=Depends(db_session),
                        auth=Depends(ctx.auth_session)):
        require_admin(user)
        from . import mail
        row = db.scalar(select(EmailDelivery).where(EmailDelivery.id == delivery_id,
                                                    EmailDelivery.tenant_id == user.tenant_id))
        if not row or row.status != "failed":
            raise HTTPException(404, "No failed delivery with this id")
        person = auth.scalar(select(Practitioner).where(Practitioner.id == row.practitioner_id,
                                                        Practitioner.tenant_id == user.tenant_id))
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == row.practitioner_id))
        if not person or not profile:
            raise HTTPException(404, "Practitioner not found")
        mail.resend(db, row, person, profile.review_note or "")
        ctx.event(db, user, "email.resend", "email_delivery", row.id)
        db.commit()
        return {"id": row.id, "status": row.status, "attempts": row.attempts, "reason": row.failure_reason}


def on_review_decision(db, user, person, decision: str) -> None:
    """Keep the registration state machine in step with the owner's approval decision."""
    to_state = {"approve": "approved", "reject": "rejected", "request_more_info": "more_info_requested"}[decision]
    if decision != "approve":
        from .directory import remove_from_directory
        remove_from_directory(db, person.id)  # an unapproved practitioner never stays in the directory
    row = ensure_row(db, person.id, user.tenant_id, person.email)
    if row.state in ("applied", "email_verified"):
        raise HTTPException(409, "An unverified application cannot be decided")
    if row.state == to_state:
        return
    if to_state not in REGISTRATION_TRANSITIONS.get(row.state, set()):
        # e.g. approve after reject: reopen first so the audit trail shows both steps.
        record_transition(db, row, "under_review", actor_kind="owner", actor_id=user.id)
    record_transition(db, row, to_state, actor_kind="owner", actor_id=user.id)


def on_resubmission(db, person) -> None:
    row = ensure_row(db, person.id, person.tenant_id, person.email)
    if row.state == "more_info_requested":
        record_transition(db, row, "under_review", actor_kind="applicant")


def ensure_row(db, practitioner_id: int, tenant_id: int, email: str) -> RegistrationApplication:
    """Practitioners registered through the v1 path get a state-machine row on first decision."""
    row = db.scalar(select(RegistrationApplication).where(RegistrationApplication.practitioner_id == practitioner_id))
    if row:
        return row
    row = RegistrationApplication(tenant_id=tenant_id, email=email, has_application=True,
                                  state="under_review", practitioner_id=practitioner_id, email_verified_at=now())
    db.add(row)
    db.flush()
    db.add(RegistrationTransition(application_id=row.id, tenant_id=tenant_id, from_state=None,
                                  to_state="under_review", actor_kind="system"))
    return row
