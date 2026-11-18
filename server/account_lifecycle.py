"""Account lifecycle, phase A: practitioner self-service deactivation and data export.

Design record: docs/ACCOUNT_LIFECYCLE_DESIGN.md. Everything here is behind SPPS_ACCOUNT_LIFECYCLE_ENABLED
(default off). With the flag off no route is mounted and the rest of the application behaves exactly as before.

* Deactivate / reactivate: takes the practitioner's own public listing offline and back. Nothing is deleted, no
  approval flag changes, the practitioner keeps working. On PostgreSQL the write goes through the SECURITY DEFINER
  function spps_dev.set_own_account_deactivation (migration 0032); the application role has no UPDATE right on the
  column. The ORM model deliberately does not map the column, so this module cannot break a database where the
  migration has not been run yet; production startup refuses the flag until it has (release_preflight).
* Export: one JSON file with the practitioner's own professional and client records. Requires the practitioner to
  prove their password again, is audited, and never contains password hashes, session or payment-provider
  identifiers or the profile photo bytes. The file holds client information: the notice inside it says whose
  responsibility that is.
* Closing and purging an account is NOT part of this phase (see the design record).
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sa_inspect, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from . import supabase_practitioner_auth as sbauth
from .models import (AppointmentSlot, AssessmentAdministration, AssessmentSubscore, Assignment, Athlete, Booking,
                     Case, Factor, InjuryCheckIn, InjuryEpisode, Inquiry, Note, Observation, PractitionerProgramme,
                     PractitionerProfile, ProgrammeEnquiry, Report, now)
from .security import verify_password

FLAG = "SPPS_ACCOUNT_LIFECYCLE_ENABLED"
REAUTH_MAX_AGE_SECONDS = 300
THROTTLE_FAILURES, THROTTLE_WINDOW = 5, 900
# Columns that never leave the server, whichever table they are on.
NEVER_EXPORT = {"password_hash", "token_hash", "csrf_token", "avatar_bytes", "supabase_auth_uid",
                "linked_account_id", "provider_order_id", "provider_payment_id"}
_failures: dict[int, list[float]] = {}


def enabled() -> bool:
    return os.getenv(FLAG, "false").lower() == "true"


# --- state storage ---------------------------------------------------------------------------------------------

def _postgres(db) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _table(db) -> str:
    return "spps_dev.practitioner_profiles" if _postgres(db) else "practitioner_profiles"


def _parse(value):
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def deactivated_at(db, practitioner_id: int):
    """When the practitioner took their listing offline, or None. Unreadable rows (another practitioner's profile
    under row-level security) count as not deactivated: the caller's own eligibility checks decide the rest."""
    row = db.execute(text(f"SELECT deactivated_at FROM {_table(db)} WHERE practitioner_id=:p"),
                     {"p": practitioner_id}).first()
    return _parse(row[0]) if row else None


def is_deactivated(db, practitioner_id: int) -> bool:
    return deactivated_at(db, practitioner_id) is not None


def set_deactivation(db, practitioner_id: int, deactivate: bool):
    if _postgres(db):
        # The function acts on the caller's own profile (transaction-local identity); it takes no id.
        return _parse(db.execute(text("SELECT spps_dev.set_own_account_deactivation(:d)"),
                                 {"d": deactivate}).scalar())
    stamp = now().isoformat() if deactivate else None
    result = db.execute(text("UPDATE practitioner_profiles SET deactivated_at="
                             + ("COALESCE(deactivated_at,:s)" if deactivate else ":s")
                             + " WHERE practitioner_id=:p"), {"s": stamp, "p": practitioner_id})
    if result.rowcount != 1:
        raise HTTPException(409, "Practitioner profile is not available")
    return deactivated_at(db, practitioner_id)


def ensure_local_column(engine) -> None:
    """Test/local SQLite databases are built from the ORM, which does not map the column. PostgreSQL is never
    altered by the application: the column comes from migration 0032."""
    if engine.dialect.name != "sqlite":
        return
    try:
        with engine.begin() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(practitioner_profiles)"))}
            if columns and "deactivated_at" not in columns:
                conn.execute(text("ALTER TABLE practitioner_profiles ADD COLUMN deactivated_at DATETIME"))
    except OperationalError:
        pass  # no local database file yet; the tests and the local runner build the schema first


# --- re-authentication -----------------------------------------------------------------------------------------

class ReauthIn(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    supabase_access_token: str | None = Field(default=None, max_length=8192)


class ConfirmIn(BaseModel):
    confirm: Literal[True]


def _password_signed_in_at(token: str):
    """Seconds-since-epoch of the password sign-in recorded in the (already GoTrue-verified) token, or None.
    The amr timestamp does not move when the access token is silently refreshed, so a stale session cannot
    pass as a fresh confirmation."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        stamps = [item.get("timestamp") for item in claims.get("amr") or []
                  if isinstance(item, dict) and item.get("method") == "password"]
        stamps = [s for s in stamps if isinstance(s, (int, float))]
        return max(stamps) if stamps else None
    except (ValueError, IndexError, TypeError, json.JSONDecodeError):
        return None


def _throttle(user_id: int, failed: bool | None = None) -> None:
    """At most five failed confirmations per fifteen minutes per account (process-local, best effort)."""
    cutoff = time.time() - THROTTLE_WINDOW
    recent = [t for t in _failures.get(user_id, []) if t > cutoff]
    if failed:
        recent.append(time.time())
    _failures[user_id] = recent
    if failed is None and len(recent) >= THROTTLE_FAILURES:
        raise HTTPException(429, "Too many failed confirmations. Wait a few minutes and try again.")


def reauthenticate(db, user, payload: ReauthIn) -> None:
    hosted = sbauth.enabled()
    supplied = bool(payload.supabase_access_token) if hosted else bool(payload.password)
    _throttle(user.id)
    try:
        if hosted:
            if not payload.supabase_access_token:
                raise HTTPException(401, "Sign in again to confirm it is you")
            verified = sbauth.verify_access_token(payload.supabase_access_token, password_required=True)
            profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == user.id))
            if not profile or not profile.supabase_auth_uid or verified["uid"] != profile.supabase_auth_uid:
                raise HTTPException(403, "That sign-in belongs to a different account")
            signed = _password_signed_in_at(payload.supabase_access_token)
            if signed is None or not (-60 <= time.time() - signed <= REAUTH_MAX_AGE_SECONDS):
                raise HTTPException(401, "Sign in again to confirm it is you")
        elif not payload.password or not verify_password(payload.password, user.password_hash):
            raise HTTPException(403, "Password is incorrect")
    except HTTPException as exc:
        if supplied and exc.status_code in (401, 403):  # an empty submission is not a guess
            _throttle(user.id, failed=True)
        raise


# --- export ----------------------------------------------------------------------------------------------------

def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return value


def _row(obj) -> dict:
    return {attr.key: _jsonable(getattr(obj, attr.key)) for attr in sa_inspect(type(obj)).column_attrs
            if attr.key not in NEVER_EXPORT}


def build_export(db, user) -> dict:
    uid, tid = user.id, user.tenant_id
    warnings: list[str] = []

    def grab(name, model, *conditions, empty=False):
        if empty:
            return []
        try:
            if _postgres(db):
                with db.begin_nested():  # a refused table must not abort the whole export transaction
                    rows = db.scalars(select(model).where(*conditions).order_by(model.id)).all()
            else:
                rows = db.scalars(select(model).where(*conditions).order_by(model.id)).all()
            return [_row(r) for r in rows]
        except DBAPIError as exc:
            warnings.append(f"{name} could not be read ({type(exc.orig).__name__}); it is missing from this file")
            return []

    profile = grab("profile", PractitionerProfile, PractitionerProfile.practitioner_id == uid)
    athletes = grab("athletes", Athlete, Athlete.owner_practitioner_id == uid, Athlete.tenant_id == tid)
    athlete_ids = [a["id"] for a in athletes]
    on_athletes = lambda col: col.in_(athlete_ids)
    administrations = grab("assessment_administrations", AssessmentAdministration,
                           on_athletes(AssessmentAdministration.athlete_id), empty=not athlete_ids)
    episodes = grab("injury_episodes", InjuryEpisode, on_athletes(InjuryEpisode.athlete_id), empty=not athlete_ids)
    body = {
        "profile": (profile or [None])[0],
        "athletes": athletes,
        "cases": grab("cases", Case, Case.owner_practitioner_id == uid, Case.tenant_id == tid),
        "notes": grab("notes", Note, Note.author_id == uid, Note.tenant_id == tid),
        "monitoring_factors": grab("factors", Factor, Factor.owner_practitioner_id == uid),
        "observations": grab("observations", Observation, on_athletes(Observation.athlete_id), empty=not athlete_ids),
        "assignments": grab("assignments", Assignment, on_athletes(Assignment.athlete_id), empty=not athlete_ids),
        "reports": grab("reports", Report, on_athletes(Report.athlete_id), empty=not athlete_ids),
        "assessment_administrations": administrations,
        "assessment_subscores": grab("assessment_subscores", AssessmentSubscore,
                                     AssessmentSubscore.administration_id.in_([a["id"] for a in administrations]),
                                     empty=not administrations),
        "injury_episodes": episodes,
        "injury_checkins": grab("injury_checkins", InjuryCheckIn,
                                InjuryCheckIn.episode_id.in_([e["id"] for e in episodes]), empty=not episodes),
        "enquiries": grab("inquiries", Inquiry, Inquiry.practitioner_id == uid),
        "appointment_slots": grab("appointment_slots", AppointmentSlot, AppointmentSlot.practitioner_id == uid),
        "bookings": grab("bookings", Booking, Booking.practitioner_id == uid),
        "programmes": grab("practitioner_programmes", PractitionerProgramme, PractitionerProgramme.practitioner_id == uid),
        "programme_enquiries": grab("programme_enquiries", ProgrammeEnquiry, ProgrammeEnquiry.practitioner_id == uid),
    }
    return {
        "format": "spps-practitioner-export-v1",
        "generated_at": now().isoformat(),
        "practitioner": {"id": uid, "name": user.name, "email": user.email, "tier": user.tier},
        "notice": ("This file contains confidential client information that you hold as a practitioner. You are "
                   "responsible for storing, sharing and deleting it securely and lawfully, including under the "
                   "Digital Personal Data Protection Act, 2023 and your professional code of ethics."),
        "excluded": ["password and sign-in secrets", "session tokens", "payment-provider identifiers",
                     "profile photo bytes", "records that belong to other practitioners",
                     "the platform's own log of terms acceptances and audit trail"],
        "warnings": warnings,
        "data": body,
    }


# --- routes ----------------------------------------------------------------------------------------------------

def state_payload(db, user) -> dict:
    from .subscription_routes import listing_eligible
    profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == user.id))
    stamp = deactivated_at(db, user.id)
    return {"enabled": True, "email": user.email, "deactivated": stamp is not None,
            "deactivated_at": stamp.isoformat() if stamp else None,
            "listed": bool(listing_eligible(db, user, profile)),
            "reauth": "supabase" if sbauth.enabled() else "password",
            "export_available": True, "closure_available": False}


def register_lifecycle_routes(app: FastAPI, db_session, get_current, event) -> None:
    ensure_local_column(app.state.engine)

    @app.get("/api/account/lifecycle")
    def lifecycle_state(user=Depends(get_current), db=Depends(db_session)):
        return state_payload(db, user)

    def change(user, db, deactivate: bool):
        try:
            set_deactivation(db, user.id, deactivate)
        except DBAPIError:
            db.rollback()
            raise HTTPException(409, "Practitioner profile is not available") from None
        event(db, user, "account.deactivated" if deactivate else "account.reactivated", "practitioner", user.id)
        db.commit()
        return state_payload(db, user)

    @app.post("/api/account/deactivate")
    def deactivate(payload: ConfirmIn, user=Depends(get_current), db=Depends(db_session)):
        return change(user, db, True)

    @app.post("/api/account/reactivate")
    def reactivate(payload: ConfirmIn, user=Depends(get_current), db=Depends(db_session)):
        return change(user, db, False)

    @app.post("/api/account/export")
    def export(payload: ReauthIn, user=Depends(get_current), db=Depends(db_session)):
        reauthenticate(db, user, payload)
        document = build_export(db, user)
        event(db, user, "account.exported", "practitioner", user.id)
        db.commit()
        filename = f"spps-export-{user.id}-{now().strftime('%Y%m%d')}.json"
        return JSONResponse(document, headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                               "Cache-Control": "no-store"})
