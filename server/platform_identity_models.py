"""Stage 13A client identity ORM (separate spps_dev_identity database).

This registry is intentionally separate from server.db.Base; the clinical
33-table schema is never changed by it. From 13A.5 the portal API reaches these
tables only through restricted roles and the SECURITY DEFINER functions in
migrations/0005_identity_auth_functions.sql.
Do not call IdentityBase.metadata.create_all against the canonical database.
The legacy practitioner/athlete foreign keys and all RLS policies require
separately reviewed migrations before this schema can be deployed.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, MetaData, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now():
    return datetime.now(timezone.utc)


class IdentityBase(DeclarativeBase):
    metadata = MetaData(schema="spps_identity_dev")


class PlatformPerson(IdentityBase):
    """A person, including a dependent without an independent login."""

    __tablename__ = "platform_people"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # 13A.6: dependants (no login) are created by a guardian account; the label
    # is a guardian-chosen display name, not verified identity evidence.
    display_name: Mapped[str] = mapped_column(
        String(120), default="", server_default=text("''"), nullable=False
    )
    created_by_account_id: Mapped[int | None] = mapped_column(Integer, index=True)


class PlatformAccount(IdentityBase):
    """A login identity; never a practitioner account or a clinical case."""

    __tablename__ = "platform_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_people.id"), unique=True, nullable=False
    )
    email_normalized: Mapped[str] = mapped_column(String(250), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(120), default="", server_default=text("''"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="REGISTERED_UNVERIFIED", nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adult_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        CheckConstraint(
            "status IN ('REGISTERED_UNVERIFIED','VERIFIED','RESTRICTED','DISABLED')",
            name="ck_platform_account_status",
        ),
    )


class PlatformRoleGrant(IdentityBase):
    __tablename__ = "platform_role_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_accounts.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        # REVIEWER = independent guardian-authority verifier; never self-registered
        # and never granted any clinical access.
        CheckConstraint("role IN ('ATHLETE','GUARDIAN','REVIEWER')", name="ck_platform_role"),
        CheckConstraint("status IN ('ACTIVE','REVOKED')", name="ck_platform_role_status"),
    )


class PlatformAccountSession(IdentityBase):
    __tablename__ = "platform_account_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_accounts.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )


class PlatformAuthThrottle(IdentityBase):
    """Fixed-window attempt counters keyed by a SHA-256 of the throttle subject.

    Stored in PostgreSQL so limits hold across worker processes; no raw IP or
    e-mail address is stored.
    """

    __tablename__ = "platform_auth_throttle"
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PlatformVerificationChallenge(IdentityBase):
    __tablename__ = "platform_verification_challenges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_accounts.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('EMAIL_VERIFY','PASSWORD_RESET')",
            name="ck_platform_challenge_purpose",
        ),
        CheckConstraint("attempts >= 0", name="ck_platform_challenge_attempts"),
    )


class GuardianAuthority(IdentityBase):
    __tablename__ = "guardian_authorities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guardian_account_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_accounts.id"), nullable=False, index=True
    )
    dependent_person_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_people.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING_EVIDENCE", nullable=False)
    # Comma-separated, sorted set of scopes (see GUARDIAN_SCOPES).
    scope: Mapped[str] = mapped_column(String(240), nullable=False)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(String(240))
    evidence_reference: Mapped[str | None] = mapped_column(String(160))
    reviewed_by_actor_id: Mapped[int | None] = mapped_column(Integer)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION','VERIFIED',"
            "'REJECTED','EXPIRED','REVOKED')",
            name="ck_guardian_authority_status",
        ),
    )


class PlatformConsentEvent(IdentityBase):
    """Append-only intent; immutability needs a reviewed DB policy/trigger."""

    __tablename__ = "platform_consent_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_people.id"), nullable=False, index=True
    )
    actor_account_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_accounts.id"), nullable=False
    )
    guardian_authority_id: Mapped[int | None] = mapped_column(
        ForeignKey("spps_identity_dev.guardian_authorities.id")
    )
    practitioner_id: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    supersedes_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("spps_identity_dev.platform_consent_events.id")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        CheckConstraint("decision IN ('GRANTED','WITHDRAWN')", name="ck_platform_consent_decision"),
    )


class PractitionerAthleteLink(IdentityBase):
    """Links one platform subject to one practitioner-owned legacy athlete record.

    Cross-registry practitioner_id and athlete_record_id are deliberately
    untrusted integers until a reviewed PostgreSQL migration adds ownership
    foreign keys, authorization policies and role-specific access functions.
    """

    __tablename__ = "practitioner_athlete_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_people.id"), nullable=False, index=True
    )
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    athlete_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    consent_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("spps_identity_dev.platform_consent_events.id")
    )
    status: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(Integer)
    requested_by_account_id: Mapped[int | None] = mapped_column(Integer)
    guardian_authority_id: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_reason: Mapped[str | None] = mapped_column(String(240))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        # 13A.7: uniqueness of OPEN links is enforced by partial indexes in
        # migration 0007 so a person can re-link after revocation.
        CheckConstraint(
            "status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW',"
            "'ACTIVE','DECLINED','EXPIRED','REVOKED')",
            name="ck_practitioner_athlete_link_status",
        ),
        CheckConstraint(
            "status != 'ACTIVE' OR "
            "(consent_event_id IS NOT NULL AND approved_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND revoked_at IS NULL)",
            name="ck_active_link_has_approval",
        ),
    )


GUARDIAN_SCOPES = (
    "appointment_metadata", "assigned_tasks", "booking_management", "consent_management",
    "own_submissions", "proxy_submission", "released_assessment_summary",
)


class PlatformAuditEvent(IdentityBase):
    """Append-only identity audit trail (no personal data in `detail`)."""

    __tablename__ = "platform_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_account_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )


class PlatformLinkInvitation(IdentityBase):
    """13A.7: one-time code issued by a practitioner for ONE of their athlete records.

    practitioner_id / athlete_record_id / tenant_id refer to the clinical database.
    The API writes them only after verifying ownership there under clinical RLS
    (athlete_for); the identity database cannot see the clinical registry.
    """

    __tablename__ = "platform_link_invitations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    athlete_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link_id: Mapped[int | None] = mapped_column(Integer)


# ---------------------------------------------------------------- Stage 13B
BOOKING_STATUSES = (
    "PENDING_APPROVAL", "APPROVED_AWAITING_PAYMENT", "CONFIRMED", "DECLINED", "RESCHEDULE_PROPOSED",
    "EXPIRED", "CANCELLED", "REFUND_PENDING", "REFUNDED", "COMPLETED",
)


class PlatformBooking(IdentityBase):
    """Approval-first appointment for a LINKED athlete (or guardian on their behalf).

    Slot details are snapshotted from the practitioner's clinical appointment_slots
    row at request time. Overlap protection is enforced by exclusion constraints
    in migration 0009 (practitioner and person time ranges), not by a pre-query.
    """

    __tablename__ = "platform_bookings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(5), default="INR", server_default=text("'INR'"), nullable=False)
    commission_bps: Mapped[int] = mapped_column(Integer, default=3000, server_default=text("3000"), nullable=False)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    link_id: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by_account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guardian_authority_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    video_requested: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False)
    # Set by practitioner_end_video (migration 0016); a closed room can never issue tickets again.
    video_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_note: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(240))
    action_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rescheduled_from_id: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(20))
    provider_order_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN (" + ",".join(f"'{s}'" for s in BOOKING_STATUSES) + ")",
                        name="ck_platform_booking_status"),
        CheckConstraint("amount_paise >= 0 AND commission_bps BETWEEN 0 AND 10000", name="ck_platform_booking_amount"),
        CheckConstraint("ends_at > starts_at", name="ck_platform_booking_range"),
    )


class PlatformBookingPayment(IdentityBase):
    """Provider-verified payment ledger. One row per captured provider payment."""

    __tablename__ = "platform_booking_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_bookings.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    practitioner_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    refund_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('CAPTURED','REFUND_PENDING','REFUNDED')", name="ck_platform_payment_status"),
        CheckConstraint("practitioner_paise + platform_paise = amount_paise AND amount_paise > 0",
                        name="ck_platform_payment_split"),
    )


class PlatformNotification(IdentityBase):
    """Durable notification outbox (in-app inbox now; email/SMS/push workers later).

    Payloads carry identifiers and event kinds only — never clinical content.
    """

    __tablename__ = "platform_notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", server_default=text("'PENDING'"), nullable=False)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("(account_id IS NULL) <> (practitioner_id IS NULL)", name="ck_notification_recipient"),
        CheckConstraint("delivery_status IN ('PENDING','SENDING','DELIVERED','FAILED')", name="ck_notification_delivery"),
    )


# ---------------------------------------------------------------- Stage 13C
class PlatformProgramme(IdentityBase):
    """A practitioner-authored clinical programme for ONE active link.

    Distinct from the public marketplace `practitioner_programmes` offers.
    """

    __tablename__ = "platform_programmes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    athlete_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    link_id: Mapped[int] = mapped_column(Integer, nullable=False)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    goal: Mapped[str] = mapped_column(String(1000), default="", server_default=text("''"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    response_reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ASSIGNED','ACCEPTED','DECLINED','PAUSED','COMPLETED','DISCONTINUED')",
                        name="ck_platform_programme_status"),
    )


class PlatformProgrammeTask(IdentityBase):
    __tablename__ = "platform_programme_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    programme_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_programmes.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    instructions: Mapped[str] = mapped_column(String(2000), default="", server_default=text("''"), nullable=False)
    frequency: Mapped[str] = mapped_column(String(12), nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(14), default="ACTIVE", server_default=text("'ACTIVE'"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("frequency IN ('once','daily','weekly','as_needed')", name="ck_programme_task_frequency"),
        CheckConstraint("status IN ('ACTIVE','DISCONTINUED')", name="ck_programme_task_status"),
    )


class PlatformTaskSubmission(IdentityBase):
    """Append-only athlete self-report (or guardian proxy). Source-tagged; never a
    practitioner observation."""

    __tablename__ = "platform_task_submissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_programme_tasks.id"), nullable=False, index=True)
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by_account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    completed: Mapped[bool] = mapped_column(nullable=False)
    self_rating: Mapped[int | None] = mapped_column(Integer)
    reflection: Mapped[str] = mapped_column(String(1000), default="", server_default=text("''"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("source IN ('ATHLETE_SELF_REPORT','GUARDIAN_PROXY')", name="ck_submission_source"),
        CheckConstraint("self_rating IS NULL OR self_rating BETWEEN 0 AND 10", name="ck_submission_rating"),
    )


class PlatformTaskReview(IdentityBase):
    __tablename__ = "platform_task_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_task_submissions.id"), nullable=False, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)


class PlatformReleasedSummary(IdentityBase):
    """Text a practitioner EXPLICITLY releases to the portal (never notes or drafts)."""

    __tablename__ = "platform_released_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    athlete_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    link_id: Mapped[int] = mapped_column(Integer, nullable=False)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("kind IN ('assessment_summary','progress_summary','session_summary')", name="ck_summary_kind"),
    )


# ---------------------------------------------------------------- Phase 9: video for platform bookings
class PlatformVideoTicket(IdentityBase):
    """Short-lived, per-participant room ticket (hash only) for a CONFIRMED video booking."""

    __tablename__ = "platform_video_tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_bookings.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(14), nullable=False)
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_to_account_id: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('practitioner','guest')", name="ck_video_ticket_role"),)


# ---------------------------------------------------------------- 13D institutional licensing
class PlatformOrganisationLicence(IdentityBase):
    """An organisation's practitioner-seat licence (from an accepted institutional quote)."""

    __tablename__ = "platform_organisation_licences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    organisation_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quote_reference: Mapped[int | None] = mapped_column(Integer)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("seats BETWEEN 1 AND 500", name="ck_licence_seats"),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','ENDED')", name="ck_licence_status"),
    )


class PlatformLicenceSeat(IdentityBase):
    __tablename__ = "platform_licence_seats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    licence_id: Mapped[int] = mapped_column(
        ForeignKey("spps_identity_dev.platform_organisation_licences.id"), nullable=False, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
