"""Stage 13A.6 — dependants, guardian authority, independent review and consent.

Every action is a SECURITY DEFINER function in the identity database that derives
the actor from the session token hash (migration 0006). This module only maps
HTTP to those functions; it never passes a client-supplied account ID.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .portal_auth import sha256_hex

CURRENT_POLICY_VERSION = "spps-consent-2026-11"
PURPOSES = Literal["PLATFORM_TERMS", "PRIVACY_NOTICE", "CASE_LINK", "PROGRAMME_PARTICIPATION",
                   "APPOINTMENT_BOOKING", "VIDEO_CONSULTATION", "DATA_SHARING_SUMMARY"]
SCOPE = Literal["appointment_metadata", "assigned_tasks", "booking_management", "consent_management",
                "own_submissions", "proxy_submission", "released_assessment_summary"]


class DependantIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)


class AuthorityIn(BaseModel):
    dependant_person_id: int = Field(gt=0)
    scopes: list[SCOPE] = Field(min_length=1, max_length=7)
    evidence_reference: str | None = Field(default=None, max_length=160)


class EvidenceIn(BaseModel):
    evidence_reference: str = Field(min_length=3, max_length=160)


class DecisionIn(BaseModel):
    decision: Literal["VERIFIED", "REJECTED"]
    reason: str = Field(min_length=5, max_length=240)
    valid_days: int | None = Field(default=365, ge=1, le=730)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=240)


class ConsentIn(BaseModel):
    subject_person_id: int = Field(gt=0)
    purpose: PURPOSES
    practitioner_id: int | None = Field(default=None, gt=0)
    decision: Literal["GRANTED", "WITHDRAWN"]
    policy_version: str = CURRENT_POLICY_VERSION


ERRORS = {"28000": (401, "Session expired"), "42501": (403, "This action is not available for your account"),
          "P0002": (404, "Not found"), "22023": (422, "Invalid details"),
          "23505": (409, "A request is already open for this dependant")}


def register_guardian_routes(app: FastAPI) -> None:
    engine = app.state.portal_auth_engine
    current_session = app.state.portal_current_session

    def call(name: str, session: dict, **params):
        params = {"p_token_hash": sha256_hex(session["token"]), **params}
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine.begin() as conn:
                return conn.execute(text(f"SELECT * FROM spps_identity_dev.{name}({placeholders})"),
                                    params).fetchall()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", "")
            if code in ERRORS:
                status, message = ERRORS[code]
                raise HTTPException(status, message) from None
            raise

    def scalar(name, session, **params):
        rows = call(name, session, **params)
        return rows[0][0] if rows else None

    def iso(v):
        return v.isoformat() if v else None

    # ------------------------------------------------------------ guardian
    @app.get("/api/portal/dependants")
    def list_dependants(session=Depends(current_session)):
        rows = call("guardian_list_dependants", session)
        return [{"person_id": r.person_id, "display_name": r.display_name, "created_at": iso(r.created_at),
                 "authority": None if r.authority_id is None else {
                     "id": r.authority_id, "status": r.authority_status,
                     "scopes": r.scopes.split(",") if r.scopes else [], "expires_at": iso(r.expires_at)}}
                for r in rows]

    @app.post("/api/portal/dependants", status_code=201)
    def add_dependant(payload: DependantIn, session=Depends(current_session)):
        return {"person_id": scalar("guardian_add_dependant", session, p_display_name=payload.display_name)}

    @app.post("/api/portal/guardian-authorities", status_code=201)
    def request_authority(payload: AuthorityIn, session=Depends(current_session)):
        authority = scalar("guardian_request_authority", session, p_person_id=payload.dependant_person_id,
                           p_scopes=",".join(payload.scopes), p_evidence_reference=payload.evidence_reference)
        return {"authority_id": authority,
                "status": "PENDING_VERIFICATION" if payload.evidence_reference else "PENDING_EVIDENCE"}

    @app.post("/api/portal/guardian-authorities/{authority_id}/evidence")
    def submit_evidence(authority_id: int, payload: EvidenceIn, session=Depends(current_session)):
        if not scalar("guardian_submit_evidence", session, p_authority_id=authority_id,
                      p_evidence_reference=payload.evidence_reference):
            raise HTTPException(404, "Not found")
        return {"status": "PENDING_VERIFICATION"}

    @app.post("/api/portal/guardian-authorities/{authority_id}/revoke")
    def revoke_authority(authority_id: int, session=Depends(current_session)):
        if not scalar("guardian_revoke_authority", session, p_authority_id=authority_id):
            raise HTTPException(404, "Not found")
        return {"status": "REVOKED"}

    # ------------------------------------------------------------ reviewer
    @app.get("/api/portal/review/authorities")
    def pending(session=Depends(current_session)):
        rows = call("reviewer_pending_authorities", session)
        return [{"authority_id": r.authority_id, "guardian_display_name": r.guardian_display_name,
                 "guardian_email": r.guardian_email, "dependant_display_name": r.dependant_display_name,
                 "scopes": r.scopes.split(","), "evidence_reference": r.evidence_reference,
                 "requested_at": iso(r.requested_at)} for r in rows]

    @app.post("/api/portal/review/authorities/{authority_id}/decision")
    def decide(authority_id: int, payload: DecisionIn, session=Depends(current_session)):
        if not scalar("reviewer_decide_authority", session, p_authority_id=authority_id,
                      p_decision=payload.decision, p_reason=payload.reason,
                      p_valid_days=payload.valid_days if payload.decision == "VERIFIED" else None):
            raise HTTPException(409, "Authority is not awaiting review or cannot be reviewed by you")
        return {"status": payload.decision}

    @app.post("/api/portal/review/authorities/{authority_id}/revoke")
    def reviewer_revoke(authority_id: int, payload: ReasonIn, session=Depends(current_session)):
        if not scalar("reviewer_revoke_authority", session, p_authority_id=authority_id, p_reason=payload.reason):
            raise HTTPException(409, "Authority cannot be revoked by you")
        return {"status": "REVOKED"}

    # ------------------------------------------------------------ consent
    @app.post("/api/portal/consents", status_code=201)
    def record_consent(payload: ConsentIn, session=Depends(current_session)):
        event = scalar("consent_record", session, p_subject_person_id=payload.subject_person_id,
                       p_practitioner_id=payload.practitioner_id, p_purpose=payload.purpose,
                       p_policy_version=payload.policy_version, p_decision=payload.decision)
        return {"event_id": event, "decision": payload.decision}

    @app.get("/api/portal/consents/{subject_person_id}")
    def consent_history(subject_person_id: int, session=Depends(current_session)):
        rows = call("consent_history", session, p_subject_person_id=subject_person_id)
        return [{"event_id": r.event_id, "purpose": r.purpose, "practitioner_id": r.practitioner_id,
                 "policy_version": r.policy_version, "decision": r.decision, "by_guardian": r.by_guardian,
                 "recorded_at": iso(r.recorded_at), "current": r.is_current} for r in rows]

    @app.get("/api/portal/consent-policy")
    def consent_policy():
        return {"policy_version": CURRENT_POLICY_VERSION,
                "purposes": list(PURPOSES.__args__)}
