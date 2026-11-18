"""Fail-closed, pure Stage 13A policy *specification*, not runtime authorization.

Actual API authorization MUST independently verify database ownership, the
latest consent/guardian status, CSRF/session validity, nested IDs and RLS.
Never accept these context objects directly from client JSON or a JWT claim.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


SHAREABLE_PROJECTIONS = frozenset({
    "appointment_metadata", "assigned_tasks", "own_submissions",
    "released_assessment_summary",
})
PRIVATE_RESOURCES = frozenset({
    "case_notes", "draft_reports", "all_assessments", "private_injury_history",
    "practitioner_sessions", "payment_secrets",
})


@dataclass(frozen=True)
class PortalPrincipal:
    account_id: int
    person_id: int
    verified: bool
    session_active: bool
    roles: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GuardianGrant:
    guardian_account_id: int
    dependent_person_id: int
    status: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class CaseLink:
    person_id: int
    practitioner_id: int
    case_owner_practitioner_id: int
    status: str
    practitioner_approved: bool
    consent_current: bool
    released_projections: frozenset[str] = field(default_factory=frozenset)
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


def may_view_released_projection(
    principal: PortalPrincipal,
    *,
    subject_person_id: int,
    resource_practitioner_id: int,
    resource: str,
    link: CaseLink | None,
    guardian: GuardianGrant | None = None,
    at: datetime | None = None,
) -> bool:
    """Default-deny decision for a future *released projection* only.

    This function neither reads a database nor grants access to legacy
    clinical tables. All inputs must be derived from current server-side
    records on every request, never supplied by the browser.
    """
    now = at if at is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        return False
    if resource not in SHAREABLE_PROJECTIONS or resource in PRIVATE_RESOURCES:
        return False
    if not principal.verified or not principal.session_active:
        return False
    if link is None or link.status != "ACTIVE":
        return False
    if link.person_id != subject_person_id:
        return False
    if not link.practitioner_approved or not link.consent_current:
        return False
    if link.practitioner_id != resource_practitioner_id:
        return False
    if link.case_owner_practitioner_id != resource_practitioner_id:
        return False
    if resource not in link.released_projections:
        return False
    if link.revoked_at is not None:
        return False
    if link.expires_at is not None and (
        link.expires_at.tzinfo is None or link.expires_at <= now
    ):
        return False

    if "ATHLETE" in principal.roles and principal.person_id == subject_person_id:
        return True
    if "GUARDIAN" not in principal.roles or guardian is None:
        return False
    if guardian.guardian_account_id != principal.account_id:
        return False
    if guardian.dependent_person_id != subject_person_id:
        return False
    if guardian.status != "VERIFIED" or guardian.revoked_at is not None:
        return False
    if guardian.expires_at is not None and (
        guardian.expires_at.tzinfo is None or guardian.expires_at <= now
    ):
        return False
    return resource in guardian.scopes
