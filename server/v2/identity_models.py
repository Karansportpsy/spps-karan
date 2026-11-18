"""v2 athlete-loop tables in the identity schema ``spps_identity_dev`` (never in the clinical models).

Two connections reach them on PostgreSQL (migration 0038):
  * ``spps_identity_auth`` (server-only, pre-authentication): accounts, sessions and invitations, like the v1
    ``spps_dev_auth`` role does for practitioner sessions;
  * ``spps_identity_portal``: every loop data table, with row-level policies keyed on the transaction-local
    identity the server sets — ``spps.loop_account_id`` for an athlete/guardian request, or
    ``spps.practitioner_id``/``spps.tenant_id`` for a practitioner request.
Clinical data never lands here: the practitioner side writes only what the athlete is meant to see (a display
label, the assignable instrument's items, programme tasks, released summaries).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import now
from .fieldcrypt import EncryptedText

SCHEMA = "spps_identity_dev"


class V2IdentityBase(DeclarativeBase):
    pass


class AthleteAccount(V2IdentityBase):
    __tablename__ = "loop_accounts"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(250), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(10))  # athlete | guardian
    status: Mapped[str] = mapped_column(String(12), default="unverified")  # unverified | active | deactivated | deleted
    verify_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verify_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AthleteSession(V2IdentityBase):
    __tablename__ = "loop_sessions"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoopInvite(V2IdentityBase):
    __tablename__ = "loop_invites"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    email: Mapped[str] = mapped_column(String(250))
    kind: Mapped[str] = mapped_column(String(10))  # athlete | guardian
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopLink(V2IdentityBase):
    __tablename__ = "loop_links"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_name: Mapped[str] = mapped_column(String(120))
    practitioner_email: Mapped[str] = mapped_column(String(250), default="")  # for alert e-mails only
    athlete_record_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_label: Mapped[str] = mapped_column(String(120))
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    athlete_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    guardian_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # invited | guardian_consented | active | ended
    status: Mapped[str] = mapped_column(String(20), default="invited")
    scope: Mapped[str] = mapped_column(Text)  # JSON list shown on the consent screen
    guardian_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    guardian_consent_evidence: Mapped[str] = mapped_column(String(240), default="")
    athlete_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    guardian_detail_by_practitioner: Mapped[bool] = mapped_column(Boolean, default=False)
    guardian_detail_by_athlete: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str] = mapped_column(String(12), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopAssignment(V2IdentityBase):
    __tablename__ = "loop_assignments"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(12))  # instrument | programme
    title: Mapped[str] = mapped_column(String(160))
    payload: Mapped[str] = mapped_column(Text)  # instrument items+scale, or programme tasks
    schedule: Mapped[str] = mapped_column(String(160), default="")
    due_on: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(12), default="assigned")  # assigned | completed | withdrawn
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoopSubmission(V2IdentityBase):
    __tablename__ = "loop_submissions"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(Integer, index=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    answers: Mapped[str] = mapped_column(Text)
    scores: Mapped[str] = mapped_column(Text, default="{}")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopCheckinPlan(V2IdentityBase):
    __tablename__ = "loop_checkin_plans"
    __table_args__ = {"schema": SCHEMA}
    link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    frequency: Mapped[str] = mapped_column(String(8), default="weekly")  # weekly | daily
    items: Mapped[str] = mapped_column(Text)  # JSON list of item keys
    starts_on: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoopCheckin(V2IdentityBase):
    __tablename__ = "loop_checkins"
    __table_args__ = (UniqueConstraint("link_id", "due_on", name="uq_loop_checkin_due"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    due_on: Mapped[str] = mapped_column(String(10))
    answers: Mapped[str] = mapped_column(Text)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopAlertRule(V2IdentityBase):
    __tablename__ = "loop_alert_rules"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    item: Mapped[str] = mapped_column(String(20))
    op: Mapped[str] = mapped_column(String(2))  # >= | <=
    threshold: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoopAlert(V2IdentityBase):
    __tablename__ = "loop_alerts"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    checkin_id: Mapped[int] = mapped_column(Integer, index=True)
    reason: Mapped[str] = mapped_column(String(200))
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoopSharedSummary(V2IdentityBase):
    __tablename__ = "loop_shared_summaries"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    for_guardian: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopSettings(V2IdentityBase):
    """Owner-configured crisis contacts shown on every athlete screen."""
    __tablename__ = "loop_settings"
    __table_args__ = {"schema": SCHEMA}
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crisis_contacts: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- Phase 8: self-serve accounts (migration 0040)
class LoopRequest(V2IdentityBase):
    """The account's own copy of an enquiry it sent through the directory. Only the account reads it."""
    __tablename__ = "loop_requests"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    practitioner_id: Mapped[int] = mapped_column(Integer)
    practitioner_name: Mapped[str] = mapped_column(String(120))
    ref: Mapped[str] = mapped_column(String(64), unique=True)
    sport: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(String(10), default="sent")  # sent | accepted | declined | withdrawn
    link_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoopShareRequest(V2IdentityBase):
    """A signed coach-level report snapshot the athlete (guardian for under-18s) may approve sending to a coach."""
    __tablename__ = "loop_share_requests"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    practitioner_id: Mapped[int] = mapped_column(Integer)
    report_id: Mapped[int] = mapped_column(Integer)
    audience: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(140))
    recipient_label: Mapped[str] = mapped_column(String(120), default="")
    snapshot: Mapped[str] = mapped_column(EncryptedText("loop_share_requests.snapshot"))
    approver_role: Mapped[str] = mapped_column(String(10))  # athlete | guardian
    expires_in_days: Mapped[int] = mapped_column(Integer, default=14)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|approved|declined|withdrawn|revoked
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    decided_by_account: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoopShareView(V2IdentityBase):
    __tablename__ = "loop_share_views"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(Integer, index=True)
    outcome: Mapped[str] = mapped_column(String(12))  # shown | expired | revoked | withdrawn | ended
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
