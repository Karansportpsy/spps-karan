"""v2 clinical-schema tables (PostgreSQL schema ``spps_dev``; unqualified so SQLite tests use the same models).

Declared on their own ``V2Base`` so the v1 metadata keeps exactly 33 tables. Cross-metadata references to v1
tables (practitioners, athletes, ...) are plain integer columns here; the foreign keys, FORCE row-level security
and grants are created by the numbered SQL migrations (see MIGRATIONS.md). No athlete/guardian identity data is
stored in these tables.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, LargeBinary, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import now


class V2Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- Phase 1: registration and operations
REGISTRATION_STATES = ("applied", "email_verified", "under_review", "more_info_requested", "approved", "rejected")
REGISTRATION_TRANSITIONS = {
    "applied": {"email_verified"},
    # A confirmed account with no application text stays in email_verified until the form is sent.
    "email_verified": {"under_review"},
    "under_review": {"more_info_requested", "approved", "rejected"},
    "more_info_requested": {"under_review", "approved", "rejected"},
    "approved": {"more_info_requested", "rejected"},  # an approval can be withdrawn by the owner
    "rejected": {"under_review"},  # only if the owner reopens it
}


class RegistrationApplication(V2Base):
    """Server-side practitioner application, created at sign-up before the e-mail is confirmed."""
    __tablename__ = "registration_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    email: Mapped[str] = mapped_column(String(250), index=True)
    auth_uid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    claim_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="applied", index=True)
    has_application: Mapped[bool] = mapped_column(Boolean, default=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    qualifications: Mapped[str] = mapped_column(Text, default="")
    experience_years: Mapped[int] = mapped_column(Integer, default=0)
    achievements: Mapped[str] = mapped_column(Text, default="")
    specialisms: Mapped[str] = mapped_column(String(250), default="")
    practitioner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RegistrationTransition(V2Base):
    """Append-only audit of every registration state change: who and when (never application text)."""
    __tablename__ = "registration_transitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24))
    actor_kind: Mapped[str] = mapped_column(String(16))  # applicant | system | owner
    actor_practitioner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailDelivery(V2Base):
    """One row per review e-mail (approval / rejection / more information). Failures are shown to the owner."""
    __tablename__ = "email_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="failed", index=True)  # sent | failed | superseded
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str] = mapped_column(String(60), default="")  # class only, never addresses or bodies
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------- Phase 2: trust foundation
class RecordAccess(V2Base):
    """Who read or wrote which athlete record, when. Never content. Practitioners see their own athletes' rows;
    the owner sees counts only (a SECURITY DEFINER aggregate on PostgreSQL)."""
    __tablename__ = "record_access_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)  # record owner (the only possible reader)
    actor_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(8))  # read | write | export
    resource: Mapped[str] = mapped_column(String(120))  # route template, e.g. /api/cases/{case_id}
    request_id: Mapped[str] = mapped_column(String(64), default="")
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DataExport(V2Base):
    __tablename__ = "data_exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    files: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PractitionerSetting(V2Base):
    __tablename__ = "practitioner_settings"
    practitioner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    ai_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PractitionerClosure(V2Base):
    __tablename__ = "practitioner_closures"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[str] = mapped_column(String(12), default="closing")  # closing | cancelled | closed | restored | purged
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    notice_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooling_off_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClosureDisposition(V2Base):
    __tablename__ = "closure_dispositions"
    __table_args__ = (UniqueConstraint("closure_id", "athlete_id", name="uq_closure_disposition_athlete"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    closure_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, index=True)
    disposition: Mapped[str] = mapped_column(String(24))  # export_and_erase | keep_deactivated
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OwnerPractitionerArchive(V2Base):
    """Written once at closure: the practitioner's professional data and a minimal closure record. No client content."""
    __tablename__ = "owner_practitioner_archive"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    closure_id: Mapped[int] = mapped_column(Integer, index=True)
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- Phase 3: the practitioner record
from .fieldcrypt import EncryptedText  # noqa: E402

LEVELS = ("not specified", "recreational", "school", "club", "state", "national", "international", "professional")
AGE_GROUPS = ("not specified", "under-12", "12-14", "15-17", "18-24", "25-34", "35+")
MINOR_AGE_GROUPS = ("under-12", "12-14", "15-17")
CONSENT_STATES = ("not_recorded", "recorded", "withdrawn")


class AthleteProfile(V2Base):
    __tablename__ = "athlete_profiles"
    athlete_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    level: Mapped[str] = mapped_column(String(20), default="not specified")
    age_group: Mapped[str] = mapped_column(String(16), default="not specified")
    guardian_name: Mapped[str] = mapped_column(String(120), default="")
    guardian_email: Mapped[str] = mapped_column(String(250), default="")
    guardian_phone: Mapped[str] = mapped_column(String(24), default="")
    consent_status: Mapped[str] = mapped_column(String(16), default="not_recorded")
    consent_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    referral_source: Mapped[str] = mapped_column(String(120), default="")
    referral_notes: Mapped[str] = mapped_column(EncryptedText("athlete_profiles.referral_notes"), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NoteTemplate(V2Base):
    __tablename__ = "note_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)  # headings/prompts only; no client content
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NoteSignature(V2Base):
    """Signing locks a note. The hash proves the signed text has not changed since (amendments are separate)."""
    __tablename__ = "note_signatures"
    note_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NoteAmendment(V2Base):
    __tablename__ = "note_amendments"
    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(EncryptedText("note_amendments.content"))
    reason: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NoteDraft(V2Base):
    """Server autosave of a note being written. Committing it creates the (v1) note and deletes the draft."""
    __tablename__ = "note_drafts"
    __table_args__ = (UniqueConstraint("practitioner_id", "client_draft_id", name="uq_note_draft_client"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    client_draft_id: Mapped[str] = mapped_column(String(40))
    note_date: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(EncryptedText("note_drafts.content"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CaseFormulation(V2Base):
    """5Ps case formulation, written by the practitioner only. One current version per athlete; history kept."""
    __tablename__ = "case_formulations"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[str] = mapped_column(EncryptedText("case_formulations.body"))  # JSON of the five sections
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AthleteImport(V2Base):
    __tablename__ = "athlete_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(12), default="uploaded")  # uploaded | committed | discarded
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(EncryptedText("athlete_imports.payload"))  # parsed cells (JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- Phase 4: assessments over time
LICENCES = ("own", "open", "licensed_score_entry_only")
VALIDATION = ("practitioner_defined", "adapted", "published", "unverified")


class Instrument(V2Base):
    """An instrument defined as data. practitioner_id NULL = shipped with the platform (owner manages licence)."""
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str] = mapped_column(String(40), default="")
    version: Mapped[str] = mapped_column(String(40), default="1")
    licence: Mapped[str] = mapped_column(String(32), default="own")
    licence_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_status: Mapped[str] = mapped_column(String(24), default="practitioner_defined")
    response_min: Mapped[int] = mapped_column(Integer, default=1)
    response_max: Mapped[int] = mapped_column(Integer, default=5)
    response_labels: Mapped[str] = mapped_column(Text, default="[]")  # JSON list, may be empty
    assignable_to_athlete: Mapped[bool] = mapped_column(Boolean, default=False)
    source_note: Mapped[str] = mapped_column(String(400), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InstrumentSubscale(V2Base):
    __tablename__ = "instrument_subscales"
    __table_args__ = (UniqueConstraint("instrument_id", "key", name="uq_instrument_subscale_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
    scoring: Mapped[str] = mapped_column(String(8), default="sum")  # sum | mean
    min_score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float)
    bands: Mapped[str] = mapped_column(Text, default="[]")  # JSON [{min,max,label}]
    position: Mapped[int] = mapped_column(Integer, default=0)


class InstrumentItem(V2Base):
    __tablename__ = "instrument_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # never stored for licensed instruments
    subscale_key: Mapped[str] = mapped_column(String(40))
    reverse_scored: Mapped[bool] = mapped_column(Boolean, default=False)


class InstrumentAdministration(V2Base):
    __tablename__ = "instrument_administrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, index=True)
    instrument_id: Mapped[int] = mapped_column(Integer, index=True)
    administered_on: Mapped[str] = mapped_column(String(10))
    mode: Mapped[str] = mapped_column(String(16))  # items | subscale_entry
    source: Mapped[str] = mapped_column(String(16), default="practitioner")  # practitioner | athlete
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InstrumentResponse(V2Base):
    __tablename__ = "instrument_responses"
    __table_args__ = (UniqueConstraint("administration_id", "item_id", name="uq_instrument_response_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(Integer, index=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    value: Mapped[float] = mapped_column(Float)


class InstrumentScore(V2Base):
    __tablename__ = "instrument_scores"
    __table_args__ = (UniqueConstraint("administration_id", "subscale_key", name="uq_instrument_score_subscale"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(Integer, index=True)
    subscale_key: Mapped[str] = mapped_column(String(40))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL = missing, never imputed
    band: Mapped[str] = mapped_column(String(80), default="")


# ---------------------------------------------------------------- Phase 5: reports for coaches and the wider team
AUDIENCES = ("athlete", "guardian", "coach", "medical")


class ReportTemplate(V2Base):
    """fields: JSON list of {key, label, kind: narrative|scores|chart, audiences: [...], instrument_id?}.
    A field with no audiences is clinical-only: it never appears in any shared report."""
    __tablename__ = "report_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120))
    fields: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SharedReport(V2Base):
    __tablename__ = "shared_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, index=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(140))
    audience: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | signed
    values: Mapped[str] = mapped_column(EncryptedText("shared_reports.values"), default="{}")  # narrative per field
    snapshot: Mapped[str | None] = mapped_column(EncryptedText("shared_reports.snapshot"), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportConsent(V2Base):
    """Recorded consent of the athlete (or the guardian, for under-18s) to share reports with one audience."""
    __tablename__ = "report_consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, index=True)
    audience: Mapped[str] = mapped_column(String(12))
    given_by: Mapped[str] = mapped_column(String(10))  # athlete | guardian
    method: Mapped[str] = mapped_column(String(12))  # written | verbal | portal
    note: Mapped[str] = mapped_column(String(240), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportLink(V2Base):
    __tablename__ = "report_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True)
    consent_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    recipient_label: Mapped[str] = mapped_column(String(120), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportLinkView(V2Base):
    __tablename__ = "report_link_views"
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    outcome: Mapped[str] = mapped_column(String(12))  # shown | expired | revoked | withdrawn
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- Phase 7: Find a Sports Psychologist directory
DELIVERY = ("online", "in_person", "both")
VERIFICATION_KINDS = ("identity", "qualification", "registration")


class DirectoryProfile(V2Base):
    """The practitioner's opt-in public profile (their own professional data). Never read by public endpoints."""
    __tablename__ = "directory_profiles"
    practitioner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    qualifications: Mapped[str] = mapped_column(String(600), default="")
    sports: Mapped[str] = mapped_column(String(300), default="")
    athlete_levels: Mapped[str] = mapped_column(String(200), default="")
    age_groups: Mapped[str] = mapped_column(String(200), default="")
    languages: Mapped[str] = mapped_column(String(200), default="")
    delivery: Mapped[str] = mapped_column(String(10), default="online")
    city: Mapped[str] = mapped_column(String(80), default="")
    taking_new_clients: Mapped[bool] = mapped_column(Boolean, default=True)
    about: Mapped[str] = mapped_column(String(1200), default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | submitted | published | delisted
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_note: Mapped[str] = mapped_column(String(240), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DirectorySnapshot(V2Base):
    """The public read model, written only when the owner publishes. Public endpoints read ONLY this table."""
    __tablename__ = "directory_snapshot"
    practitioner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    qualifications: Mapped[str] = mapped_column(String(600))
    sports: Mapped[str] = mapped_column(String(300))
    athlete_levels: Mapped[str] = mapped_column(String(200))
    age_groups: Mapped[str] = mapped_column(String(200))
    languages: Mapped[str] = mapped_column(String(200))
    delivery: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(80))
    taking_new_clients: Mapped[bool] = mapped_column(Boolean)
    about: Mapped[str] = mapped_column(String(1200))
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    badges: Mapped[str] = mapped_column(Text)  # JSON [{kind, checked_on}]
    verified_until: Mapped[str] = mapped_column(String(10))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- Booking-only directory, phase 2: services
SERVICE_MODES = ("online", "in_person")


class PractitionerService(V2Base):
    """A practitioner's own service (no price: payments are off). Never read by public or portal handlers."""
    __tablename__ = "practitioner_services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(80))
    short_description: Mapped[str] = mapped_column(String(280), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DirectoryServiceSnapshot(V2Base):
    """Public read model of ACTIVE services; shown only while the practitioner is listed in directory_snapshot."""
    __tablename__ = "directory_service_snapshot"
    service_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(Integer, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    short_description: Mapped[str] = mapped_column(String(280), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
