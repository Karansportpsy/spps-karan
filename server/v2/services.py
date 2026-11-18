"""Booking-only directory, phase 2: a practitioner's own service catalogue.

* ``practitioner_services`` holds the practitioner's services (FORCE RLS: own rows only). No price: payments are off.
  A service is never deleted, only deactivated, so a booking that names it stays explainable.
* ``directory_service_snapshot`` is the public read model. Every write here keeps it in step: an active service has
  a row, an inactive one has none. PostgreSQL shows a row to the public only while the practitioner is listed in
  ``directory_snapshot`` (owner-published, verification in date); public and portal handlers read only this table.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..models import now
from .context import Ctx
from .models import SERVICE_MODES, DirectoryServiceSnapshot, PractitionerService

MAX_SERVICES = 30


class ServiceIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    short_description: str = Field(default="", max_length=280)
    duration_minutes: int = Field(ge=20, le=120)
    mode: str = "online"


class OrderIn(BaseModel):
    service_ids: list[int] = Field(min_length=1, max_length=MAX_SERVICES)


def view(s: PractitionerService) -> dict:
    return {"id": s.id, "name": s.name, "short_description": s.short_description,
            "duration_minutes": s.duration_minutes, "mode": s.mode, "active": s.active, "sort_order": s.sort_order}


def public_view(s: DirectoryServiceSnapshot) -> dict:
    return {"service_id": s.service_id, "name": s.name, "short_description": s.short_description,
            "duration_minutes": s.duration_minutes, "mode": s.mode}


def sync_snapshot(db, s: PractitionerService) -> None:
    """Keep the public copy in step with the service (the caller commits)."""
    snap = db.get(DirectoryServiceSnapshot, s.id)
    if not s.active:
        if snap:
            db.delete(snap)
        return
    snap = snap or DirectoryServiceSnapshot(service_id=s.id, practitioner_id=s.practitioner_id, tenant_id=s.tenant_id)
    snap.name, snap.short_description = s.name, s.short_description
    snap.duration_minutes, snap.mode, snap.sort_order, snap.updated_at = s.duration_minutes, s.mode, s.sort_order, now()
    db.merge(snap)


def public_services(db, practitioner_ids: list[int]) -> dict[int, list[dict]]:
    """Active services for already-listed practitioners, read from the public projection only."""
    if not practitioner_ids:
        return {}
    rows = db.scalars(select(DirectoryServiceSnapshot)
                      .where(DirectoryServiceSnapshot.practitioner_id.in_(practitioner_ids))
                      .order_by(DirectoryServiceSnapshot.sort_order, DirectoryServiceSnapshot.service_id)).all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r.practitioner_id, []).append(public_view(r))
    return out


def _clean(body: ServiceIn) -> dict:
    if body.mode not in SERVICE_MODES:
        raise HTTPException(422, "Choose online or in_person")
    name = " ".join(body.name.split())
    if len(name) < 3:
        raise HTTPException(422, "Give the service a name of at least 3 characters")
    return {"name": name, "short_description": body.short_description.strip(),
            "duration_minutes": body.duration_minutes, "mode": body.mode}


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current = ctx.db_session, ctx.get_current

    def own(db, user, sid: int) -> PractitionerService:
        s = db.scalar(select(PractitionerService).where(PractitionerService.id == sid,
                                                        PractitionerService.practitioner_id == user.id,
                                                        PractitionerService.tenant_id == user.tenant_id))
        if not s:
            raise HTTPException(404, "Service not found")
        return s

    def mine(db, user) -> list[PractitionerService]:
        return db.scalars(select(PractitionerService).where(PractitionerService.practitioner_id == user.id,
                                                            PractitionerService.tenant_id == user.tenant_id)
                          .order_by(PractitionerService.sort_order, PractitionerService.id)).all()

    @app.get("/api/v2/practitioner-services")
    def list_services(user=Depends(get_current), db=Depends(db_session)):
        return {"services": [view(s) for s in mine(db, user)]}

    @app.post("/api/v2/practitioner-services", status_code=201)
    def add_service(body: ServiceIn, user=Depends(get_current), db=Depends(db_session)):
        existing = mine(db, user)
        if len(existing) >= MAX_SERVICES:
            raise HTTPException(409, f"Up to {MAX_SERVICES} services")
        s = PractitionerService(practitioner_id=user.id, tenant_id=user.tenant_id, active=True,
                                sort_order=min(999, max([x.sort_order for x in existing], default=-1) + 1),
                                created_at=now(), updated_at=now(), **_clean(body))
        db.add(s)
        db.flush()
        sync_snapshot(db, s)
        ctx.event(db, user, "service.created", "practitioner_service", s.id)
        db.commit()
        return view(s)

    @app.put("/api/v2/practitioner-services/{sid}")
    def edit_service(sid: int, body: ServiceIn, user=Depends(get_current), db=Depends(db_session)):
        s = own(db, user, sid)
        for k, v in _clean(body).items():
            setattr(s, k, v)
        s.updated_at = now()
        sync_snapshot(db, s)
        ctx.event(db, user, "service.updated", "practitioner_service", s.id)
        db.commit()
        return view(s)

    def set_active(sid: int, active: bool, user, db):
        s = own(db, user, sid)
        s.active, s.updated_at = active, now()
        db.flush()
        sync_snapshot(db, s)
        ctx.event(db, user, "service.activated" if active else "service.deactivated", "practitioner_service", s.id)
        db.commit()
        return view(s)

    @app.post("/api/v2/practitioner-services/{sid}/deactivate")
    def deactivate(sid: int, user=Depends(get_current), db=Depends(db_session)):
        return set_active(sid, False, user, db)

    @app.post("/api/v2/practitioner-services/{sid}/activate")
    def activate(sid: int, user=Depends(get_current), db=Depends(db_session)):
        return set_active(sid, True, user, db)

    @app.post("/api/v2/practitioner-services/order")
    def reorder(body: OrderIn, user=Depends(get_current), db=Depends(db_session)):
        services = {s.id: s for s in mine(db, user)}
        if len(set(body.service_ids)) != len(body.service_ids) or set(body.service_ids) != set(services):
            raise HTTPException(422, "List each of your services exactly once")
        for i, sid in enumerate(body.service_ids):
            services[sid].sort_order, services[sid].updated_at = i, now()
            sync_snapshot(db, services[sid])
        ctx.event(db, user, "service.reordered", "practitioner", user.id)
        db.commit()
        return {"services": [view(s) for s in mine(db, user)]}
