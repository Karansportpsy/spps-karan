-- REVIEW ONLY, do not apply blindly. Fresh fictional staging schema.

CREATE SCHEMA IF NOT EXISTS spps_pilot;

SET search_path TO spps_pilot;

CREATE TABLE tenants (
	id SERIAL NOT NULL, 
	slug VARCHAR(60) NOT NULL, 
	display_name VARCHAR(150) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
);

CREATE TABLE audit_events (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	actor_id INTEGER, 
	action VARCHAR(70) NOT NULL, 
	object_type VARCHAR(55) NOT NULL, 
	object_id INTEGER, 
	happened_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);

CREATE TABLE interventions (
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
);

CREATE TABLE organisation_quote_requests (
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
);

CREATE TABLE practitioners (
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
);

CREATE TABLE services (
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
);

CREATE TABLE appointment_slots (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	currency VARCHAR(5) NOT NULL, 
	available BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (amount_paise > 0), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
);

CREATE TABLE athletes (
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
	UNIQUE (tenant_id, code), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(owner_practitioner_id) REFERENCES practitioners (id)
);

CREATE TABLE factors (
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
);

CREATE TABLE login_sessions (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token VARCHAR(80) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	UNIQUE (token_hash)
);

CREATE TABLE practitioner_profiles (
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
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
);

CREATE TABLE practitioner_programmes (
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
);

CREATE TABLE practitioner_registration_intents (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (practitioner_id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
);

CREATE TABLE practitioner_subscriptions (
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
);

CREATE TABLE terms_acceptances (
	id SERIAL NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	version VARCHAR(30) NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id)
);

CREATE TABLE bookings (
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
	CHECK (amount_paise > 0), 
	CHECK (commission_bps >= 0 AND commission_bps <= 10000), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(practitioner_id) REFERENCES practitioners (id), 
	FOREIGN KEY(slot_id) REFERENCES appointment_slots (id), 
	UNIQUE (provider_order_id), 
	UNIQUE (provider_payment_id)
);

CREATE TABLE cases (
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
);

CREATE TABLE inquiries (
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
);

CREATE TABLE practitioner_subscription_charges (
	id SERIAL NOT NULL, 
	subscription_id INTEGER NOT NULL, 
	provider_payment_id VARCHAR(100) NOT NULL, 
	amount_paise INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subscription_id) REFERENCES practitioner_subscriptions (id), 
	UNIQUE (provider_payment_id)
);

CREATE TABLE programme_enquiries (
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
);

CREATE TABLE retention_holds (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	reason VARCHAR(250) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id)
);

CREATE TABLE assessment_administrations (
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
);

CREATE TABLE assignments (
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
);

CREATE TABLE consultation_rooms (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (booking_id), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id)
);

CREATE TABLE injury_episodes (
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
);

CREATE TABLE notes (
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
);

CREATE TABLE observations (
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
);

CREATE TABLE payment_allocations (
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
);

CREATE TABLE reports (
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
);

CREATE TABLE assessment_subscores (
	id SERIAL NOT NULL, 
	administration_id INTEGER NOT NULL, 
	parent_factor VARCHAR(120) NOT NULL, 
	sub_factor VARCHAR(120) NOT NULL, 
	unit VARCHAR(28) NOT NULL, 
	value FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(administration_id) REFERENCES assessment_administrations (id)
);

CREATE TABLE injury_checkins (
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
);

CREATE TABLE room_access (
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
);

CREATE TABLE session_reviews (
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
);

CREATE INDEX ix_audit_events_tenant_id ON audit_events (tenant_id);

CREATE INDEX ix_interventions_tenant_id ON interventions (tenant_id);

CREATE INDEX ix_organisation_quote_requests_tenant_id ON organisation_quote_requests (tenant_id);

CREATE INDEX ix_practitioners_tenant_id ON practitioners (tenant_id);

CREATE INDEX ix_services_tenant_id ON services (tenant_id);

CREATE INDEX ix_appointment_slots_practitioner_id ON appointment_slots (practitioner_id);

CREATE INDEX ix_athletes_owner_practitioner_id ON athletes (owner_practitioner_id);

CREATE INDEX ix_athletes_tenant_id ON athletes (tenant_id);

CREATE INDEX ix_factors_owner_practitioner_id ON factors (owner_practitioner_id);

CREATE INDEX ix_factors_tenant_id ON factors (tenant_id);

CREATE INDEX ix_login_sessions_practitioner_id ON login_sessions (practitioner_id);

CREATE UNIQUE INDEX ix_practitioner_profiles_practitioner_id ON practitioner_profiles (practitioner_id);

CREATE INDEX ix_practitioner_programmes_tenant_id ON practitioner_programmes (tenant_id);

CREATE INDEX ix_practitioner_programmes_practitioner_id ON practitioner_programmes (practitioner_id);

CREATE UNIQUE INDEX ix_practitioner_registration_intents_token_hash ON practitioner_registration_intents (token_hash);

CREATE UNIQUE INDEX ix_practitioner_subscriptions_practitioner_id ON practitioner_subscriptions (practitioner_id);

CREATE INDEX ix_terms_acceptances_practitioner_id ON terms_acceptances (practitioner_id);

CREATE INDEX ix_bookings_tenant_id ON bookings (tenant_id);

CREATE INDEX ix_bookings_slot_id ON bookings (slot_id);

CREATE INDEX ix_bookings_practitioner_id ON bookings (practitioner_id);

CREATE INDEX ix_cases_owner_practitioner_id ON cases (owner_practitioner_id);

CREATE INDEX ix_cases_athlete_id ON cases (athlete_id);

CREATE INDEX ix_cases_tenant_id ON cases (tenant_id);

CREATE INDEX ix_inquiries_practitioner_id ON inquiries (practitioner_id);

CREATE INDEX ix_inquiries_tenant_id ON inquiries (tenant_id);

CREATE INDEX ix_practitioner_subscription_charges_subscription_id ON practitioner_subscription_charges (subscription_id);

CREATE INDEX ix_programme_enquiries_practitioner_id ON programme_enquiries (practitioner_id);

CREATE INDEX ix_programme_enquiries_tenant_id ON programme_enquiries (tenant_id);

CREATE INDEX ix_retention_holds_athlete_id ON retention_holds (athlete_id);

CREATE INDEX ix_retention_holds_tenant_id ON retention_holds (tenant_id);

CREATE INDEX ix_assessment_administrations_athlete_id ON assessment_administrations (athlete_id);

CREATE INDEX ix_assessment_administrations_tenant_id ON assessment_administrations (tenant_id);

CREATE INDEX ix_assessment_administrations_case_id ON assessment_administrations (case_id);

CREATE INDEX ix_assignments_tenant_id ON assignments (tenant_id);

CREATE INDEX ix_assignments_athlete_id ON assignments (athlete_id);

CREATE INDEX ix_injury_episodes_athlete_id ON injury_episodes (athlete_id);

CREATE INDEX ix_injury_episodes_tenant_id ON injury_episodes (tenant_id);

CREATE INDEX ix_injury_episodes_case_id ON injury_episodes (case_id);

CREATE INDEX ix_notes_case_id ON notes (case_id);

CREATE INDEX ix_notes_tenant_id ON notes (tenant_id);

CREATE INDEX ix_observations_tenant_id ON observations (tenant_id);

CREATE INDEX ix_observations_athlete_id ON observations (athlete_id);

CREATE INDEX ix_payment_allocations_booking_id ON payment_allocations (booking_id);

CREATE INDEX ix_reports_tenant_id ON reports (tenant_id);

CREATE INDEX ix_assessment_subscores_administration_id ON assessment_subscores (administration_id);

CREATE INDEX ix_injury_checkins_episode_id ON injury_checkins (episode_id);

CREATE INDEX ix_room_access_room_id ON room_access (room_id);

CREATE INDEX ix_session_reviews_room_id ON session_reviews (room_id);
