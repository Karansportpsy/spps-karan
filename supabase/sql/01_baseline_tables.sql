-- SPPS production database, step 01 of the serial set: baseline_tables
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
BEGIN;
SET LOCAL search_path=spps_dev;

CREATE TABLE IF NOT EXISTS tenants (
	id SERIAL NOT NULL, 
	slug VARCHAR(60) NOT NULL, 
	display_name VARCHAR(150) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
)

;

CREATE TABLE IF NOT EXISTS audit_events (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	actor_id INTEGER, 
	action VARCHAR(70) NOT NULL, 
	object_type VARCHAR(55) NOT NULL, 
	object_id INTEGER, 
	happened_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)

;

CREATE TABLE IF NOT EXISTS interventions (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	name VARCHAR(130) NOT NULL, 
	purpose VARCHAR(240) NOT NULL, 
	method TEXT NOT NULL, 
	evidence_note TEXT NOT NULL, 
	reference_url VARCHAR(500) NOT NULL, 
	approved BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)

;

CREATE TABLE IF NOT EXISTS organisation_quote_requests (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	organisation_name VARCHAR(160) NOT NULL, 
	contact_name VARCHAR(120) NOT NULL, 
	contact_email VARCHAR(250) NOT NULL, 
	practitioner_seats INTEGER NOT NULL, 
	athletes_estimate INTEGER NOT NULL, 
	requirements VARCHAR(700) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)

;

CREATE TABLE IF NOT EXISTS practitioners (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	email VARCHAR(250) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	active BOOLEAN NOT NULL, 
	tier VARCHAR(16) NOT NULL, 
	is_admin BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	UNIQUE (email)
)

;

CREATE TABLE IF NOT EXISTS services (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	slug VARCHAR(90) NOT NULL, 
	title VARCHAR(130) NOT NULL, 
	description TEXT NOT NULL, 
	duration VARCHAR(70) NOT NULL, 
	price_label VARCHAR(70) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (tenant_id, slug), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)

;

CREATE TABLE IF NOT EXISTS appointment_slots (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	currency VARCHAR(5) NOT NULL, 
	available BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (amount_paise >= 0), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS athletes (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	owner_practitioner_id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	sport VARCHAR(80) NOT NULL, 
	athlete_group VARCHAR(40) NOT NULL, 
	service_consent_recorded_at TIMESTAMP WITH TIME ZONE, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_athlete_owner_code UNIQUE (tenant_id, owner_practitioner_id, code), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(owner_practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS factors (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	owner_practitioner_id INTEGER, 
	label VARCHAR(120) NOT NULL, 
	group_name VARCHAR(120) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	unit VARCHAR(40) NOT NULL, 
	low FLOAT NOT NULL, 
	high FLOAT NOT NULL, 
	higher_is VARCHAR(30) NOT NULL, 
	version INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (high > low), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(owner_practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS login_sessions (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token VARCHAR(80) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	UNIQUE (token_hash)
)

;

CREATE TABLE IF NOT EXISTS practitioner_profiles (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	qualifications TEXT NOT NULL, 
	experience_years INTEGER NOT NULL, 
	achievements TEXT NOT NULL, 
	specialisms VARCHAR(250) NOT NULL, 
	public_approved BOOLEAN NOT NULL, 
	avatar_key VARCHAR(32) NOT NULL, 
	linked_account_id VARCHAR(120), 
	consultation_fee_paise INTEGER NOT NULL, 
	avatar_bytes BYTEA, 
	credentials_verified BOOLEAN NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending_approval' NOT NULL, 
	supabase_auth_uid VARCHAR(36), 
	email_verified_at TIMESTAMP WITH TIME ZONE, 
	review_note TEXT, 
	more_info_requested_at TIMESTAMP WITH TIME ZONE, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	approval_email_sent_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	UNIQUE (supabase_auth_uid)
)

;

CREATE TABLE IF NOT EXISTS practitioner_programmes (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	duration_weeks INTEGER NOT NULL, 
	sessions_included INTEGER NOT NULL, 
	deliverables TEXT NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	published BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (duration_weeks BETWEEN 1 AND 52), 
	CHECK (sessions_included BETWEEN 1 AND 50), 
	CHECK (amount_paise BETWEEN 10000 AND 100000000), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS practitioner_registration_intents (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (practitioner_id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS practitioner_subscriptions (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	plan VARCHAR(12) NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	current_period_start TIMESTAMP WITH TIME ZONE, 
	current_period_end TIMESTAMP WITH TIME ZONE, 
	provider_subscription_id VARCHAR(100), 
	provider_plan_id VARCHAR(100), 
	last_payment_id VARCHAR(100), 
	test_mode BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (amount_paise > 0), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	UNIQUE (provider_subscription_id)
)

;

CREATE TABLE IF NOT EXISTS terms_acceptances (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	version VARCHAR(30) NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS bookings (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	slot_id INTEGER NOT NULL, 
	contact_name VARCHAR(120) NOT NULL, 
	contact_email VARCHAR(250) NOT NULL, 
	is_minor BOOLEAN NOT NULL, 
	guardian_confirmed BOOLEAN NOT NULL, 
	contact_consent BOOLEAN NOT NULL, 
	video_consent BOOLEAN NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	commission_bps INTEGER NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	provider_order_id VARCHAR(100), 
	provider_payment_id VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (amount_paise >= 0), 
	CHECK (commission_bps >= 0 AND commission_bps <= 10000), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	FOREIGN KEY(slot_id) REFERENCES appointment_slots (id), 
	UNIQUE (provider_order_id), 
	UNIQUE (provider_payment_id)
)

;

CREATE TABLE IF NOT EXISTS cases (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	owner_practitioner_id INTEGER NOT NULL, 
	title VARCHAR(140) NOT NULL, 
	category VARCHAR(60) NOT NULL, 
	goal TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	next_review VARCHAR(10), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(owner_practitioner_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS inquiries (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER, 
	service_id INTEGER NOT NULL, 
	contact_name VARCHAR(120) NOT NULL, 
	contact_email VARCHAR(250) NOT NULL, 
	contact_phone VARCHAR(24), 
	enquiring_for VARCHAR(30) NOT NULL, 
	sport VARCHAR(80) NOT NULL, 
	support_topic VARCHAR(60) NOT NULL, 
	contact_preference VARCHAR(24) NOT NULL, 
	scheduling_note VARCHAR(250), 
	consent_version VARCHAR(30) NOT NULL, 
	consent_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	linked_athlete_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	FOREIGN KEY(service_id) REFERENCES services (id), 
	FOREIGN KEY(linked_athlete_id) REFERENCES athletes (id)
)

;

CREATE TABLE IF NOT EXISTS practitioner_subscription_charges (
	id SERIAL NOT NULL, 
	subscription_id INTEGER NOT NULL, 
	provider_payment_id VARCHAR(100) NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subscription_id) REFERENCES practitioner_subscriptions (id), 
	UNIQUE (provider_payment_id)
)

;

CREATE TABLE IF NOT EXISTS programme_enquiries (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	programme_id INTEGER NOT NULL, 
	contact_name VARCHAR(120) NOT NULL, 
	contact_email VARCHAR(250) NOT NULL, 
	quoted_amount_paise INTEGER NOT NULL, 
	message VARCHAR(350) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	FOREIGN KEY(programme_id) REFERENCES practitioner_programmes (id)
)

;

CREATE TABLE IF NOT EXISTS retention_holds (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	reason VARCHAR(250) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id)
)

;

CREATE TABLE IF NOT EXISTS assessment_administrations (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	case_id INTEGER NOT NULL, 
	instrument_name VARCHAR(180) NOT NULL, 
	instrument_version VARCHAR(50) NOT NULL, 
	observed_on VARCHAR(10) NOT NULL, 
	note VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id)
)

;

CREATE TABLE IF NOT EXISTS assignments (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	case_id INTEGER NOT NULL, 
	intervention_id INTEGER NOT NULL, 
	dosage VARCHAR(240) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	assigned_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id), 
	FOREIGN KEY(intervention_id) REFERENCES interventions (id)
)

;

CREATE TABLE IF NOT EXISTS consultation_rooms (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (booking_id), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id)
)

;

CREATE TABLE IF NOT EXISTS injury_episodes (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	case_id INTEGER NOT NULL, 
	injury_label VARCHAR(140) NOT NULL, 
	stage VARCHAR(45) NOT NULL, 
	start_date VARCHAR(10) NOT NULL, 
	closed BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id)
)

;

CREATE TABLE IF NOT EXISTS notes (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	case_id INTEGER NOT NULL, 
	author_id INTEGER NOT NULL, 
	note_date VARCHAR(10) NOT NULL, 
	content TEXT NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id), 
	FOREIGN KEY(author_id) REFERENCES practitioners (id)
)

;

CREATE TABLE IF NOT EXISTS observations (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	case_id INTEGER, 
	factor_id INTEGER NOT NULL, 
	value FLOAT NOT NULL, 
	observed_on VARCHAR(10) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	context VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id), 
	FOREIGN KEY(factor_id) REFERENCES factors (id)
)

;

CREATE TABLE IF NOT EXISTS payment_allocations (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	provider_payment_id VARCHAR(100) NOT NULL, 
	gross_paise INTEGER NOT NULL, 
	practitioner_paise INTEGER NOT NULL, 
	platform_paise INTEGER NOT NULL, 
	currency VARCHAR(5) NOT NULL, 
	payout_status VARCHAR(36) NOT NULL, 
	provider_transfer_id VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id), 
	UNIQUE (provider_payment_id)
)

;

CREATE TABLE IF NOT EXISTS reports (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	case_id INTEGER NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	audience VARCHAR(32) NOT NULL, 
	body TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(case_id) REFERENCES cases (id)
)

;

CREATE TABLE IF NOT EXISTS assessment_subscores (
	id SERIAL NOT NULL, 
	administration_id INTEGER NOT NULL, 
	parent_factor VARCHAR(120) NOT NULL, 
	sub_factor VARCHAR(120) NOT NULL, 
	unit VARCHAR(28) NOT NULL, 
	value FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(administration_id) REFERENCES assessment_administrations (id)
)

;

CREATE TABLE IF NOT EXISTS injury_checkins (
	id SERIAL NOT NULL, 
	episode_id INTEGER NOT NULL, 
	observed_on VARCHAR(10) NOT NULL, 
	pain FLOAT NOT NULL, 
	fear_of_reinjury FLOAT NOT NULL, 
	confidence FLOAT NOT NULL, 
	coping FLOAT NOT NULL, 
	note VARCHAR(1000) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(episode_id) REFERENCES injury_episodes (id)
)

;

CREATE TABLE IF NOT EXISTS room_access (
	id SERIAL NOT NULL, 
	room_id INTEGER NOT NULL, 
	role VARCHAR(24) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consent_at TIMESTAMP WITH TIME ZONE, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(room_id) REFERENCES consultation_rooms (id), 
	UNIQUE (token_hash)
)

;

CREATE TABLE IF NOT EXISTS session_reviews (
	id SERIAL NOT NULL, 
	room_id INTEGER NOT NULL, 
	author_id INTEGER NOT NULL, 
	time_offset_seconds INTEGER NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(room_id) REFERENCES consultation_rooms (id), 
	FOREIGN KEY(author_id) REFERENCES practitioners (id)
)

;
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_id ON audit_events (tenant_id);
CREATE INDEX IF NOT EXISTS ix_interventions_tenant_id ON interventions (tenant_id);
CREATE INDEX IF NOT EXISTS ix_organisation_quote_requests_tenant_id ON organisation_quote_requests (tenant_id);
CREATE INDEX IF NOT EXISTS ix_practitioners_tenant_id ON practitioners (tenant_id);
CREATE INDEX IF NOT EXISTS ix_services_tenant_id ON services (tenant_id);
CREATE INDEX IF NOT EXISTS ix_appointment_slots_practitioner_id ON appointment_slots (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_athletes_owner_practitioner_id ON athletes (owner_practitioner_id);
CREATE INDEX IF NOT EXISTS ix_athletes_tenant_id ON athletes (tenant_id);
CREATE INDEX IF NOT EXISTS ix_factors_owner_practitioner_id ON factors (owner_practitioner_id);
CREATE INDEX IF NOT EXISTS ix_factors_tenant_id ON factors (tenant_id);
CREATE INDEX IF NOT EXISTS ix_login_sessions_practitioner_id ON login_sessions (practitioner_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_practitioner_profiles_practitioner_id ON practitioner_profiles (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_practitioner_programmes_practitioner_id ON practitioner_programmes (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_practitioner_programmes_tenant_id ON practitioner_programmes (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_practitioner_registration_intents_token_hash ON practitioner_registration_intents (token_hash);
CREATE UNIQUE INDEX IF NOT EXISTS ix_practitioner_subscriptions_practitioner_id ON practitioner_subscriptions (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_terms_acceptances_practitioner_id ON terms_acceptances (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_bookings_practitioner_id ON bookings (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_bookings_slot_id ON bookings (slot_id);
CREATE INDEX IF NOT EXISTS ix_bookings_tenant_id ON bookings (tenant_id);
CREATE INDEX IF NOT EXISTS ix_cases_athlete_id ON cases (athlete_id);
CREATE INDEX IF NOT EXISTS ix_cases_owner_practitioner_id ON cases (owner_practitioner_id);
CREATE INDEX IF NOT EXISTS ix_cases_tenant_id ON cases (tenant_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_practitioner_id ON inquiries (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_tenant_id ON inquiries (tenant_id);
CREATE INDEX IF NOT EXISTS ix_practitioner_subscription_charges_subscription_id ON practitioner_subscription_charges (subscription_id);
CREATE INDEX IF NOT EXISTS ix_programme_enquiries_practitioner_id ON programme_enquiries (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_programme_enquiries_tenant_id ON programme_enquiries (tenant_id);
CREATE INDEX IF NOT EXISTS ix_retention_holds_athlete_id ON retention_holds (athlete_id);
CREATE INDEX IF NOT EXISTS ix_retention_holds_tenant_id ON retention_holds (tenant_id);
CREATE INDEX IF NOT EXISTS ix_assessment_administrations_athlete_id ON assessment_administrations (athlete_id);
CREATE INDEX IF NOT EXISTS ix_assessment_administrations_case_id ON assessment_administrations (case_id);
CREATE INDEX IF NOT EXISTS ix_assessment_administrations_tenant_id ON assessment_administrations (tenant_id);
CREATE INDEX IF NOT EXISTS ix_assignments_athlete_id ON assignments (athlete_id);
CREATE INDEX IF NOT EXISTS ix_assignments_tenant_id ON assignments (tenant_id);
CREATE INDEX IF NOT EXISTS ix_injury_episodes_athlete_id ON injury_episodes (athlete_id);
CREATE INDEX IF NOT EXISTS ix_injury_episodes_case_id ON injury_episodes (case_id);
CREATE INDEX IF NOT EXISTS ix_injury_episodes_tenant_id ON injury_episodes (tenant_id);
CREATE INDEX IF NOT EXISTS ix_notes_case_id ON notes (case_id);
CREATE INDEX IF NOT EXISTS ix_notes_tenant_id ON notes (tenant_id);
CREATE INDEX IF NOT EXISTS ix_observations_athlete_id ON observations (athlete_id);
CREATE INDEX IF NOT EXISTS ix_observations_tenant_id ON observations (tenant_id);
CREATE INDEX IF NOT EXISTS ix_payment_allocations_booking_id ON payment_allocations (booking_id);
CREATE INDEX IF NOT EXISTS ix_reports_tenant_id ON reports (tenant_id);
CREATE INDEX IF NOT EXISTS ix_assessment_subscores_administration_id ON assessment_subscores (administration_id);
CREATE INDEX IF NOT EXISTS ix_injury_checkins_episode_id ON injury_checkins (episode_id);
CREATE INDEX IF NOT EXISTS ix_room_access_room_id ON room_access (room_id);
CREATE INDEX IF NOT EXISTS ix_session_reviews_room_id ON session_reviews (room_id);

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_audit_events (
	id SERIAL NOT NULL, 
	actor_account_id INTEGER, 
	action VARCHAR(60) NOT NULL, 
	object_type VARCHAR(40) NOT NULL, 
	object_id INTEGER, 
	detail VARCHAR(200) DEFAULT '' NOT NULL, 
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_auth_throttle (
	key_hash VARCHAR(64) NOT NULL, 
	window_start TIMESTAMP WITH TIME ZONE NOT NULL, 
	hits INTEGER NOT NULL, 
	PRIMARY KEY (key_hash)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_bookings (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	slot_id INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	currency VARCHAR(5) DEFAULT 'INR' NOT NULL, 
	commission_bps INTEGER DEFAULT 3000 NOT NULL, 
	person_id INTEGER NOT NULL, 
	link_id INTEGER NOT NULL, 
	requested_by_account_id INTEGER NOT NULL, 
	guardian_authority_id INTEGER, 
	status VARCHAR(32) NOT NULL, 
	video_requested BOOLEAN DEFAULT false NOT NULL, 
	video_closed_at TIMESTAMP WITH TIME ZONE, 
	client_note VARCHAR(300) DEFAULT '' NOT NULL, 
	decision_reason VARCHAR(240), 
	action_deadline TIMESTAMP WITH TIME ZONE, 
	rescheduled_from_id INTEGER, 
	provider VARCHAR(20), 
	provider_order_id VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_booking_status CHECK (status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED','DECLINED','RESCHEDULE_PROPOSED','EXPIRED','CANCELLED','REFUND_PENDING','REFUNDED','COMPLETED')), 
	CONSTRAINT ck_platform_booking_amount CHECK (amount_paise >= 0 AND commission_bps BETWEEN 0 AND 10000), 
	CONSTRAINT ck_platform_booking_range CHECK (ends_at > starts_at), 
	UNIQUE (provider_order_id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_link_invitations (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_record_id INTEGER NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	link_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (code_hash)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_notifications (
	id SERIAL NOT NULL, 
	account_id INTEGER, 
	practitioner_id INTEGER, 
	kind VARCHAR(60) NOT NULL, 
	object_type VARCHAR(40) NOT NULL, 
	object_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	delivery_status VARCHAR(20) DEFAULT 'PENDING' NOT NULL, 
	delivery_attempts INTEGER DEFAULT 0 NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_notification_recipient CHECK ((account_id IS NULL) <> (practitioner_id IS NULL)), 
	CONSTRAINT ck_notification_delivery CHECK (delivery_status IN ('PENDING','SENDING','DELIVERED','FAILED'))
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_organisation_licences (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	organisation_name VARCHAR(160) NOT NULL, 
	quote_reference INTEGER, 
	seats INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	valid_until TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_by_practitioner_id INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_licence_seats CHECK (seats BETWEEN 1 AND 500), 
	CONSTRAINT ck_licence_status CHECK (status IN ('ACTIVE','SUSPENDED','ENDED'))
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_people (
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	display_name VARCHAR(120) DEFAULT '' NOT NULL, 
	created_by_account_id INTEGER, 
	PRIMARY KEY (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_programmes (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	athlete_record_id INTEGER NOT NULL, 
	link_id INTEGER NOT NULL, 
	person_id INTEGER NOT NULL, 
	title VARCHAR(140) NOT NULL, 
	goal VARCHAR(1000) DEFAULT '' NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	response_reason VARCHAR(240), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	assigned_at TIMESTAMP WITH TIME ZONE, 
	responded_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_programme_status CHECK (status IN ('DRAFT','ASSIGNED','ACCEPTED','DECLINED','PAUSED','COMPLETED','DISCONTINUED'))
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_released_summaries (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	athlete_record_id INTEGER NOT NULL, 
	link_id INTEGER NOT NULL, 
	person_id INTEGER NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	title VARCHAR(140) NOT NULL, 
	body VARCHAR(2000) NOT NULL, 
	released_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	withdrawn_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_summary_kind CHECK (kind IN ('assessment_summary','progress_summary','session_summary'))
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_accounts (
	id SERIAL NOT NULL, 
	person_id INTEGER NOT NULL, 
	email_normalized VARCHAR(250) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	display_name VARCHAR(120) DEFAULT '' NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	adult_attested_at TIMESTAMP WITH TIME ZONE, 
	password_changed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_account_status CHECK (status IN ('REGISTERED_UNVERIFIED','VERIFIED','RESTRICTED','DISABLED')), 
	UNIQUE (person_id), 
	FOREIGN KEY(person_id) REFERENCES spps_identity_dev.platform_people (id), 
	UNIQUE (email_normalized)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_booking_payments (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	provider_order_id VARCHAR(100) NOT NULL, 
	provider_payment_id VARCHAR(100) NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	practitioner_paise INTEGER NOT NULL, 
	platform_paise INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	refund_id VARCHAR(100), 
	captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	refunded_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_payment_status CHECK (status IN ('CAPTURED','REFUND_PENDING','REFUNDED')), 
	CONSTRAINT ck_platform_payment_split CHECK (practitioner_paise + platform_paise = amount_paise AND amount_paise > 0), 
	FOREIGN KEY(booking_id) REFERENCES spps_identity_dev.platform_bookings (id), 
	UNIQUE (provider_payment_id), 
	UNIQUE (refund_id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_licence_seats (
	id SERIAL NOT NULL, 
	licence_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	assigned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(licence_id) REFERENCES spps_identity_dev.platform_organisation_licences (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_programme_tasks (
	id SERIAL NOT NULL, 
	programme_id INTEGER NOT NULL, 
	title VARCHAR(140) NOT NULL, 
	instructions VARCHAR(2000) DEFAULT '' NOT NULL, 
	frequency VARCHAR(12) NOT NULL, 
	due_date TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(14) DEFAULT 'ACTIVE' NOT NULL, 
	revision INTEGER DEFAULT 1 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_programme_task_frequency CHECK (frequency IN ('once','daily','weekly','as_needed')), 
	CONSTRAINT ck_programme_task_status CHECK (status IN ('ACTIVE','DISCONTINUED')), 
	FOREIGN KEY(programme_id) REFERENCES spps_identity_dev.platform_programmes (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_video_tickets (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	role VARCHAR(14) NOT NULL, 
	ticket_hash VARCHAR(64) NOT NULL, 
	issued_to_account_id INTEGER, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consent_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_video_ticket_role CHECK (role IN ('practitioner','guest')), 
	FOREIGN KEY(booking_id) REFERENCES spps_identity_dev.platform_bookings (id), 
	UNIQUE (ticket_hash)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.guardian_authorities (
	id SERIAL NOT NULL, 
	guardian_account_id INTEGER NOT NULL, 
	dependent_person_id INTEGER NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	scope VARCHAR(240) NOT NULL, 
	requested_at TIMESTAMP WITH TIME ZONE, 
	review_reason VARCHAR(240), 
	evidence_reference VARCHAR(160), 
	reviewed_by_actor_id INTEGER, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_guardian_authority_status CHECK (status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION','VERIFIED','REJECTED','EXPIRED','REVOKED')), 
	FOREIGN KEY(guardian_account_id) REFERENCES spps_identity_dev.platform_accounts (id), 
	FOREIGN KEY(dependent_person_id) REFERENCES spps_identity_dev.platform_people (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_account_sessions (
	id SERIAL NOT NULL, 
	account_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES spps_identity_dev.platform_accounts (id), 
	UNIQUE (token_hash)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_role_grants (
	id SERIAL NOT NULL, 
	account_id INTEGER NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_role CHECK (role IN ('ATHLETE','GUARDIAN','REVIEWER')), 
	CONSTRAINT ck_platform_role_status CHECK (status IN ('ACTIVE','REVOKED')), 
	FOREIGN KEY(account_id) REFERENCES spps_identity_dev.platform_accounts (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_task_submissions (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	task_revision INTEGER NOT NULL, 
	person_id INTEGER NOT NULL, 
	submitted_by_account_id INTEGER NOT NULL, 
	source VARCHAR(24) NOT NULL, 
	completed BOOLEAN NOT NULL, 
	self_rating INTEGER, 
	reflection VARCHAR(1000) DEFAULT '' NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_submission_source CHECK (source IN ('ATHLETE_SELF_REPORT','GUARDIAN_PROXY')), 
	CONSTRAINT ck_submission_rating CHECK (self_rating IS NULL OR self_rating BETWEEN 0 AND 10), 
	FOREIGN KEY(task_id) REFERENCES spps_identity_dev.platform_programme_tasks (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_verification_challenges (
	id SERIAL NOT NULL, 
	account_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	purpose VARCHAR(24) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_challenge_purpose CHECK (purpose IN ('EMAIL_VERIFY','PASSWORD_RESET')), 
	CONSTRAINT ck_platform_challenge_attempts CHECK (attempts >= 0), 
	FOREIGN KEY(account_id) REFERENCES spps_identity_dev.platform_accounts (id), 
	UNIQUE (token_hash)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_consent_events (
	id SERIAL NOT NULL, 
	person_id INTEGER NOT NULL, 
	actor_account_id INTEGER NOT NULL, 
	guardian_authority_id INTEGER, 
	practitioner_id INTEGER, 
	purpose VARCHAR(50) NOT NULL, 
	policy_version VARCHAR(40) NOT NULL, 
	decision VARCHAR(12) NOT NULL, 
	supersedes_event_id INTEGER, 
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_platform_consent_decision CHECK (decision IN ('GRANTED','WITHDRAWN')), 
	FOREIGN KEY(person_id) REFERENCES spps_identity_dev.platform_people (id), 
	FOREIGN KEY(actor_account_id) REFERENCES spps_identity_dev.platform_accounts (id), 
	FOREIGN KEY(guardian_authority_id) REFERENCES spps_identity_dev.guardian_authorities (id), 
	FOREIGN KEY(supersedes_event_id) REFERENCES spps_identity_dev.platform_consent_events (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.platform_task_reviews (
	id SERIAL NOT NULL, 
	submission_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	feedback VARCHAR(1000) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(submission_id) REFERENCES spps_identity_dev.platform_task_submissions (id)
)

;

CREATE TABLE IF NOT EXISTS spps_identity_dev.practitioner_athlete_links (
	id SERIAL NOT NULL, 
	person_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	athlete_record_id INTEGER NOT NULL, 
	consent_event_id INTEGER, 
	status VARCHAR(24) NOT NULL, 
	tenant_id INTEGER, 
	requested_by_account_id INTEGER, 
	guardian_authority_id INTEGER, 
	requested_at TIMESTAMP WITH TIME ZONE, 
	decided_reason VARCHAR(240), 
	approved_at TIMESTAMP WITH TIME ZONE, 
	activated_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_practitioner_athlete_link_status CHECK (status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE','DECLINED','EXPIRED','REVOKED')), 
	CONSTRAINT ck_active_link_has_approval CHECK (status != 'ACTIVE' OR (consent_event_id IS NOT NULL AND approved_at IS NOT NULL AND activated_at IS NOT NULL AND revoked_at IS NULL)), 
	FOREIGN KEY(person_id) REFERENCES spps_identity_dev.platform_people (id), 
	FOREIGN KEY(consent_event_id) REFERENCES spps_identity_dev.platform_consent_events (id)
)

;
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_bookings_person_id ON spps_identity_dev.platform_bookings (person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_bookings_practitioner_id ON spps_identity_dev.platform_bookings (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_link_invitations_practitioner_id ON spps_identity_dev.platform_link_invitations (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_notifications_account_id ON spps_identity_dev.platform_notifications (account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_notifications_practitioner_id ON spps_identity_dev.platform_notifications (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_organisation_licences_tenant_id ON spps_identity_dev.platform_organisation_licences (tenant_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_people_created_by_account_id ON spps_identity_dev.platform_people (created_by_account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_programmes_person_id ON spps_identity_dev.platform_programmes (person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_programmes_practitioner_id ON spps_identity_dev.platform_programmes (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_released_summaries_person_id ON spps_identity_dev.platform_released_summaries (person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_released_summaries_practi_4eb6 ON spps_identity_dev.platform_released_summaries (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_booking_payments_booking_id ON spps_identity_dev.platform_booking_payments (booking_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_licence_seats_licence_id ON spps_identity_dev.platform_licence_seats (licence_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_licence_seats_practitioner_id ON spps_identity_dev.platform_licence_seats (practitioner_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_programme_tasks_programme_id ON spps_identity_dev.platform_programme_tasks (programme_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_video_tickets_booking_id ON spps_identity_dev.platform_video_tickets (booking_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_guardian_authorities_dependent_person_id ON spps_identity_dev.guardian_authorities (dependent_person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_guardian_authorities_guardian_account_id ON spps_identity_dev.guardian_authorities (guardian_account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_account_sessions_account_id ON spps_identity_dev.platform_account_sessions (account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_role_grants_account_id ON spps_identity_dev.platform_role_grants (account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_task_submissions_task_id ON spps_identity_dev.platform_task_submissions (task_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_verification_challenges_a_1caf ON spps_identity_dev.platform_verification_challenges (account_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_consent_events_person_id ON spps_identity_dev.platform_consent_events (person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_platform_task_reviews_submission_id ON spps_identity_dev.platform_task_reviews (submission_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_practitioner_athlete_links_person_id ON spps_identity_dev.practitioner_athlete_links (person_id);
CREATE INDEX IF NOT EXISTS ix_spps_identity_dev_practitioner_athlete_links_practitioner_id ON spps_identity_dev.practitioner_athlete_links (practitioner_id);

INSERT INTO spps_dev.tenants(slug,display_name) VALUES ('winmindperform','WinMindPerform SPPS') ON CONFLICT (slug) DO NOTHING;
COMMIT;
