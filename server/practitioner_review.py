"""Stage 13D — independent credential verification and directory publication.

Three separate gates (see subscription_routes.listing_eligible):
  credential:  submitted -> verified | rejected | suspended   (PractitionerProfile.credentials_verified)
  membership:  provider-confirmed active subscription          (active_membership)
  publication: approved | hidden | suspended                  (PractitionerProfile.public_approved)
Only a tenant administrator may decide, never about their own profile. Every decision
is audited. Publication approval is refused unless credentials are verified AND the
membership is active, so payment alone can never list a practitioner.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from .models import Audit, OrganisationQuoteRequest, Practitioner, PractitionerProfile, LoginSession, now
from .supabase_practitioner_auth import send_review_email, verify_access_token
from .subscription_routes import active_membership, listing_eligible


class CredentialDecisionIn(BaseModel):
    decision: Literal["verify", "reject", "suspend"]
    reason: str = Field(min_length=5, max_length=240)


class PublicationDecisionIn(BaseModel):
    decision: Literal["approve", "hide", "suspend"]
    reason: str = Field(min_length=5, max_length=240)


class ApprovalDecisionIn(BaseModel):
    decision: Literal['approve', 'reject', 'request_more_info']
    note: str = Field(min_length=5, max_length=1500)

class ApplicationAccessIn(BaseModel):
    access_token: str = Field(min_length=100, max_length=8192)


class ApplicationUpdateIn(ApplicationAccessIn):
    qualifications: str = Field(min_length=4, max_length=1000)
    experience_years: int = Field(ge=0, le=70)
    achievements: str = Field(default="", max_length=1500)
    specialisms: str = Field(default="", max_length=250)


def application_phase(profile) -> str:
    """Applicant-facing phase of a practitioner application."""
    if profile.status == "approved":
        return "approved"
    if profile.status == "rejected":
        return "rejected"
    if profile.more_info_requested_at:
        return "more_info"
    if (profile.review_note or "").strip():
        return "responded"
    return "pending"


def application_message(profile) -> str:
    """Text shown to a verified applicant who is not yet approved. The administrator's note is
    shown only after the applicant has proved control of the verified e-mail and password."""
    phase = application_phase(profile)
    note = (profile.review_note or "").strip()
    if phase == "rejected":
        text_ = "Your practitioner application was not approved."
        return text_ + (" Administrator note: " + note if note else "")
    if phase == "more_info":
        return ("The administrator needs more information before approving your application."
                + (" Administrator note: " + note if note else ""))
    if phase == "responded":
        return "Thank you. Your updated application is waiting for the administrator to review it."
    return "Your account is pending verification by the platform admin."


class QuoteStatusIn(BaseModel):
    status: Literal["contacted", "proposal_sent", "accepted", "declined", "closed"]


QUOTE_TRANSITIONS = {
    "new": {"contacted", "declined", "closed"},
    "contacted": {"proposal_sent", "declined", "closed"},
    "proposal_sent": {"accepted", "declined", "closed"},
    "accepted": {"closed"},
    "declined": {"closed"},
}


def register_review_routes(app: FastAPI, db_session, get_current, event, auth_session=None) -> None:
    auth_session = auth_session or db_session
    def admin(user):
        if not user.is_admin:
            raise HTTPException(403, "Administrator required")

    def target(db, auth, user, pid):
        if pid == user.id:
            raise HTTPException(403, "Reviewers cannot decide on their own profile")
        person = auth.scalar(select(Practitioner).where(Practitioner.id == pid, Practitioner.tenant_id == user.tenant_id))
        profile = db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == pid)) if person else None
        if not person or not profile:
            raise HTTPException(404, "Practitioner profile not found")
        return person, profile

    def last_decision(db, pid, prefix):
        return db.scalar(select(Audit.action).where(Audit.object_type == "practitioner", Audit.object_id == pid,
                                                    Audit.action.like(prefix + "%")).order_by(Audit.id.desc()))

    def state(db, auth, person, profile):
        credential = "verified" if profile.credentials_verified else (
            (last_decision(db, person.id, "credential.") or "credential.submitted").split(".", 1)[1])
        publication = "approved" if profile.public_approved else (
            (last_decision(db, person.id, "publication.") or "publication.pending_review").split(".", 1)[1])
        if publication == "approved" and not profile.public_approved:
            publication = "withdrawn"  # unlisted by a later credential decision or credential edit
        return {"practitioner_id": person.id, "name": person.name, "email": person.email,
                "tier": person.tier, "credential_status": credential,
                "membership_active": active_membership(auth, person),
                "publication_status": publication, "listed": listing_eligible(auth, person, profile, lifecycle_db=db),
                "status": profile.status, "review_note": profile.review_note,
                "email_verified": bool(profile.email_verified_at),
                "phase": application_phase(profile),
                "more_info_requested": bool(profile.more_info_requested_at),
                "is_owner_account": bool(person.is_admin),
                "qualifications": profile.qualifications, "experience_years": profile.experience_years,
                "achievements": profile.achievements, "specialisms": profile.specialisms}

    @app.get("/api/admin/practitioner-reviews")
    def review_queue(user=Depends(get_current), db=Depends(db_session), auth=Depends(auth_session)):
        admin(user)
        people = {p.id: p for p in auth.scalars(select(Practitioner).where(Practitioner.tenant_id == user.tenant_id))}
        profiles = db.scalars(select(PractitionerProfile).where(
            PractitionerProfile.practitioner_id.in_(list(people))).order_by(PractitionerProfile.practitioner_id)).all()
        return [{**state(db, auth, people[pr.practitioner_id], pr), "self": pr.practitioner_id == user.id}
                for pr in profiles]

    def applicant_context(clinical, auth, token):
        """Verified Supabase identity -> (person, profile) bound to that exact subject."""
        identity = verify_access_token(token, password_required=True)
        person = auth.scalar(select(Practitioner).where(Practitioner.email == identity["email"]))
        if not person and hasattr(app.state, "v2_promote"):
            # v2 phase 1: a confirmed account's server-side application is promoted on its first call.
            from .v2.registration import owner_email
            if identity["email"] != owner_email():
                from .models import Tenant
                tenant = clinical.scalar(select(Tenant).where(Tenant.slug == os.getenv("MODEL_C_PUBLIC_TENANT", "model-c-demo")))
                if tenant:
                    app.state.v2_promote(clinical, tenant, identity)
                    auth.expire_all()
                    person = auth.scalar(select(Practitioner).where(Practitioner.email == identity["email"]))
        if not person:
            return identity, None, None
        if os.getenv("SPPS_HOSTED_RLS", "false").lower() == "true":
            from .identity import bind_identity
            bind_identity(clinical, person)
        profile = clinical.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id == person.id))
        if not profile or str(profile.supabase_auth_uid or "") != identity["uid"]:
            raise HTTPException(403, "Verified account is not linked to this application")
        return identity, person, profile

    @app.post("/api/public/practitioner-application/status")
    def application_status(body: ApplicationAccessIn, clinical=Depends(db_session), auth=Depends(auth_session)):
        _, person, profile = applicant_context(clinical, auth, body.access_token)
        if not person:
            return {"phase": "none", "message": "No practitioner application was found for this email. Register first."}
        phase = application_phase(profile)
        editable = phase in ("pending", "more_info", "responded") and not person.is_admin
        return {"phase": phase, "message": application_message(profile), "can_update": editable,
                "qualifications": profile.qualifications if editable else "",
                "experience_years": profile.experience_years if editable else 0,
                "achievements": profile.achievements if editable else "",
                "specialisms": profile.specialisms if editable else ""}

    @app.post("/api/public/practitioner-application/update")
    def application_update(body: ApplicationUpdateIn, clinical=Depends(db_session), auth=Depends(auth_session)):
        identity, person, profile = applicant_context(clinical, auth, body.access_token)
        if not person or application_phase(profile) not in ("pending", "more_info", "responded") or person.is_admin:
            raise HTTPException(409, "There is no pending application to update")
        if clinical.get_bind().dialect.name == "postgresql":
            try:
                clinical.execute(text("""SELECT spps_dev.resubmit_own_application(
                    CAST(:uid AS uuid), :q, :y, :a, :s)"""),
                    {"uid": identity["uid"], "q": body.qualifications, "y": body.experience_years,
                     "a": body.achievements, "s": body.specialisms}).scalar_one()
                clinical.execute(text("SELECT spps_dev.v2_applicant_resubmitted(CAST(:uid AS uuid))"),
                                 {"uid": identity["uid"]})
                clinical.commit()
            except DBAPIError as exc:
                clinical.rollback()
                raise HTTPException(409, "There is no pending application to update") from exc
        else:
            profile.qualifications = body.qualifications.strip()
            profile.experience_years = body.experience_years
            profile.achievements = body.achievements
            profile.specialisms = body.specialisms
            profile.more_info_requested_at = None
            from .v2.registration import on_resubmission
            on_resubmission(clinical, person)
            clinical.commit()
        clinical.refresh(profile)
        return {"phase": application_phase(profile), "message": application_message(profile)}

    @app.post("/api/admin/practitioners/{pid}/approval")
    def approval_decision(pid: int, body: ApprovalDecisionIn, user=Depends(get_current),
                          db=Depends(db_session), auth=Depends(auth_session)):
        admin(user)
        person, profile = target(db, auth, user, pid)
        if body.decision == 'approve':
            if not profile.email_verified_at:
                raise HTTPException(409, "Practitioner must verify their email through Supabase Auth before approval")
            if len((profile.qualifications or '').strip()) < 5:
                raise HTTPException(409, "Professional qualifications are required")
            if not profile.supabase_auth_uid:
                raise HTTPException(409, "Practitioner Supabase Auth identity is not linked")
            profile.status = 'approved'
            profile.credentials_verified = True
            profile.public_approved = True
            profile.approved_at = now()
            profile.more_info_requested_at = None
            person.active = True
        else:
            # Disable access first, including already-issued cookies.
            person.active = False
            profile.status = 'rejected' if body.decision == 'reject' else 'pending_approval'
            profile.credentials_verified = False
            profile.public_approved = False
            profile.approved_at = None
            if body.decision == 'request_more_info':
                profile.more_info_requested_at = now()
        profile.review_note = body.note.strip()
        # v2 phase 1: state machine + audit (raises 409 for an unverified application).
        from .v2.registration import on_review_decision
        on_review_decision(db, user, person, body.decision)
        if body.decision != 'approve':
            # Session checks re-read person.active on each request.
            auth.query(LoginSession).filter(LoginSession.practitioner_id == pid).update(
                {LoginSession.revoked: True}, synchronize_session=False)
        event(db, user, 'approval.' + body.decision, 'practitioner', pid)
        # In the hosted dual-database model, apply disabling changes to auth
        # first; a later profile error therefore cannot leave clinical access open.
        if auth is not db:
            if body.decision != 'approve':
                auth.commit()
                db.commit()
            else:
                db.commit()
                auth.commit()
        else:
            db.commit()
        # v2 phase 1: every attempt is logged; a failure is shown to the owner with a resend button.
        from .v2 import mail as v2mail
        delivery = v2mail.deliver_review(db, tenant_id=user.tenant_id, actor_id=user.id, person=person,
                                         kind={'approve': 'approved', 'reject': 'rejected'}.get(body.decision, body.decision),
                                         note=body.note)
        delivered = delivery.status == 'sent'
        if delivered and body.decision == 'approve':
            profile.approval_email_sent_at = now()
            db.commit()
        result = state(db, auth, person, profile)
        return {**result, 'notification_sent': delivered, 'email_delivery_id': delivery.id,
                'email_failure_reason': delivery.failure_reason}

    @app.post("/api/admin/practitioners/{pid}/credentials")
    def credential_decision(pid: int, body: CredentialDecisionIn, user=Depends(get_current), db=Depends(db_session),
                            auth=Depends(auth_session)):
        admin(user)
        person, profile = target(db, auth, user, pid)
        if body.decision == "verify":
            if len((profile.qualifications or "").strip()) < 5:
                raise HTTPException(409, "No qualifications submitted for review")
            profile.credentials_verified = True
        else:
            profile.credentials_verified = False
            profile.public_approved = False  # rejected/suspended credentials unlist immediately
        action = {"verify": "credential.verified", "reject": "credential.rejected", "suspend": "credential.suspended"}
        event(db, user, action[body.decision], "practitioner", pid)
        db.commit()
        return state(db, auth, person, profile)

    @app.post("/api/admin/practitioners/{pid}/publication")
    def publication_decision(pid: int, body: PublicationDecisionIn, user=Depends(get_current), db=Depends(db_session),
                             auth=Depends(auth_session)):
        admin(user)
        person, profile = target(db, auth, user, pid)
        if body.decision == "approve":
            if not profile.credentials_verified:
                raise HTTPException(409, "Credentials must be independently verified before publication")
            if not active_membership(auth, person):
                raise HTTPException(409, "An active, provider-confirmed membership is required before publication")
            profile.public_approved = True
        else:
            profile.public_approved = False
        event(db, user, {"approve": "publication.approved", "hide": "publication.hidden",
                         "suspend": "publication.suspended"}[body.decision], "practitioner", pid)
        db.commit()
        return state(db, auth, person, profile)

    @app.post("/api/admin/organisation-quotes/{qid}/status")
    def quote_status(qid: int, body: QuoteStatusIn, user=Depends(get_current), db=Depends(db_session)):
        """Institutional licensing pipeline: enquiry -> contacted -> proposal -> accepted/declined."""
        admin(user)
        quote = db.scalar(select(OrganisationQuoteRequest).where(OrganisationQuoteRequest.id == qid,
                                                                 OrganisationQuoteRequest.tenant_id == user.tenant_id))
        if not quote:
            raise HTTPException(404, "Quote not found")
        if body.status not in QUOTE_TRANSITIONS.get(quote.status, set()):
            raise HTTPException(409, f"Cannot move a {quote.status} quote to {body.status}")
        quote.status = body.status
        event(db, user, f"organisation_quote.{body.status}", "organisation_quote", qid)
        db.commit()
        return {"id": qid, "status": quote.status}
