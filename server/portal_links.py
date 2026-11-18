"""Stage 13A.7 — practitioner–athlete linking across the clinical and identity databases.

Trust boundary: practitioner routes run `athlete_for` against the clinical database
(FORCE RLS bound to the signed-in practitioner) BEFORE any identity-database call,
so practitioner_id/athlete_record_id written to the identity database are always
server-verified. Portal routes derive the actor from the portal session token.
No email, name, booking contact or athlete code can create a link: only a
one-time, expiring, practitioner-issued code plus current CASE_LINK consent and
practitioner approval.
"""
from __future__ import annotations

import secrets
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .portal_auth import sha256_hex

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_TTL_SECONDS = 7 * 24 * 3600
ERRORS = {"28000": (401, "Session expired"), "42501": (403, "Not permitted"), "P0002": (404, "Not found"),
          "P0001": (400, "This invitation code is invalid or has expired"),
          "P0003": (409, "Record consent to link with this practitioner first"),
          "23505": (409, "Already linked or a request is pending"), "22023": (422, "Invalid details")}


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))  # 60 bits
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def code_hash(code: str) -> str:
    normalized = "".join(ch for ch in code.upper() if ch in CODE_ALPHABET)
    return sha256_hex("spps-link-invitation:" + normalized)


class RedeemIn(BaseModel):
    code: str = Field(min_length=12, max_length=20)
    subject_person_id: int = Field(gt=0)


class PreviewIn(BaseModel):
    code: str = Field(min_length=12, max_length=20)


class LinkDecisionIn(BaseModel):
    decision: Literal["approve", "decline", "revoke"]
    reason: str = Field(default="", max_length=240)


def register_link_routes(app: FastAPI, db_session, get_current, athlete_for, event) -> None:
    engine = app.state.portal_auth_engine
    current_session = app.state.portal_current_session

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

    def iso(v):
        return v.isoformat() if v else None

    def link_view(r):
        return {"link_id": r.link_id, "athlete_record_id": r.athlete_record_id, "status": r.status,
                "requester_display_name": r.requester_display_name, "via_guardian": r.via_guardian,
                "subject_display_name": r.subject_display_name, "consent_current": r.consent_current,
                "requested_at": iso(r.requested_at), "activated_at": iso(r.activated_at),
                "invitation_expires_at": iso(r.invitation_expires_at)}

    # ------------------------------------------------------------ practitioner
    @app.post("/api/athletes/{athlete_id}/portal-invitations", status_code=201)
    def create_invitation(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete = athlete_for(db, user, athlete_id)  # clinical RLS ownership check first
        code = new_code()
        call("practitioner_create_invitation", p_practitioner_id=user.id, p_tenant_id=user.tenant_id,
             p_athlete_record_id=athlete.id, p_code_hash=code_hash(code), p_ttl_seconds=CODE_TTL_SECONDS)
        event(db, user, "portal_link.invited", "athlete", athlete.id)
        db.commit()
        # The code is shown ONCE to the practitioner to pass to the athlete/guardian in person.
        return {"code": code, "expires_in_days": CODE_TTL_SECONDS // 86400}

    @app.get("/api/athletes/{athlete_id}/portal-links")
    def athlete_links(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete = athlete_for(db, user, athlete_id)
        return [link_view(r) for r in call("practitioner_list_links", p_practitioner_id=user.id,
                                           p_athlete_record_id=athlete.id)]

    @app.get("/api/portal-links")
    def all_links(user=Depends(get_current)):
        return [link_view(r) for r in call("practitioner_list_links", p_practitioner_id=user.id,
                                           p_athlete_record_id=None)]

    @app.post("/api/portal-links/{link_id}/decision")
    def decide_link(link_id: int, payload: LinkDecisionIn, user=Depends(get_current), db=Depends(db_session)):
        status = call("practitioner_decide_link", p_practitioner_id=user.id, p_link_id=link_id,
                      p_decision=payload.decision.upper(), p_reason=payload.reason)[0][0]
        event(db, user, f"portal_link.{payload.decision}", "portal_link", link_id)
        db.commit()
        return {"link_id": link_id, "status": status}

    # ------------------------------------------------------------ portal
    @app.post("/api/portal/links/redeem", status_code=201)
    def redeem(payload: RedeemIn, session=Depends(current_session)):
        # Throttle guessing per account (codes carry 60 bits and expire in 7 days).
        if not call("auth_throttle_hit", p_key=sha256_hex(f"portal:redeem:{session['account_id']}"),
                    p_window_seconds=3600, p_limit=10)[0][0]:
            raise HTTPException(429, "Too many attempts; try again later")
        row = call("portal_redeem_invitation", p_token_hash=sha256_hex(session["token"]),
                   p_code_hash=code_hash(payload.code), p_subject_person_id=payload.subject_person_id)[0]
        return {"link_id": row.link_id, "practitioner_id": row.practitioner_id, "status": row.status}

    def practitioner_names(ids):
        """Display names only (never contact details) via the clinical auth role."""
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        from sqlalchemy import select
        from .models import Practitioner
        with app.state.AuthDB() as auth:
            return {p.id: p.name for p in auth.scalars(select(Practitioner).where(Practitioner.id.in_(ids)))}

    @app.post("/api/portal/links/preview")
    def preview(payload: PreviewIn, session=Depends(current_session)):
        if not call("auth_throttle_hit", p_key=sha256_hex(f"portal:redeem:{session['account_id']}"),
                    p_window_seconds=3600, p_limit=10)[0][0]:
            raise HTTPException(429, "Too many attempts; try again later")
        pid = call("portal_preview_invitation", p_token_hash=sha256_hex(session["token"]),
                   p_code_hash=code_hash(payload.code))[0][0]
        return {"practitioner_id": pid, "practitioner_name": practitioner_names([pid]).get(pid, "Practitioner")}

    @app.get("/api/portal/links")
    def my_links(session=Depends(current_session)):
        rows = call("portal_list_links", p_token_hash=sha256_hex(session["token"]))
        names = practitioner_names(r.practitioner_id for r in rows)
        return [{"link_id": r.link_id, "person_id": r.person_id, "subject_display_name": r.subject_display_name,
                 "practitioner_id": r.practitioner_id, "practitioner_name": names.get(r.practitioner_id, "Practitioner"),
                 "status": r.status, "active": r.active,
                 "via_guardian": r.via_guardian, "requested_at": iso(r.requested_at),
                 "activated_at": iso(r.activated_at)} for r in rows]

    @app.post("/api/portal/links/{link_id}/revoke")
    def revoke(link_id: int, session=Depends(current_session)):
        if not call("portal_revoke_link", p_token_hash=sha256_hex(session["token"]), p_link_id=link_id)[0][0]:
            raise HTTPException(404, "Not found")
        return {"link_id": link_id, "status": "REVOKED"}
