BEGIN;
-- 0038 (v2 phase 6): the practitioner-invited athlete loop in the identity schema (accounts, invitations, consent links, assignments, check-ins, alerts, released summaries, crisis contacts).
--
-- Nothing here is clinical. Two restricted roles reach these tables:
--   * spps_identity_auth (server-only, before sign-in): accounts, sessions, invitations, crisis contacts, and the
--     link rows an invitation acts on — the same posture as the v1 practitioner auth role.
--   * spps_identity_portal: every loop data table, under policies keyed on the server-set transaction identity:
--     spps.loop_account_id (an athlete or guardian) or spps.practitioner_id + spps.tenant_id (a practitioner).
-- An athlete sees only their own active links; a guardian sees only participation counts and released summaries,
-- and raw answers only when BOTH the practitioner and the athlete have enabled it; a practitioner sees only links
-- they created. Ending a link removes the athlete's and guardian's access to its data in the same statement.
-- Tables are owned by spps_identity_owner like every v1 identity table.

CREATE TABLE spps_identity_dev.loop_accounts (
	id SERIAL NOT NULL, 
	email VARCHAR(250) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	verify_token_hash VARCHAR(64), 
	verify_expires_at TIMESTAMP WITH TIME ZONE, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE spps_identity_dev.loop_alert_rules (
	id SERIAL NOT NULL, 
	link_id INTEGER NOT NULL, 
	item VARCHAR(20) NOT NULL, 
	op VARCHAR(2) NOT NULL, 
	threshold FLOAT NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE spps_identity_dev.loop_alerts (
	id SERIAL NOT NULL, 
	link_id INTEGER NOT NULL, 
	checkin_id INTEGER NOT NULL, 
	reason VARCHAR(200) NOT NULL, 
	urgent BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	acknowledged_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE spps_identity_dev.loop_assignments (
	id SERIAL NOT NULL, 
	link_id INTEGER NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	payload TEXT NOT NULL, 
	schedule VARCHAR(160) NOT NULL, 
	due_on VARCHAR(10) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE spps_identity_dev.loop_checkin_plans (
	link_id SERIAL NOT NULL, 
	frequency VARCHAR(8) NOT NULL, 
	items TEXT NOT NULL, 
	starts_on VARCHAR(10) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (link_id)
);

CREATE TABLE spps_identity_dev.loop_checkins (
	id SERIAL NOT NULL, 
	link_id INTEGER NOT NULL, 
	due_on VARCHAR(10) NOT NULL, 
	answers TEXT NOT NULL, 
	urgent BOOLEAN NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_loop_checkin_due UNIQUE (link_id, due_on)
);

CREATE TABLE spps_identity_dev.loop_invites (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	link_id INTEGER NOT NULL, 
	email VARCHAR(250) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);

CREATE TABLE spps_identity_dev.loop_links (
	id SERIAL NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	practitioner_id INTEGER NOT NULL, 
	practitioner_name VARCHAR(120) NOT NULL, 
	practitioner_email VARCHAR(250) NOT NULL, 
	athlete_record_id INTEGER NOT NULL, 
	athlete_label VARCHAR(120) NOT NULL, 
	is_minor BOOLEAN NOT NULL, 
	athlete_account_id INTEGER, 
	guardian_account_id INTEGER, 
	status VARCHAR(20) NOT NULL, 
	scope TEXT NOT NULL, 
	guardian_consent_at TIMESTAMP WITH TIME ZONE, 
	guardian_consent_evidence VARCHAR(240) NOT NULL, 
	athlete_consent_at TIMESTAMP WITH TIME ZONE, 
	guardian_detail_by_practitioner BOOLEAN NOT NULL, 
	guardian_detail_by_athlete BOOLEAN NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	ended_by VARCHAR(12) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE spps_identity_dev.loop_sessions (
	id SERIAL NOT NULL, 
	account_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token VARCHAR(80) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);

CREATE TABLE spps_identity_dev.loop_settings (
	tenant_id SERIAL NOT NULL, 
	crisis_contacts TEXT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (tenant_id)
);

CREATE TABLE spps_identity_dev.loop_shared_summaries (
	id SERIAL NOT NULL, 
	link_id INTEGER NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	body TEXT NOT NULL, 
	for_guardian BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE spps_identity_dev.loop_submissions (
	id SERIAL NOT NULL, 
	assignment_id INTEGER NOT NULL, 
	link_id INTEGER NOT NULL, 
	answers TEXT NOT NULL, 
	scores TEXT NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

ALTER TABLE spps_identity_dev.loop_accounts ADD CHECK (role IN ('athlete','guardian')),
  ADD CHECK (status IN ('unverified','active','deactivated','deleted'));
ALTER TABLE spps_identity_dev.loop_links ADD CHECK (status IN ('invited','guardian_consented','active','ended')),
  ADD CHECK (status <> 'active' OR (athlete_account_id IS NOT NULL AND athlete_consent_at IS NOT NULL
             AND (NOT is_minor OR guardian_consent_at IS NOT NULL)));
ALTER TABLE spps_identity_dev.loop_invites ADD CHECK (kind IN ('athlete','guardian')),
  ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id);
ALTER TABLE spps_identity_dev.loop_sessions ADD FOREIGN KEY (account_id) REFERENCES spps_identity_dev.loop_accounts(id);
ALTER TABLE spps_identity_dev.loop_assignments ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id),
  ADD CHECK (kind IN ('instrument','programme')), ADD CHECK (status IN ('assigned','completed','withdrawn'));
ALTER TABLE spps_identity_dev.loop_submissions ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id),
  ADD FOREIGN KEY (assignment_id) REFERENCES spps_identity_dev.loop_assignments(id);
ALTER TABLE spps_identity_dev.loop_checkin_plans ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id),
  ADD CHECK (frequency IN ('weekly','daily'));
ALTER TABLE spps_identity_dev.loop_checkins ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id);
ALTER TABLE spps_identity_dev.loop_alert_rules ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id),
  ADD CHECK (op IN ('>=','<='));
ALTER TABLE spps_identity_dev.loop_alerts ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id),
  ADD FOREIGN KEY (checkin_id) REFERENCES spps_identity_dev.loop_checkins(id);
ALTER TABLE spps_identity_dev.loop_shared_summaries ADD FOREIGN KEY (link_id) REFERENCES spps_identity_dev.loop_links(id);
CREATE INDEX ix_loop_sessions_account_id ON spps_identity_dev.loop_sessions(account_id);
CREATE INDEX ix_loop_invites_link_id ON spps_identity_dev.loop_invites(link_id);
CREATE INDEX ix_loop_links_practitioner_id ON spps_identity_dev.loop_links(practitioner_id);
CREATE INDEX ix_loop_links_athlete_account_id ON spps_identity_dev.loop_links(athlete_account_id);
CREATE INDEX ix_loop_links_guardian_account_id ON spps_identity_dev.loop_links(guardian_account_id);
CREATE INDEX ix_loop_assignments_link_id ON spps_identity_dev.loop_assignments(link_id);
CREATE INDEX ix_loop_submissions_link_id ON spps_identity_dev.loop_submissions(link_id);
CREATE INDEX ix_loop_submissions_assignment_id ON spps_identity_dev.loop_submissions(assignment_id);
CREATE INDEX ix_loop_checkins_link_id ON spps_identity_dev.loop_checkins(link_id);
CREATE INDEX ix_loop_alert_rules_link_id ON spps_identity_dev.loop_alert_rules(link_id);
CREATE INDEX ix_loop_alerts_link_id ON spps_identity_dev.loop_alerts(link_id);
CREATE INDEX ix_loop_alerts_checkin_id ON spps_identity_dev.loop_alerts(checkin_id);
CREATE INDEX ix_loop_shared_summaries_link_id ON spps_identity_dev.loop_shared_summaries(link_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_accounts','loop_sessions','loop_invites','loop_links','loop_assignments',
     'loop_submissions','loop_checkin_plans','loop_checkins','loop_alert_rules','loop_alerts','loop_shared_summaries',
     'loop_settings'] LOOP
    EXECUTE format('ALTER TABLE spps_identity_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_identity_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- Identity of the current request (set by the server per transaction; never from client input).
CREATE FUNCTION spps_identity_dev.v2_loop_account() RETURNS integer LANGUAGE sql STABLE
  SET search_path=pg_catalog AS $$ SELECT nullif(current_setting('spps.loop_account_id', true), '')::integer $$;
CREATE FUNCTION spps_identity_dev.v2_loop_practitioner() RETURNS integer LANGUAGE sql STABLE
  SET search_path=pg_catalog AS $$ SELECT nullif(current_setting('spps.practitioner_id', true), '')::integer $$;
CREATE FUNCTION spps_identity_dev.v2_loop_tenant() RETURNS integer LANGUAGE sql STABLE
  SET search_path=pg_catalog AS $$ SELECT nullif(current_setting('spps.tenant_id', true), '')::integer $$;
-- The caller's relation to a link: practitioner | athlete | guardian | guardian_detail | NULL (no access).
-- Athlete and guardian access ends the moment the link is ended.
CREATE FUNCTION spps_identity_dev.v2_loop_role(p_link integer) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path=pg_catalog,spps_identity_dev AS $$
  SELECT CASE
    WHEN l.practitioner_id = spps_identity_dev.v2_loop_practitioner()
         AND l.tenant_id = spps_identity_dev.v2_loop_tenant() THEN 'practitioner'
    WHEN l.status = 'ended' THEN NULL
    WHEN l.status = 'active' AND l.athlete_account_id = spps_identity_dev.v2_loop_account() THEN 'athlete'
    WHEN l.guardian_account_id = spps_identity_dev.v2_loop_account()
         AND l.guardian_detail_by_practitioner AND l.guardian_detail_by_athlete AND l.status = 'active' THEN 'guardian_detail'
    WHEN l.guardian_account_id = spps_identity_dev.v2_loop_account() THEN 'guardian'
  END FROM spps_identity_dev.loop_links l WHERE l.id = p_link $$;
-- Guardian participation counts without access to the answers themselves.
CREATE FUNCTION spps_identity_dev.v2_loop_participation(p_link integer)
RETURNS TABLE (assignments_completed bigint, assignments_open bigint, checkins_submitted bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $$
  SELECT (SELECT count(*) FROM spps_identity_dev.loop_assignments a WHERE a.link_id = p_link AND a.status = 'completed'),
         (SELECT count(*) FROM spps_identity_dev.loop_assignments a WHERE a.link_id = p_link AND a.status = 'assigned'),
         (SELECT count(*) FROM spps_identity_dev.loop_checkins c WHERE c.link_id = p_link)
   WHERE spps_identity_dev.v2_loop_role(p_link) IN ('guardian','guardian_detail','practitioner') $$;
REVOKE ALL ON FUNCTION spps_identity_dev.v2_loop_account(), spps_identity_dev.v2_loop_practitioner(),
  spps_identity_dev.v2_loop_tenant(), spps_identity_dev.v2_loop_role(integer),
  spps_identity_dev.v2_loop_participation(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.v2_loop_account(), spps_identity_dev.v2_loop_practitioner(),
  spps_identity_dev.v2_loop_tenant(), spps_identity_dev.v2_loop_role(integer),
  spps_identity_dev.v2_loop_participation(integer) TO spps_identity_portal, spps_identity_auth;

-- spps_identity_auth: server-only pre-authentication work.
GRANT SELECT, INSERT, UPDATE ON spps_identity_dev.loop_accounts, spps_identity_dev.loop_sessions,
  spps_identity_dev.loop_invites, spps_identity_dev.loop_settings TO spps_identity_auth;
GRANT SELECT, UPDATE ON spps_identity_dev.loop_links TO spps_identity_auth;
GRANT SELECT ON spps_identity_dev.loop_checkin_plans, spps_identity_dev.loop_checkins TO spps_identity_auth;
GRANT USAGE ON SEQUENCE spps_identity_dev.loop_accounts_id_seq, spps_identity_dev.loop_sessions_id_seq,
  spps_identity_dev.loop_invites_id_seq TO spps_identity_auth;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_accounts','loop_sessions','loop_invites','loop_settings','loop_links',
                           'loop_checkin_plans','loop_checkins'] LOOP
    EXECUTE format('CREATE POLICY v2_loop_auth_server ON spps_identity_dev.%I TO spps_identity_auth USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

-- spps_identity_portal: loop data under the per-request identity.
GRANT SELECT, INSERT, UPDATE ON spps_identity_dev.loop_links, spps_identity_dev.loop_assignments,
  spps_identity_dev.loop_checkin_plans, spps_identity_dev.loop_alert_rules, spps_identity_dev.loop_alerts,
  spps_identity_dev.loop_shared_summaries TO spps_identity_portal;
GRANT SELECT, INSERT ON spps_identity_dev.loop_submissions, spps_identity_dev.loop_checkins TO spps_identity_portal;
GRANT SELECT, INSERT ON spps_identity_dev.loop_invites TO spps_identity_portal;
GRANT USAGE ON SEQUENCE spps_identity_dev.loop_links_id_seq, spps_identity_dev.loop_invites_id_seq,
  spps_identity_dev.loop_assignments_id_seq, spps_identity_dev.loop_submissions_id_seq,
  spps_identity_dev.loop_checkins_id_seq, spps_identity_dev.loop_alert_rules_id_seq,
  spps_identity_dev.loop_alerts_id_seq, spps_identity_dev.loop_shared_summaries_id_seq TO spps_identity_portal;

CREATE POLICY v2_loop_links_practitioner ON spps_identity_dev.loop_links TO spps_identity_portal
  USING (practitioner_id = spps_identity_dev.v2_loop_practitioner() AND tenant_id = spps_identity_dev.v2_loop_tenant())
  WITH CHECK (practitioner_id = spps_identity_dev.v2_loop_practitioner() AND tenant_id = spps_identity_dev.v2_loop_tenant());
CREATE POLICY v2_loop_links_member_read ON spps_identity_dev.loop_links FOR SELECT TO spps_identity_portal
  USING (athlete_account_id = spps_identity_dev.v2_loop_account() OR guardian_account_id = spps_identity_dev.v2_loop_account());
CREATE POLICY v2_loop_links_member_update ON spps_identity_dev.loop_links FOR UPDATE TO spps_identity_portal
  USING (status <> 'ended' AND (athlete_account_id = spps_identity_dev.v2_loop_account()
                                OR guardian_account_id = spps_identity_dev.v2_loop_account()))
  WITH CHECK (athlete_account_id = spps_identity_dev.v2_loop_account()
              OR guardian_account_id = spps_identity_dev.v2_loop_account());
CREATE POLICY v2_loop_invites_practitioner ON spps_identity_dev.loop_invites TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) = 'practitioner')
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner');

CREATE POLICY v2_loop_assignments_rw ON spps_identity_dev.loop_assignments TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete','guardian_detail'))
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete'));
CREATE POLICY v2_loop_submissions_read ON spps_identity_dev.loop_submissions FOR SELECT TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete','guardian_detail'));
CREATE POLICY v2_loop_submissions_athlete ON spps_identity_dev.loop_submissions FOR INSERT TO spps_identity_portal
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'athlete');
CREATE POLICY v2_loop_plans ON spps_identity_dev.loop_checkin_plans TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete','guardian_detail'))
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner');
CREATE POLICY v2_loop_checkins_read ON spps_identity_dev.loop_checkins FOR SELECT TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete','guardian_detail'));
CREATE POLICY v2_loop_checkins_athlete ON spps_identity_dev.loop_checkins FOR INSERT TO spps_identity_portal
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'athlete');
CREATE POLICY v2_loop_rules ON spps_identity_dev.loop_alert_rules TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete'))
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner');
CREATE POLICY v2_loop_alerts_read ON spps_identity_dev.loop_alerts FOR SELECT TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete'));
CREATE POLICY v2_loop_alerts_raise ON spps_identity_dev.loop_alerts FOR INSERT TO spps_identity_portal
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'athlete');
CREATE POLICY v2_loop_alerts_ack ON spps_identity_dev.loop_alerts FOR UPDATE TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) = 'practitioner')
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner');
CREATE POLICY v2_loop_summaries ON spps_identity_dev.loop_shared_summaries TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) IN ('practitioner','athlete','guardian_detail')
         OR (for_guardian AND spps_identity_dev.v2_loop_role(link_id) = 'guardian'))
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner');

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_accounts','loop_sessions','loop_invites','loop_links','loop_assignments',
     'loop_submissions','loop_checkin_plans','loop_checkins','loop_alert_rules','loop_alerts','loop_shared_summaries',
     'loop_settings'] LOOP
    EXECUTE format('ALTER TABLE spps_identity_dev.%I OWNER TO spps_identity_owner', t);
  END LOOP;
END $$;
COMMIT;
