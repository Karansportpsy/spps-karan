-- Stage 13A.4: execute ONLY through the guarded isolated-identity provisioner.
-- No public or clinical tables. Eight ORM tables must exist in spps_identity_dev.
REVOKE ALL ON SCHEMA spps_identity_dev FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA spps_identity_dev FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA spps_identity_dev FROM PUBLIC;
GRANT USAGE ON SCHEMA spps_identity_dev TO spps_identity_portal,spps_identity_auth;

DO $$ DECLARE t record; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='spps_identity_dev' LOOP
  EXECUTE format('ALTER TABLE spps_identity_dev.%I OWNER TO spps_identity_owner',t.tablename);
  EXECUTE format('ALTER TABLE spps_identity_dev.%I ENABLE ROW LEVEL SECURITY',t.tablename);
  EXECUTE format('ALTER TABLE spps_identity_dev.%I FORCE ROW LEVEL SECURITY',t.tablename);
  EXECUTE format('CREATE POLICY identity_owner_read ON spps_identity_dev.%I FOR SELECT TO spps_identity_owner USING (true)',t.tablename);
 END LOOP;
END $$;

-- A forged account ID GUC is useless. Authorization requires a random session
-- bearer whose SHA256 matches an active verified account. The token is neither
-- returned by nor queryable from the portal tables. Future API must enforce
-- HTTPS, CSRF, credential review, session rotation and anti-abuse controls.
CREATE FUNCTION spps_identity_dev.current_account_id() RETURNS integer
 LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT a.id FROM spps_identity_dev.platform_account_sessions s
 JOIN spps_identity_dev.platform_accounts a ON a.id=s.account_id
 WHERE s.token_hash=encode(sha256(convert_to(
   nullif(current_setting('spps_identity.session_token',true),''),'UTF8')),'hex')
 AND s.revoked_at IS NULL AND s.expires_at>CURRENT_TIMESTAMP
 AND a.status='VERIFIED' AND a.verified_at IS NOT NULL
 ORDER BY s.id DESC LIMIT 1
 $body$;
ALTER FUNCTION spps_identity_dev.current_account_id() OWNER TO spps_identity_owner;
REVOKE ALL ON FUNCTION spps_identity_dev.current_account_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.current_account_id() TO spps_identity_portal;

CREATE FUNCTION spps_identity_dev.portal_can_view_person(subject_id integer)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT COALESCE(
 EXISTS (
  SELECT 1 FROM spps_identity_dev.platform_accounts a
  JOIN spps_identity_dev.platform_role_grants r ON r.account_id=a.id
  WHERE a.id=spps_identity_dev.current_account_id()
   AND a.person_id=subject_id AND r.role='ATHLETE'
   AND r.status='ACTIVE' AND r.revoked_at IS NULL
 )
 OR EXISTS (
  SELECT 1 FROM spps_identity_dev.guardian_authorities g
  JOIN spps_identity_dev.platform_role_grants r
    ON r.account_id=g.guardian_account_id
  WHERE g.guardian_account_id=spps_identity_dev.current_account_id()
   AND g.dependent_person_id=subject_id
   AND r.role='GUARDIAN' AND r.status='ACTIVE' AND r.revoked_at IS NULL
   AND g.status='VERIFIED' AND g.verified_at IS NOT NULL
   AND g.revoked_at IS NULL
   AND (g.expires_at IS NULL OR g.expires_at>CURRENT_TIMESTAMP)
   AND g.scope IN ('assigned_tasks','appointment_metadata',
                   'own_submissions','released_assessment_summary')
 ),false)
 $body$;
ALTER FUNCTION spps_identity_dev.portal_can_view_person(integer) OWNER TO spps_identity_owner;
REVOKE ALL ON FUNCTION spps_identity_dev.portal_can_view_person(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.portal_can_view_person(integer) TO spps_identity_portal;

-- Metadata gate only. Practitioner/case/tenant references remain untrusted
-- until a later reviewed cross-registry validation migration and API review.
CREATE FUNCTION spps_identity_dev.portal_can_view_link(
 subject_id integer, owner_practitioner_id integer, consent_id integer,
 link_status varchar, approved_at timestamptz, activated_at timestamptz,
 revoked_at timestamptz, expires_at timestamptz
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT COALESCE(
 spps_identity_dev.portal_can_view_person(subject_id)
 AND link_status='ACTIVE' AND approved_at IS NOT NULL
 AND activated_at IS NOT NULL AND revoked_at IS NULL
 AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP)
 AND EXISTS(
  SELECT 1 FROM spps_identity_dev.platform_consent_events consent
  WHERE consent.id=consent_id AND consent.person_id=subject_id
   AND consent.practitioner_id=owner_practitioner_id
   AND consent.purpose='CASE_LINK' AND consent.decision='GRANTED'
   AND NOT EXISTS(
    SELECT 1 FROM spps_identity_dev.platform_consent_events withdrawn
    WHERE withdrawn.person_id=subject_id
     AND withdrawn.practitioner_id=owner_practitioner_id
     AND withdrawn.purpose='CASE_LINK' AND withdrawn.decision='WITHDRAWN'
     AND withdrawn.id>consent.id
   )
 ),false)
 $body$;
ALTER FUNCTION spps_identity_dev.portal_can_view_link(integer,integer,integer,varchar,timestamptz,timestamptz,timestamptz,timestamptz) OWNER TO spps_identity_owner;
REVOKE ALL ON FUNCTION spps_identity_dev.portal_can_view_link(integer,integer,integer,varchar,timestamptz,timestamptz,timestamptz,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.portal_can_view_link(integer,integer,integer,varchar,timestamptz,timestamptz,timestamptz,timestamptz) TO spps_identity_portal;

GRANT SELECT (id,created_at) ON spps_identity_dev.platform_people TO spps_identity_portal;
GRANT SELECT (id,person_id,email_normalized,status,verified_at,created_at)
 ON spps_identity_dev.platform_accounts TO spps_identity_portal;
GRANT SELECT (id,account_id,role,status,created_at,revoked_at)
 ON spps_identity_dev.platform_role_grants TO spps_identity_portal;
GRANT SELECT (id,guardian_account_id,dependent_person_id,status,scope,
 verified_at,expires_at,revoked_at)
 ON spps_identity_dev.guardian_authorities TO spps_identity_portal;
GRANT SELECT (id,person_id,practitioner_id,athlete_record_id,status,
 approved_at,activated_at,revoked_at,expires_at)
 ON spps_identity_dev.practitioner_athlete_links TO spps_identity_portal;

CREATE POLICY portal_people_read ON spps_identity_dev.platform_people
 FOR SELECT TO spps_identity_portal
 USING(spps_identity_dev.portal_can_view_person(id));
CREATE POLICY portal_accounts_read ON spps_identity_dev.platform_accounts
 FOR SELECT TO spps_identity_portal
 USING(id=spps_identity_dev.current_account_id());
CREATE POLICY portal_role_grants_read ON spps_identity_dev.platform_role_grants
 FOR SELECT TO spps_identity_portal
 USING(account_id=spps_identity_dev.current_account_id());
CREATE POLICY portal_guardian_read ON spps_identity_dev.guardian_authorities
 FOR SELECT TO spps_identity_portal
 USING(guardian_account_id=spps_identity_dev.current_account_id());
CREATE POLICY portal_link_read ON spps_identity_dev.practitioner_athlete_links
 FOR SELECT TO spps_identity_portal
 USING(spps_identity_dev.portal_can_view_link(
  person_id,practitioner_id,consent_event_id,status,
  approved_at,activated_at,revoked_at,expires_at));

-- Auth login is server-only and READ-ONLY. No registration/session mutation yet.
GRANT SELECT ON spps_identity_dev.platform_accounts,
 spps_identity_dev.platform_account_sessions,
 spps_identity_dev.platform_role_grants TO spps_identity_auth;
CREATE POLICY auth_account_lookup ON spps_identity_dev.platform_accounts
 FOR SELECT TO spps_identity_auth USING(true);
CREATE POLICY auth_session_lookup ON spps_identity_dev.platform_account_sessions
 FOR SELECT TO spps_identity_auth USING(true);
CREATE POLICY auth_roles_lookup ON spps_identity_dev.platform_role_grants
 FOR SELECT TO spps_identity_auth USING(true);
-- No INSERT, UPDATE or DELETE grants / policies for either LOGIN role.
