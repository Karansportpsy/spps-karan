"""13D institutional licensing: organisation seat licences managed by tenant administrators.

An administrator (verified in the clinical database by get_current + is_admin) creates a
licence for an organisation and assigns seats to practitioners of the SAME tenant. An active
seat counts as membership (subscription_routes.MEMBERSHIP_SOURCES) — it never verifies
credentials or publishes a directory listing; those gates stay independent.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from . import subscription_routes
from .models import Practitioner

ERRORS = {"P0002": (404, "Not found"), "P0004": (409, "No seat available or licence not active"),
          "23505": (409, "Practitioner already holds a seat"), "22023": (422, "Invalid details")}


class LicenceIn(BaseModel):
    organisation_name: str = Field(min_length=2, max_length=160)
    seats: int = Field(ge=1, le=500)
    valid_days: int = Field(default=365, ge=1, le=1100)
    quote_reference: int | None = Field(default=None, gt=0)


class SeatIn(BaseModel):
    practitioner_id: int = Field(gt=0)


class StatusIn(BaseModel):
    status: Literal["ACTIVE", "SUSPENDED", "ENDED"]


def register_licensing_routes(app: FastAPI, *, get_current, db_session, event) -> None:
    engine = app.state.portal_auth_engine

    def call(name, **params):
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine.begin() as conn:
                return conn.execute(text(f"SELECT spps_identity_dev.{name}({placeholders})"), params).scalar()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", "")
            if code in ERRORS:
                raise HTTPException(*ERRORS[code]) from None
            raise

    def has_seat(user) -> bool:
        try:
            return bool(call("practitioner_has_seat", p_practitioner=user.id, p_tenant=user.tenant_id))
        except Exception:  # noqa: BLE001 — entitlement lookups fail closed
            return False

    if has_seat not in subscription_routes.MEMBERSHIP_SOURCES:
        subscription_routes.MEMBERSHIP_SOURCES.clear()  # one app per process; never stack stale engines
        subscription_routes.MEMBERSHIP_SOURCES.append(has_seat)

    def admin(user):
        if not user.is_admin:
            raise HTTPException(403, "Administrator required")

    @app.get("/api/admin/licences")
    def licences(user=Depends(get_current)):
        admin(user)
        return call("admin_licences", p_tenant=user.tenant_id)

    @app.post("/api/admin/licences", status_code=201)
    def create(body: LicenceIn, user=Depends(get_current), db=Depends(db_session)):
        admin(user)
        lid = call("admin_create_licence", p_admin=user.id, p_tenant=user.tenant_id, p_name=body.organisation_name,
                   p_seats=body.seats, p_valid_days=body.valid_days, p_quote=body.quote_reference)
        event(db, user, "licence.created", "licence", lid)
        db.commit()
        return {"licence_id": lid}

    @app.post("/api/admin/licences/{licence_id}/seats", status_code=201)
    def assign(licence_id: int, body: SeatIn, user=Depends(get_current), db=Depends(db_session)):
        admin(user)
        # Seat holders must be practitioners of the administrator's own tenant (auth-role lookup).
        with app.state.AuthDB() as auth:
            target = auth.scalar(select(Practitioner.id).where(Practitioner.id == body.practitioner_id,
                                                               Practitioner.tenant_id == user.tenant_id))
        if target is None:
            raise HTTPException(404, "Practitioner not found")
        seat = call("admin_assign_seat", p_admin=user.id, p_tenant=user.tenant_id, p_licence=licence_id,
                    p_practitioner=body.practitioner_id)
        event(db, user, "licence.seat_assigned", "licence", licence_id)
        db.commit()
        return {"seat_id": seat}

    @app.post("/api/admin/licences/{licence_id}/seats/{practitioner_id}/revoke")
    def revoke(licence_id: int, practitioner_id: int, user=Depends(get_current), db=Depends(db_session)):
        admin(user)
        call("admin_revoke_seat", p_tenant=user.tenant_id, p_licence=licence_id, p_practitioner=practitioner_id)
        event(db, user, "licence.seat_revoked", "licence", licence_id)
        db.commit()
        return {"revoked": True}

    @app.post("/api/admin/licences/{licence_id}/status")
    def status(licence_id: int, body: StatusIn, user=Depends(get_current), db=Depends(db_session)):
        admin(user)
        result = call("admin_set_licence_status", p_tenant=user.tenant_id, p_licence=licence_id, p_status=body.status)
        event(db, user, f"licence.{body.status.lower()}", "licence", licence_id)
        db.commit()
        return {"licence_id": licence_id, "status": result}
