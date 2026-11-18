-- SPPS production database, step 02 of the serial set: 0002_isolated_development_rls
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0002_isolated_development_rls.sql
BEGIN;
-- LOCAL DEVELOPMENT ONLY. Applied by tools.development_db to a fresh spps_dev
-- schema. Never apply to spps_pilot. All unlisted tables remain default-deny.
SET LOCAL search_path = spps_dev;
-- Athlete codes belong to a practitioner; another practitioner cannot reserve
-- a code or learn its existence via a tenant-wide uniqueness conflict.
ALTER TABLE athletes DROP CONSTRAINT IF EXISTS athletes_tenant_id_code_key;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='athletes'::regclass AND conname='uq_athlete_owner_code') THEN
    ALTER TABLE athletes ADD CONSTRAINT uq_athlete_owner_code UNIQUE (tenant_id, owner_practitioner_id, code);
  END IF;
END $$;
REVOKE ALL ON SCHEMA spps_dev FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA spps_dev FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA spps_dev FROM PUBLIC;
GRANT USAGE ON SCHEMA spps_dev TO spps_dev_app, spps_dev_auth;

DO $$ DECLARE t record; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='spps_dev' LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;

-- Invoker functions; no lookup, privilege elevation, or client-supplied JWT data.
CREATE FUNCTION practitioner_id() RETURNS integer LANGUAGE sql STABLE
  SECURITY INVOKER SET search_path = pg_catalog AS $$
  SELECT nullif(current_setting('spps.practitioner_id', true), '')::integer
$$;
CREATE FUNCTION tenant_id() RETURNS integer LANGUAGE sql STABLE
  SECURITY INVOKER SET search_path = pg_catalog AS $$
  SELECT nullif(current_setting('spps.tenant_id', true), '')::integer
$$;
REVOKE ALL ON FUNCTION practitioner_id(), tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION practitioner_id(), tenant_id() TO spps_dev_app, spps_dev_auth;

-- Authentication connection is server-only, cannot read or write clinical data,
-- cannot provision users, change memberships, alter schema, or assume app role.
GRANT SELECT ON practitioners, practitioner_subscriptions, login_sessions TO spps_dev_auth;
GRANT INSERT ON login_sessions, terms_acceptances, audit_events TO spps_dev_auth;
GRANT SELECT (id) ON terms_acceptances TO spps_dev_auth;
GRANT SELECT (id) ON audit_events TO spps_dev_app, spps_dev_auth;
GRANT UPDATE (revoked) ON login_sessions TO spps_dev_auth;
GRANT USAGE ON SEQUENCE login_sessions_id_seq, terms_acceptances_id_seq, audit_events_id_seq TO spps_dev_auth;
CREATE POLICY auth_lookup ON practitioners FOR SELECT TO spps_dev_auth USING (true);
CREATE POLICY membership_lookup ON practitioner_subscriptions FOR SELECT TO spps_dev_auth USING (true);
CREATE POLICY session_lookup ON login_sessions FOR SELECT TO spps_dev_auth USING (true);
CREATE POLICY session_create ON login_sessions FOR INSERT TO spps_dev_auth
  WITH CHECK (practitioner_id = spps_dev.practitioner_id());
CREATE POLICY session_revoke ON login_sessions FOR UPDATE TO spps_dev_auth
  USING (practitioner_id = spps_dev.practitioner_id()) WITH CHECK (practitioner_id = spps_dev.practitioner_id());
CREATE POLICY accept_terms ON terms_acceptances FOR INSERT TO spps_dev_auth
  WITH CHECK (practitioner_id = spps_dev.practitioner_id());
CREATE POLICY accepted_terms_id ON terms_acceptances FOR SELECT TO spps_dev_auth
  USING (practitioner_id = spps_dev.practitioner_id());
CREATE POLICY own_audit_id ON audit_events FOR SELECT TO spps_dev_app, spps_dev_auth
  USING (actor_id = spps_dev.practitioner_id() AND tenant_id = spps_dev.tenant_id());

GRANT SELECT, INSERT ON athletes, cases, factors, observations,
  assessment_administrations, assessment_subscores TO spps_dev_app;
GRANT UPDATE (service_consent_recorded_at) ON athletes TO spps_dev_app;
GRANT USAGE ON SEQUENCE athletes_id_seq, cases_id_seq, factors_id_seq,
  observations_id_seq, assessment_administrations_id_seq, assessment_subscores_id_seq,
  audit_events_id_seq TO spps_dev_app;
GRANT INSERT ON audit_events TO spps_dev_app;
CREATE POLICY append_audit ON audit_events FOR INSERT TO spps_dev_app, spps_dev_auth
  WITH CHECK (actor_id = spps_dev.practitioner_id() AND tenant_id = spps_dev.tenant_id());

CREATE POLICY athlete_owner ON athletes TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND owner_practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND owner_practitioner_id = spps_dev.practitioner_id());
CREATE POLICY case_owner ON cases TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND owner_practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM athletes a WHERE a.id=cases.athlete_id AND a.tenant_id=cases.tenant_id))
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND owner_practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM athletes a WHERE a.id=cases.athlete_id AND a.tenant_id=cases.tenant_id));
CREATE POLICY factor_read ON factors FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND spps_dev.practitioner_id() IS NOT NULL
    AND (owner_practitioner_id IS NULL OR owner_practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY factor_create ON factors FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND owner_practitioner_id = spps_dev.practitioner_id());
CREATE POLICY observation_owner ON observations TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id()
    AND EXISTS (SELECT 1 FROM athletes a WHERE a.id=observations.athlete_id AND a.tenant_id=observations.tenant_id)
    AND EXISTS (SELECT 1 FROM factors f WHERE f.id=observations.factor_id AND f.tenant_id=observations.tenant_id)
    AND (case_id IS NULL OR EXISTS (SELECT 1 FROM cases c WHERE c.id=observations.case_id AND c.athlete_id=observations.athlete_id)))
  WITH CHECK (tenant_id = spps_dev.tenant_id()
    AND EXISTS (SELECT 1 FROM athletes a WHERE a.id=observations.athlete_id AND a.tenant_id=observations.tenant_id)
    AND EXISTS (SELECT 1 FROM factors f WHERE f.id=observations.factor_id AND f.tenant_id=observations.tenant_id)
    AND (case_id IS NULL OR EXISTS (SELECT 1 FROM cases c WHERE c.id=observations.case_id AND c.athlete_id=observations.athlete_id)));
CREATE POLICY assessment_owner ON assessment_administrations TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND EXISTS (SELECT 1 FROM cases c
    WHERE c.id=assessment_administrations.case_id AND c.athlete_id=assessment_administrations.athlete_id AND c.tenant_id=assessment_administrations.tenant_id))
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND EXISTS (SELECT 1 FROM cases c
    WHERE c.id=assessment_administrations.case_id AND c.athlete_id=assessment_administrations.athlete_id AND c.tenant_id=assessment_administrations.tenant_id));
CREATE POLICY subscore_owner ON assessment_subscores TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM assessment_administrations a WHERE a.id=assessment_subscores.administration_id))
  WITH CHECK (EXISTS (SELECT 1 FROM assessment_administrations a WHERE a.id=assessment_subscores.administration_id));

-- Existing workspace reads needed by the shared UI. Writes outside milestone
-- one have no grants. Public directory, billing and room tokens stay closed.
GRANT SELECT ON inquiries, notes, assignments, reports, interventions,
  practitioner_profiles, appointment_slots, bookings, practitioner_programmes,
  programme_enquiries, injury_episodes, injury_checkins TO spps_dev_app;
CREATE POLICY own_inquiries ON inquiries FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_notes ON notes FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND author_id=spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM cases c WHERE c.id=notes.case_id));
CREATE POLICY own_assignments ON assignments FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND EXISTS (SELECT 1 FROM cases c WHERE c.id=assignments.case_id AND c.athlete_id=assignments.athlete_id));
CREATE POLICY own_reports ON reports FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND EXISTS (SELECT 1 FROM cases c WHERE c.id=reports.case_id AND c.athlete_id=reports.athlete_id));
CREATE POLICY reference_interventions ON interventions FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND spps_dev.practitioner_id() IS NOT NULL);
CREATE POLICY own_profile ON practitioner_profiles FOR SELECT TO spps_dev_app USING (practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_slots ON appointment_slots FOR SELECT TO spps_dev_app USING (practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_bookings ON bookings FOR SELECT TO spps_dev_app USING (tenant_id=spps_dev.tenant_id() AND practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_programmes ON practitioner_programmes FOR SELECT TO spps_dev_app USING (tenant_id=spps_dev.tenant_id() AND practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_programme_enquiries ON programme_enquiries FOR SELECT TO spps_dev_app USING (tenant_id=spps_dev.tenant_id() AND practitioner_id=spps_dev.practitioner_id());
CREATE POLICY own_injuries ON injury_episodes FOR SELECT TO spps_dev_app
  USING (tenant_id=spps_dev.tenant_id() AND EXISTS (SELECT 1 FROM cases c WHERE c.id=injury_episodes.case_id AND c.athlete_id=injury_episodes.athlete_id));
CREATE POLICY own_checkins ON injury_checkins FOR SELECT TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM injury_episodes e WHERE e.id=injury_checkins.episode_id));
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0002_isolated_development_rls.sql','62702cc80948d0f780f96fa560c114de50168940a8b8ed63a3697a5f4acb5215') ON CONFLICT (name) DO NOTHING;
COMMIT;
