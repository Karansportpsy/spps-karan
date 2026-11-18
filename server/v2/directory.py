"""Phase 7: Find a Sports Psychologist (flag SPPS_DIRECTORY_ENABLED).

* A practitioner opts in with a public profile and submits it. Publication is a separate owner decision from
  account approval; the owner records what was checked (identity, qualification, registration) and when.
* Publishing writes ``directory_snapshot``. Every public endpoint reads ONLY that table, and only rows whose
  verification has not lapsed (yearly). Delisting, rejecting or suspending the account, or closing it, deletes
  the snapshot row, so the practitioner disappears at once.
* Search is by filters only; results are in alphabetical order. No ranking, no reviews, no payments.
* There is no enquiry form. Contact starts with an appointment request (booking), see bookings.py.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..models import Practitioner, PractitionerProfile, now
from . import guards
from .context import Ctx, require_admin
from .models import DELIVERY, VERIFICATION_KINDS, DirectoryProfile, DirectorySnapshot
from .services import public_services

PUBLIC_FIELDS = ("practitioner_id", "display_name", "qualifications", "sports", "athlete_levels", "age_groups",
                 "languages", "delivery", "city", "taking_new_clients", "about")
REVERIFY = timedelta(days=365)
CLINICAL_WORDS = re.compile(r"\b(diagnos\w*|medication|suicid\w*|self[- ]?harm|depress\w*|disorder|trauma|abuse)\b", re.I)
NOT_CRISIS = "SPPS is not a crisis or emergency service."


def crisis_text() -> str:
    return os.getenv("SPPS_CRISIS_CONTACTS_TEXT", "").strip() or \
        "In an emergency call 112. Tele-MANAS mental health helpline: 14416 (free, 24x7)."


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=3, max_length=120)
    qualifications: str = Field(min_length=4, max_length=600)
    sports: str = Field(default="", max_length=300)
    athlete_levels: str = Field(default="", max_length=200)
    age_groups: str = Field(default="", max_length=200)
    languages: str = Field(default="", max_length=200)
    delivery: str = "online"
    city: str = Field(default="", max_length=80)
    taking_new_clients: bool = True
    about: str = Field(default="", max_length=1200)


class VerificationIn(BaseModel):
    kind: str
    checked_on: date


class PublishIn(BaseModel):
    verifications: list[VerificationIn] = Field(min_length=1, max_length=3)


class DelistIn(BaseModel):
    reason: str = Field(min_length=3, max_length=240)


def remove_from_directory(db, practitioner_id: int) -> None:
    """Called whenever a practitioner may no longer be listed (delist, rejection, suspension, closure)."""
    snap = db.get(DirectorySnapshot, practitioner_id)
    if snap:
        db.delete(snap)
    prof = db.get(DirectoryProfile, practitioner_id)
    if prof and prof.status == "published":
        prof.status = "delisted"


def _split(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def public_row(s: DirectorySnapshot, services: list[dict] | None = None) -> dict:
    out = {k: getattr(s, k) for k in PUBLIC_FIELDS}
    out["services"] = services or []
    out.update({"sports": _split(s.sports), "athlete_levels": _split(s.athlete_levels),
                "age_groups": _split(s.age_groups), "languages": _split(s.languages),
                "badges": json.loads(s.badges), "has_photo": s.photo is not None,
                "photo_url": f"/api/v2/public/directory/{s.practitioner_id}/photo" if s.photo else None})
    return out


def register(app: FastAPI, ctx: Ctx) -> None:
    db_session, get_current, auth_session, tenant_public = ctx.db_session, ctx.get_current, ctx.auth_session, ctx.tenant_public

    # ------------------------------------------------------------------ practitioner
    @app.get("/api/v2/directory/profile")
    def my_profile(user=Depends(get_current), db=Depends(db_session)):
        p = db.get(DirectoryProfile, user.id)
        if not p:
            return {"status": "none", "display_name": user.name}
        return {**{k: getattr(p, k) for k in ProfileIn.model_fields}, "status": p.status, "owner_note": p.owner_note,
                "listed": db.get(DirectorySnapshot, user.id) is not None}

    @app.put("/api/v2/directory/profile")
    def save_profile(body: ProfileIn, user=Depends(get_current), db=Depends(db_session)):
        if body.delivery not in DELIVERY:
            raise HTTPException(422, "Choose online, in_person or both")
        p = db.get(DirectoryProfile, user.id) or DirectoryProfile(practitioner_id=user.id, tenant_id=user.tenant_id)
        for k, v in body.model_dump().items():
            setattr(p, k, v.strip() if isinstance(v, str) else v)
        # Any edit takes the public copy down until the owner publishes the new text.
        if p.status in ("published", "submitted"):
            p.status = "draft"
        p.updated_at = now()
        db.merge(p)
        snap = db.get(DirectorySnapshot, user.id)
        if snap:
            db.delete(snap)
        db.commit()
        return my_profile(user, db)

    @app.post("/api/v2/directory/profile/submit")
    def submit_profile(user=Depends(get_current), db=Depends(db_session)):
        p = db.get(DirectoryProfile, user.id)
        if not p:
            raise HTTPException(409, "Write your public profile first")
        p.status, p.submitted_at = "submitted", now()
        db.commit()
        return {"status": p.status}

    @app.post("/api/v2/directory/profile/withdraw")
    def withdraw_profile(user=Depends(get_current), db=Depends(db_session)):
        remove_from_directory(db, user.id)
        p = db.get(DirectoryProfile, user.id)
        if p:
            p.status = "draft"
        db.commit()
        return {"status": "draft"}

    # ------------------------------------------------------------------ owner
    @app.get("/api/v2/admin/directory")
    def owner_queue(user=Depends(get_current), db=Depends(db_session)):
        require_admin(user)
        out = []
        for p in db.scalars(select(DirectoryProfile).where(DirectoryProfile.tenant_id == user.tenant_id)):
            s = db.get(DirectorySnapshot, p.practitioner_id)
            out.append({**{k: getattr(p, k) for k in ProfileIn.model_fields}, "practitioner_id": p.practitioner_id,
                        "status": p.status, "listed": s is not None, "badges": json.loads(s.badges) if s else [],
                        "verified_until": s.verified_until if s else None,
                        "reverification_due": bool(s and s.verified_until <= (date.today() + timedelta(days=30)).isoformat())})
        return out

    @app.post("/api/v2/admin/directory/{pid}/publish")
    def publish(pid: int, body: PublishIn, user=Depends(get_current), db=Depends(db_session), auth=Depends(auth_session)):
        require_admin(user)
        p = db.scalar(select(DirectoryProfile).where(DirectoryProfile.practitioner_id == pid,
                                                     DirectoryProfile.tenant_id == user.tenant_id))
        if not p or p.status not in ("submitted", "published", "delisted"):
            raise HTTPException(409, "The practitioner has not submitted a public profile")
        person = auth.scalar(select(Practitioner).where(Practitioner.id == pid, Practitioner.tenant_id == user.tenant_id))
        account = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == pid))
        if not person or not person.active or not account or account.status != "approved":
            raise HTTPException(409, "Only an approved, active practitioner account can be published")
        kinds = [v.kind for v in body.verifications]
        if set(kinds) - set(VERIFICATION_KINDS) or len(set(kinds)) != len(kinds):
            raise HTTPException(422, "Verification kinds: identity, qualification, registration (each once)")
        if any(v.checked_on > date.today() or v.checked_on < date.today() - REVERIFY for v in body.verifications):
            raise HTTPException(422, "Checks must be dated within the last year and not in the future")
        oldest = min(v.checked_on for v in body.verifications)
        snap = db.get(DirectorySnapshot, pid) or DirectorySnapshot(practitioner_id=pid, tenant_id=user.tenant_id)
        for k in PUBLIC_FIELDS[1:]:
            setattr(snap, k, getattr(p, k))
        snap.photo = account.avatar_bytes
        snap.badges = json.dumps([{"kind": v.kind, "checked_on": v.checked_on.isoformat()} for v in body.verifications])
        snap.verified_until = (oldest + REVERIFY).isoformat()
        snap.published_at = now()
        db.merge(snap)
        p.status, p.owner_note = "published", ""
        ctx.event(db, user, "directory.published", "practitioner", pid)
        db.commit()
        return {"status": "published", "verified_until": snap.verified_until}

    @app.post("/api/v2/admin/directory/{pid}/delist")
    def delist(pid: int, body: DelistIn, user=Depends(get_current), db=Depends(db_session)):
        require_admin(user)
        p = db.scalar(select(DirectoryProfile).where(DirectoryProfile.practitioner_id == pid,
                                                     DirectoryProfile.tenant_id == user.tenant_id))
        if not p:
            raise HTTPException(404, "Profile not found")
        remove_from_directory(db, pid)
        p.status, p.owner_note = "delisted", body.reason.strip()
        ctx.event(db, user, "directory.delisted", "practitioner", pid)
        db.commit()
        return {"status": "delisted"}

    # ------------------------------------------------------------------ public (reads ONLY directory_snapshot)
    def listed(db, tid):
        return select(DirectorySnapshot).where(DirectorySnapshot.tenant_id == tid,
                                               DirectorySnapshot.verified_until >= date.today().isoformat())

    @app.get("/api/v2/public/directory")
    def search(request: Request, sport: str = "", level: str = "", age_group: str = "", language: str = "",
               delivery: str = "", city: str = "", taking_new_clients: bool | None = None, db=Depends(db_session)):
        guards.limit_ip(request, "directory-search")
        tid = tenant_public(db).id
        rows = db.scalars(listed(db, tid).order_by(DirectorySnapshot.display_name)).all()

        def has(csv_, want):
            return not want or want.strip().lower() in [x.lower() for x in _split(csv_)]
        rows = [s for s in rows
               if has(s.sports, sport) and has(s.athlete_levels, level) and has(s.age_groups, age_group)
               and has(s.languages, language) and (not delivery or s.delivery in (delivery, "both"))
               and (not city or s.city.strip().lower() == city.strip().lower())
               and (taking_new_clients is None or s.taking_new_clients == taking_new_clients)]
        offered = public_services(db, [s.practitioner_id for s in rows])
        out = [public_row(s, offered.get(s.practitioner_id)) for s in rows]
        return {"results": out, "order": "alphabetical", "notice": NOT_CRISIS + " " + crisis_text()}

    def one(db, pid):
        s = db.scalar(listed(db, tenant_public(db).id).where(DirectorySnapshot.practitioner_id == pid))
        if not s:
            raise HTTPException(404, "Not found")
        return s

    @app.get("/api/v2/public/directory/{pid}")
    def public_profile(pid: int, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "directory-search")
        s = one(db, pid)
        return public_row(s, public_services(db, [s.practitioner_id]).get(s.practitioner_id))

    @app.get("/api/v2/public/directory/{pid}/photo")
    def public_photo(pid: int, request: Request, db=Depends(db_session)):
        guards.limit_ip(request, "directory-search")
        s = one(db, pid)
        if not s.photo:
            raise HTTPException(404, "No photo")
        return Response(s.photo, media_type="image/webp", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/find")
    def find_page():
        from ..main import WEB
        return FileResponse(WEB / "find.html")

