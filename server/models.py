from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, DateTime, Boolean, ForeignKey, Text, LargeBinary, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base
from .v2.fieldcrypt import EncryptedText

def now(): return datetime.now(timezone.utc)

class Tenant(Base):
    __tablename__='tenants'
    id:Mapped[int]=mapped_column(primary_key=True)
    slug:Mapped[str]=mapped_column(String(60),unique=True)
    display_name:Mapped[str]=mapped_column(String(150))

class Practitioner(Base):
    __tablename__='practitioners'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    email:Mapped[str]=mapped_column(String(250),unique=True)
    name:Mapped[str]=mapped_column(String(120))
    password_hash:Mapped[str]=mapped_column(String(256))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    tier:Mapped[str]=mapped_column(String(16),default='core')
    is_admin:Mapped[bool]=mapped_column(Boolean,default=False)

class LoginSession(Base):
    __tablename__='login_sessions'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    token_hash:Mapped[str]=mapped_column(String(64),unique=True)
    csrf_token:Mapped[str]=mapped_column(String(80))
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked:Mapped[bool]=mapped_column(Boolean,default=False)

class Service(Base):
    __tablename__='services'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    slug:Mapped[str]=mapped_column(String(90))
    title:Mapped[str]=mapped_column(String(130))
    description:Mapped[str]=mapped_column(Text)
    duration:Mapped[str]=mapped_column(String(70))
    price_label:Mapped[str]=mapped_column(String(70))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    __table_args__=(UniqueConstraint('tenant_id','slug'),)

class Athlete(Base):
    __tablename__='athletes'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    owner_practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    code:Mapped[str]=mapped_column(String(32))
    display_name:Mapped[str]=mapped_column(String(120))
    sport:Mapped[str]=mapped_column(String(80))
    athlete_group:Mapped[str]=mapped_column(String(40),default='not specified')
    service_consent_recorded_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    __table_args__=(UniqueConstraint('tenant_id','owner_practitioner_id','code',name='uq_athlete_owner_code'),)

class Case(Base):
    __tablename__='cases'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    owner_practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    title:Mapped[str]=mapped_column(String(140))
    category:Mapped[str]=mapped_column(String(60),default='Performance psychology')
    goal:Mapped[str]=mapped_column(Text,default='')
    status:Mapped[str]=mapped_column(String(20),default='active')
    next_review:Mapped[str|None]=mapped_column(String(10),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Note(Base):
    __tablename__='notes'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    case_id:Mapped[int]=mapped_column(ForeignKey('cases.id'),index=True)
    author_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'))
    note_date:Mapped[str]=mapped_column(String(10))
    # v2 phase 2: encrypted at the application layer (server/v2/fieldcrypt.py); the column is still text.
    content:Mapped[str]=mapped_column(EncryptedText('notes.content'))
    state:Mapped[str]=mapped_column(String(20),default='draft')
    revision:Mapped[int]=mapped_column(Integer,default=1)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Factor(Base):
    __tablename__='factors'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    owner_practitioner_id:Mapped[int|None]=mapped_column(ForeignKey('practitioners.id'),nullable=True,index=True)
    label:Mapped[str]=mapped_column(String(120))
    group_name:Mapped[str]=mapped_column(String(120),default='Daily monitoring')
    kind:Mapped[str]=mapped_column(String(20),default='monitoring')
    unit:Mapped[str]=mapped_column(String(40),default='points')
    low:Mapped[float]=mapped_column(Float,default=0)
    high:Mapped[float]=mapped_column(Float,default=10)
    higher_is:Mapped[str]=mapped_column(String(30),default='contextual')
    version:Mapped[int]=mapped_column(Integer,default=1)
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    __table_args__=(CheckConstraint('high > low'),)

class Observation(Base):
    __tablename__='observations'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    case_id:Mapped[int|None]=mapped_column(ForeignKey('cases.id'),nullable=True)
    factor_id:Mapped[int]=mapped_column(ForeignKey('factors.id'))
    value:Mapped[float]=mapped_column(Float)
    observed_on:Mapped[str]=mapped_column(String(10))
    source:Mapped[str]=mapped_column(String(32),default='practitioner')
    context:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Intervention(Base):
    __tablename__='interventions'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    name:Mapped[str]=mapped_column(String(130))
    purpose:Mapped[str]=mapped_column(String(240))
    method:Mapped[str]=mapped_column(Text)
    evidence_note:Mapped[str]=mapped_column(Text)
    reference_url:Mapped[str]=mapped_column(String(500))
    approved:Mapped[bool]=mapped_column(Boolean,default=False)

class Assignment(Base):
    __tablename__='assignments'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    case_id:Mapped[int]=mapped_column(ForeignKey('cases.id'))
    intervention_id:Mapped[int]=mapped_column(ForeignKey('interventions.id'))
    dosage:Mapped[str]=mapped_column(String(240))
    status:Mapped[str]=mapped_column(String(32),default='assigned')
    assigned_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Report(Base):
    __tablename__='reports'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'))
    case_id:Mapped[int]=mapped_column(ForeignKey('cases.id'))
    title:Mapped[str]=mapped_column(String(120))
    audience:Mapped[str]=mapped_column(String(32))
    body:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(20),default='draft')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Audit(Base):
    __tablename__='audit_events'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    actor_id:Mapped[int|None]=mapped_column(Integer,nullable=True)
    action:Mapped[str]=mapped_column(String(70))
    object_type:Mapped[str]=mapped_column(String(55))
    object_id:Mapped[int|None]=mapped_column(Integer,nullable=True)
    happened_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


# Beta 1.0 entities. Commercial information is separate from confidential case data.
class PractitionerProfile(Base):
    __tablename__='practitioner_profiles'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),unique=True,index=True)
    qualifications:Mapped[str]=mapped_column(Text,default='')
    experience_years:Mapped[int]=mapped_column(Integer,default=0)
    achievements:Mapped[str]=mapped_column(Text,default='')
    specialisms:Mapped[str]=mapped_column(String(250),default='')
    public_approved:Mapped[bool]=mapped_column(Boolean,default=False)
    avatar_key:Mapped[str]=mapped_column(String(32),default='portrait_01')  # filename allowlist, not external URL
    linked_account_id:Mapped[str|None]=mapped_column(String(120),nullable=True) # PRIVATE: never serialize publicly
    consultation_fee_paise:Mapped[int]=mapped_column(Integer,default=150000)
    avatar_bytes:Mapped[bytes|None]=mapped_column(LargeBinary,nullable=True)
    credentials_verified:Mapped[bool]=mapped_column(Boolean,default=False) # subscription does not constitute credential verification
    status:Mapped[str]=mapped_column(String(16),default='pending_approval',server_default='pending_approval',nullable=False)
    supabase_auth_uid:Mapped[str|None]=mapped_column(String(36),unique=True,nullable=True)
    email_verified_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    review_note:Mapped[str|None]=mapped_column(Text,nullable=True)
    more_info_requested_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    approved_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    approval_email_sent_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

class AppointmentSlot(Base):
    __tablename__='appointment_slots'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    starts_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    duration_minutes:Mapped[int]=mapped_column(Integer,default=50)
    amount_paise:Mapped[int]=mapped_column(Integer)
    currency:Mapped[str]=mapped_column(String(5),default='INR')
    available:Mapped[bool]=mapped_column(Boolean,default=True)
    __table_args__=(CheckConstraint('amount_paise >= 0'),)

class Booking(Base):
    __tablename__='bookings'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    slot_id:Mapped[int]=mapped_column(ForeignKey('appointment_slots.id'),index=True)
    contact_name:Mapped[str]=mapped_column(String(120))
    contact_email:Mapped[str]=mapped_column(String(250))
    is_minor:Mapped[bool]=mapped_column(Boolean,default=False)
    guardian_confirmed:Mapped[bool]=mapped_column(Boolean,default=False)
    contact_consent:Mapped[bool]=mapped_column(Boolean,default=False)
    video_consent:Mapped[bool]=mapped_column(Boolean,default=False)
    amount_paise:Mapped[int]=mapped_column(Integer)
    commission_bps:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(32),default='awaiting_payment')
    provider_order_id:Mapped[str|None]=mapped_column(String(100),unique=True,nullable=True)
    provider_payment_id:Mapped[str|None]=mapped_column(String(100),unique=True,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    __table_args__=(CheckConstraint('amount_paise >= 0'),CheckConstraint('commission_bps >= 0 AND commission_bps <= 10000'),)

class PaymentAllocation(Base):
    __tablename__='payment_allocations'
    id:Mapped[int]=mapped_column(primary_key=True)
    booking_id:Mapped[int]=mapped_column(ForeignKey('bookings.id'),index=True)
    provider_payment_id:Mapped[str]=mapped_column(String(100),unique=True)
    gross_paise:Mapped[int]=mapped_column(Integer)
    practitioner_paise:Mapped[int]=mapped_column(Integer)
    platform_paise:Mapped[int]=mapped_column(Integer)
    currency:Mapped[str]=mapped_column(String(5),default='INR')
    payout_status:Mapped[str]=mapped_column(String(36),default='allocation_only')
    provider_transfer_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class ConsultationRoom(Base):
    __tablename__='consultation_rooms'
    id:Mapped[int]=mapped_column(primary_key=True)
    booking_id:Mapped[int]=mapped_column(ForeignKey('bookings.id'),unique=True)
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class RoomAccess(Base):
    __tablename__='room_access'
    id:Mapped[int]=mapped_column(primary_key=True)
    room_id:Mapped[int]=mapped_column(ForeignKey('consultation_rooms.id'),index=True)
    role:Mapped[str]=mapped_column(String(24))
    token_hash:Mapped[str]=mapped_column(String(64),unique=True)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    consent_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    revoked:Mapped[bool]=mapped_column(Boolean,default=False)

class SessionReview(Base):
    __tablename__='session_reviews'
    id:Mapped[int]=mapped_column(primary_key=True)
    room_id:Mapped[int]=mapped_column(ForeignKey('consultation_rooms.id'),index=True)
    author_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'))
    time_offset_seconds:Mapped[int]=mapped_column(Integer,default=0)
    kind:Mapped[str]=mapped_column(String(30),default='practitioner_observation')
    note:Mapped[str]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class AssessmentAdministration(Base):
    __tablename__='assessment_administrations'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    case_id:Mapped[int]=mapped_column(ForeignKey('cases.id'),index=True)
    instrument_name:Mapped[str]=mapped_column(String(180))
    instrument_version:Mapped[str]=mapped_column(String(50),default='offline custom')
    observed_on:Mapped[str]=mapped_column(String(10))
    note:Mapped[str]=mapped_column(String(500),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class AssessmentSubscore(Base):
    __tablename__='assessment_subscores'
    id:Mapped[int]=mapped_column(primary_key=True)
    administration_id:Mapped[int]=mapped_column(ForeignKey('assessment_administrations.id'),index=True)
    parent_factor:Mapped[str]=mapped_column(String(120))
    sub_factor:Mapped[str]=mapped_column(String(120))
    unit:Mapped[str]=mapped_column(String(28))
    value:Mapped[float]=mapped_column(Float)
    source:Mapped[str]=mapped_column(String(32),default='offline-manual')

class InjuryEpisode(Base):
    __tablename__='injury_episodes'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    case_id:Mapped[int]=mapped_column(ForeignKey('cases.id'),index=True)
    injury_label:Mapped[str]=mapped_column(String(140))
    stage:Mapped[str]=mapped_column(String(45),default='early rehabilitation')
    start_date:Mapped[str]=mapped_column(String(10))
    closed:Mapped[bool]=mapped_column(Boolean,default=False)

class InjuryCheckIn(Base):
    __tablename__='injury_checkins'
    id:Mapped[int]=mapped_column(primary_key=True)
    episode_id:Mapped[int]=mapped_column(ForeignKey('injury_episodes.id'),index=True)
    observed_on:Mapped[str]=mapped_column(String(10))
    pain:Mapped[float]=mapped_column(Float)
    fear_of_reinjury:Mapped[float]=mapped_column(Float)
    confidence:Mapped[float]=mapped_column(Float)
    coping:Mapped[float]=mapped_column(Float)
    note:Mapped[str]=mapped_column(String(1000),default='')

class RetentionHold(Base):
    __tablename__='retention_holds'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    athlete_id:Mapped[int]=mapped_column(ForeignKey('athletes.id'),index=True)
    reason:Mapped[str]=mapped_column(String(250))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


class TermsAcceptance(Base):
    __tablename__='terms_acceptances'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    version:Mapped[str]=mapped_column(String(30))
    accepted_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


# Recurring practitioner membership is independent of client consultation revenue.
class PractitionerSubscription(Base):
    __tablename__='practitioner_subscriptions'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),unique=True,index=True)
    plan:Mapped[str]=mapped_column(String(12))  # core 199 INR or pro 499 INR per month
    amount_paise:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(20),default='pending')
    current_period_start:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    current_period_end:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    provider_subscription_id:Mapped[str|None]=mapped_column(String(100),unique=True,nullable=True)
    provider_plan_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    last_payment_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    test_mode:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    __table_args__=(CheckConstraint('amount_paise > 0'),)

class RegistrationIntent(Base):
    __tablename__='practitioner_registration_intents'
    id:Mapped[int]=mapped_column(primary_key=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),unique=True)
    token_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class SubscriptionCharge(Base):
    __tablename__='practitioner_subscription_charges'
    id:Mapped[int]=mapped_column(primary_key=True)
    subscription_id:Mapped[int]=mapped_column(ForeignKey('practitioner_subscriptions.id'),index=True)
    provider_payment_id:Mapped[str]=mapped_column(String(100),unique=True)
    amount_paise:Mapped[int]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


# Marketplace proposals. Independent of booking/payment allocation tables.
class PractitionerProgramme(Base):
    __tablename__='practitioner_programmes'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    title:Mapped[str]=mapped_column(String(120))
    duration_weeks:Mapped[int]=mapped_column(Integer)
    sessions_included:Mapped[int]=mapped_column(Integer)
    deliverables:Mapped[str]=mapped_column(Text)
    amount_paise:Mapped[int]=mapped_column(Integer)
    published:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    __table_args__=(CheckConstraint('duration_weeks BETWEEN 1 AND 52'),CheckConstraint('sessions_included BETWEEN 1 AND 50'),CheckConstraint('amount_paise BETWEEN 10000 AND 100000000'),)

class ProgrammeEnquiry(Base):
    __tablename__='programme_enquiries'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    practitioner_id:Mapped[int]=mapped_column(ForeignKey('practitioners.id'),index=True)
    programme_id:Mapped[int]=mapped_column(ForeignKey('practitioner_programmes.id'))
    contact_name:Mapped[str]=mapped_column(String(120))
    contact_email:Mapped[str]=mapped_column(String(250))
    quoted_amount_paise:Mapped[int]=mapped_column(Integer)
    message:Mapped[str]=mapped_column(String(350),default='')
    status:Mapped[str]=mapped_column(String(24),default='new')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class OrganisationQuoteRequest(Base):
    __tablename__='organisation_quote_requests'
    id:Mapped[int]=mapped_column(primary_key=True)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    organisation_name:Mapped[str]=mapped_column(String(160))
    contact_name:Mapped[str]=mapped_column(String(120))
    contact_email:Mapped[str]=mapped_column(String(250))
    practitioner_seats:Mapped[int]=mapped_column(Integer)
    athletes_estimate:Mapped[int]=mapped_column(Integer,default=0)
    requirements:Mapped[str]=mapped_column(String(700),default='')
    status:Mapped[str]=mapped_column(String(24),default='new')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
