-- SPPS production database, step 09 of the serial set: 0006_guardian_authority_consent
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0006_guardian_authority_consent.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
-- Stage 13A.6: guardian authority, independent reviewer verification, consent ledger.
-- Identity database only; executed by tools.identity_review_db after 0005.
-- All functions: SECURITY DEFINER, owner spps_identity_owner, fixed search_path,
-- EXECUTE granted only to spps_identity_auth (the API). Every function derives the
-- actor from the session token hash; client-supplied account IDs are never trusted.

-- Audit trail is append-only like the consent ledger.
DROP POLICY identity_owner_update ON spps_identity_dev.platform_audit_events;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE
 ON spps_identity_dev.platform_audit_events FOR EACH ROW
 EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE
 ON spps_identity_dev.platform_audit_events FOR EACH STATEMENT
 EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();

-- At most one open (pending or verified, unrevoked) authority per guardian+dependant.
CREATE UNIQUE INDEX uq_guardian_authority_open ON spps_identity_dev.guardian_authorities
 (guardian_account_id, dependent_person_id)
 WHERE status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION','VERIFIED') AND revoked_at IS NULL;

-- Portal projection: a guardian may also see the dependant records they created.
GRANT SELECT (display_name, created_by_account_id) ON spps_identity_dev.platform_people TO spps_identity_portal;
DROP POLICY portal_people_read ON spps_identity_dev.platform_people;
CREATE POLICY portal_people_read ON spps_identity_dev.platform_people
 FOR SELECT TO spps_identity_portal
 USING (spps_identity_dev.portal_can_view_person(id)
        OR created_by_account_id = spps_identity_dev.current_account_id());

-- Multi-scope authority: scope is a sorted comma-separated set.
CREATE OR REPLACE FUNCTION spps_identity_dev.portal_can_view_person(subject_id integer)
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
  JOIN spps_identity_dev.platform_role_grants r ON r.account_id=g.guardian_account_id
  WHERE g.guardian_account_id=spps_identity_dev.current_account_id()
   AND g.dependent_person_id=subject_id
   AND r.role='GUARDIAN' AND r.status='ACTIVE' AND r.revoked_at IS NULL
   AND g.status='VERIFIED' AND g.verified_at IS NOT NULL AND g.revoked_at IS NULL
   AND (g.expires_at IS NULL OR g.expires_at>CURRENT_TIMESTAMP)
   AND string_to_array(g.scope, ',') && ARRAY['assigned_tasks','appointment_metadata',
        'own_submissions','released_assessment_summary','booking_management',
        'consent_management','proxy_submission']
 ),false)
$body$;

-- ---------------------------------------------------------------- helpers
CREATE FUNCTION spps_identity_dev.session_account(p_token_hash text)
 RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT s.account_id FROM platform_account_sessions s
 JOIN platform_accounts a ON a.id=s.account_id
 WHERE s.token_hash=p_token_hash AND s.revoked_at IS NULL AND s.expires_at>now()
   AND a.status='VERIFIED' AND a.verified_at IS NOT NULL
 LIMIT 1
$body$;

CREATE FUNCTION spps_identity_dev.account_has_role(p_account integer, p_role text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT EXISTS (SELECT 1 FROM platform_role_grants r WHERE r.account_id=p_account
                AND r.role=p_role AND r.status='ACTIVE' AND r.revoked_at IS NULL)
$body$;

CREATE FUNCTION spps_identity_dev.require_actor(p_token_hash text, p_role text)
 RETURNS integer LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v integer := session_account(p_token_hash);
BEGIN
 IF v IS NULL THEN RAISE EXCEPTION 'session required' USING ERRCODE='28000'; END IF;
 IF p_role IS NOT NULL AND NOT account_has_role(v, p_role) THEN
  RAISE EXCEPTION 'role required' USING ERRCODE='42501';
 END IF;
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.normalize_scopes(p_scopes text)
 RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $body$
DECLARE v text[];
BEGIN
 SELECT array_agg(DISTINCT btrim(x) ORDER BY btrim(x)) INTO v
  FROM unnest(string_to_array(coalesce(p_scopes,''), ',')) x WHERE btrim(x)<>'';
 IF v IS NULL OR NOT v <@ ARRAY['appointment_metadata','assigned_tasks','booking_management',
    'consent_management','own_submissions','proxy_submission','released_assessment_summary'] THEN
  RAISE EXCEPTION 'invalid guardian scopes' USING ERRCODE='22023';
 END IF;
 RETURN array_to_string(v, ',');
END $body$;

-- Verified, unexpired, unrevoked authority held by p_account over p_person, optionally with a scope.
CREATE FUNCTION spps_identity_dev.active_authority(p_account integer, p_person integer, p_scope text)
 RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT g.id FROM guardian_authorities g
 WHERE g.guardian_account_id=p_account AND g.dependent_person_id=p_person
   AND g.status='VERIFIED' AND g.verified_at IS NOT NULL AND g.revoked_at IS NULL
   AND (g.expires_at IS NULL OR g.expires_at>now())
   AND (p_scope IS NULL OR p_scope = ANY (string_to_array(g.scope, ',')))
   AND account_has_role(p_account, 'GUARDIAN')
 ORDER BY g.id DESC LIMIT 1
$body$;

CREATE FUNCTION spps_identity_dev.audit(p_actor integer, p_action text, p_type text, p_id integer, p_detail text)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 INSERT INTO platform_audit_events(actor_account_id,action,object_type,object_id,detail,recorded_at)
 VALUES (p_actor,p_action,p_type,p_id,left(coalesce(p_detail,''),200),now())
$body$;

-- ---------------------------------------------------------------- guardian
CREATE FUNCTION spps_identity_dev.guardian_add_dependant(p_token_hash text, p_display_name text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'GUARDIAN'); v_person integer;
BEGIN
 IF length(btrim(coalesce(p_display_name,''))) NOT BETWEEN 2 AND 120 THEN
  RAISE EXCEPTION 'invalid dependant name' USING ERRCODE='22023';
 END IF;
 IF (SELECT count(*) FROM platform_people WHERE created_by_account_id=v_actor) >= 20 THEN
  RAISE EXCEPTION 'dependant limit reached' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_people(created_at,display_name,created_by_account_id)
 VALUES (now(),btrim(p_display_name),v_actor) RETURNING id INTO v_person;
 PERFORM audit(v_actor,'dependant.created','person',v_person,'');
 RETURN v_person;
END $body$;

CREATE FUNCTION spps_identity_dev.guardian_list_dependants(p_token_hash text)
 RETURNS TABLE(person_id integer, display_name text, created_at timestamptz,
               authority_id integer, authority_status text, scopes text, expires_at timestamptz)
 LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,'GUARDIAN');
BEGIN
 RETURN QUERY
 SELECT p.id, p.display_name::text, p.created_at, g.id, g.status::text, g.scope::text, g.expires_at
 FROM platform_people p
 LEFT JOIN LATERAL (
  SELECT * FROM guardian_authorities ga WHERE ga.dependent_person_id=p.id
   AND ga.guardian_account_id=v_actor ORDER BY ga.id DESC LIMIT 1) g ON true
 WHERE p.created_by_account_id=v_actor ORDER BY p.id;
END $body$;

CREATE FUNCTION spps_identity_dev.guardian_request_authority(
 p_token_hash text, p_person_id integer, p_scopes text, p_evidence_reference text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'GUARDIAN'); v_id integer;
        v_scopes text := normalize_scopes(p_scopes);
        v_evidence text := nullif(btrim(coalesce(p_evidence_reference,'')),'');
BEGIN
 -- Only for a dependant this guardian created. Names, emails or booking contacts
 -- never confer a relationship (matrix case 5); unknown and foreign IDs look identical.
 PERFORM 1 FROM platform_people WHERE id=p_person_id AND created_by_account_id=v_actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'dependant not found' USING ERRCODE='P0002'; END IF;
 IF length(coalesce(v_evidence,'')) > 160 THEN
  RAISE EXCEPTION 'evidence reference too long' USING ERRCODE='22023';
 END IF;
 IF EXISTS (SELECT 1 FROM guardian_authorities WHERE guardian_account_id=v_actor
            AND dependent_person_id=p_person_id AND revoked_at IS NULL
            AND (status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION')
                 OR (status='VERIFIED' AND (expires_at IS NULL OR expires_at>now())))) THEN
  RAISE EXCEPTION 'an authority request is already open' USING ERRCODE='23505';
 END IF;
 -- Expired verified rows are closed so the partial unique index admits a renewal.
 UPDATE guardian_authorities SET status='EXPIRED'
  WHERE guardian_account_id=v_actor AND dependent_person_id=p_person_id AND status='VERIFIED'
    AND expires_at IS NOT NULL AND expires_at<=now();
 INSERT INTO guardian_authorities(guardian_account_id,dependent_person_id,status,scope,
                                  evidence_reference,requested_at)
 VALUES (v_actor,p_person_id,
         CASE WHEN v_evidence IS NULL THEN 'PENDING_EVIDENCE' ELSE 'PENDING_VERIFICATION' END,
         v_scopes,v_evidence,now())
 RETURNING id INTO v_id;
 PERFORM audit(v_actor,'guardian_authority.requested','guardian_authority',v_id,v_scopes);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.guardian_submit_evidence(p_token_hash text, p_authority_id integer, p_evidence_reference text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'GUARDIAN');
        v_evidence text := nullif(btrim(coalesce(p_evidence_reference,'')),''); n integer;
BEGIN
 IF v_evidence IS NULL OR length(v_evidence) > 160 THEN
  RAISE EXCEPTION 'invalid evidence reference' USING ERRCODE='22023';
 END IF;
 UPDATE guardian_authorities SET evidence_reference=v_evidence, status='PENDING_VERIFICATION'
  WHERE id=p_authority_id AND guardian_account_id=v_actor AND revoked_at IS NULL
    AND status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION');
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RETURN false; END IF;
 PERFORM audit(v_actor,'guardian_authority.evidence','guardian_authority',p_authority_id,'');
 RETURN true;
END $body$;

CREATE FUNCTION spps_identity_dev.guardian_revoke_authority(p_token_hash text, p_authority_id integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'GUARDIAN'); n integer;
BEGIN
 UPDATE guardian_authorities SET status='REVOKED', revoked_at=now()
  WHERE id=p_authority_id AND guardian_account_id=v_actor AND revoked_at IS NULL
    AND status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION','VERIFIED');
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RETURN false; END IF;
 PERFORM audit(v_actor,'guardian_authority.revoked_by_guardian','guardian_authority',p_authority_id,'');
 RETURN true;
END $body$;

-- ---------------------------------------------------------------- reviewer
CREATE FUNCTION spps_identity_dev.reviewer_pending_authorities(p_token_hash text)
 RETURNS TABLE(authority_id integer, guardian_display_name text, guardian_email text,
               dependant_display_name text, scopes text, evidence_reference text, requested_at timestamptz)
 LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,'REVIEWER');
BEGIN
 RETURN QUERY
 SELECT g.id, a.display_name::text, a.email_normalized::text, p.display_name::text,
        g.scope::text, g.evidence_reference::text, g.requested_at
 FROM guardian_authorities g
 JOIN platform_accounts a ON a.id=g.guardian_account_id
 JOIN platform_people p ON p.id=g.dependent_person_id
 WHERE g.status='PENDING_VERIFICATION' AND g.revoked_at IS NULL
   AND g.guardian_account_id<>v_actor
 ORDER BY g.requested_at NULLS FIRST, g.id;
END $body$;

CREATE FUNCTION spps_identity_dev.reviewer_decide_authority(
 p_token_hash text, p_authority_id integer, p_decision text, p_reason text, p_valid_days integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'REVIEWER'); n integer;
        v_reason text := btrim(coalesce(p_reason,''));
BEGIN
 IF p_decision NOT IN ('VERIFIED','REJECTED') OR length(v_reason) NOT BETWEEN 5 AND 240
    OR (p_decision='VERIFIED' AND (p_valid_days IS NULL OR p_valid_days NOT BETWEEN 1 AND 730)) THEN
  RAISE EXCEPTION 'invalid review decision' USING ERRCODE='22023';
 END IF;
 UPDATE guardian_authorities SET status=p_decision, review_reason=v_reason, reviewed_by_actor_id=v_actor,
   verified_at=CASE WHEN p_decision='VERIFIED' THEN now() ELSE NULL END,
   expires_at=CASE WHEN p_decision='VERIFIED' THEN now()+make_interval(days=>p_valid_days) ELSE NULL END
  WHERE id=p_authority_id AND status='PENDING_VERIFICATION' AND revoked_at IS NULL
    AND evidence_reference IS NOT NULL
    AND guardian_account_id<>v_actor;  -- separation of duties: never review yourself
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RETURN false; END IF;
 PERFORM audit(v_actor,'guardian_authority.'||lower(p_decision),'guardian_authority',p_authority_id,'');
 RETURN true;
END $body$;

CREATE FUNCTION spps_identity_dev.reviewer_revoke_authority(p_token_hash text, p_authority_id integer, p_reason text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,'REVIEWER'); n integer;
BEGIN
 IF length(btrim(coalesce(p_reason,''))) NOT BETWEEN 5 AND 240 THEN
  RAISE EXCEPTION 'reason required' USING ERRCODE='22023';
 END IF;
 UPDATE guardian_authorities SET status='REVOKED', revoked_at=now(), review_reason=btrim(p_reason),
   reviewed_by_actor_id=v_actor
  WHERE id=p_authority_id AND revoked_at IS NULL AND status IN ('PENDING_EVIDENCE','PENDING_VERIFICATION','VERIFIED')
    AND guardian_account_id<>v_actor;
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RETURN false; END IF;
 PERFORM audit(v_actor,'guardian_authority.revoked_by_reviewer','guardian_authority',p_authority_id,'');
 RETURN true;
END $body$;

-- ---------------------------------------------------------------- consent
-- Practitioner-scoped purposes need practitioner_id; platform purposes must not have one.
CREATE FUNCTION spps_identity_dev.consent_subject_authority(p_actor integer, p_subject integer)
 RETURNS TABLE(allowed boolean, authority_id integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT true, NULL::integer FROM platform_accounts a
  WHERE a.id=p_actor AND a.person_id=p_subject
    AND (account_has_role(p_actor,'ATHLETE') OR account_has_role(p_actor,'GUARDIAN'))
 UNION ALL
 SELECT true, active_authority(p_actor, p_subject, 'consent_management')
  WHERE active_authority(p_actor, p_subject, 'consent_management') IS NOT NULL
 LIMIT 1
$body$;

CREATE FUNCTION spps_identity_dev.consent_record(
 p_token_hash text, p_subject_person_id integer, p_practitioner_id integer,
 p_purpose text, p_policy_version text, p_decision text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_allowed boolean; v_authority integer;
        v_previous platform_consent_events%ROWTYPE; v_id integer;
BEGIN
 IF p_decision NOT IN ('GRANTED','WITHDRAWN')
    OR p_policy_version !~ '^[a-z0-9][a-z0-9._-]{0,39}$'
    OR p_purpose NOT IN ('PLATFORM_TERMS','PRIVACY_NOTICE','CASE_LINK','PROGRAMME_PARTICIPATION',
                         'APPOINTMENT_BOOKING','VIDEO_CONSULTATION','DATA_SHARING_SUMMARY')
    OR (p_purpose IN ('PLATFORM_TERMS','PRIVACY_NOTICE') AND p_practitioner_id IS NOT NULL)
    OR (p_purpose NOT IN ('PLATFORM_TERMS','PRIVACY_NOTICE')
        AND (p_practitioner_id IS NULL OR p_practitioner_id<=0)) THEN
  RAISE EXCEPTION 'invalid consent parameters' USING ERRCODE='22023';
 END IF;
 SELECT c.allowed, c.authority_id INTO v_allowed, v_authority
  FROM consent_subject_authority(v_actor, p_subject_person_id) c;
 IF v_allowed IS NOT TRUE THEN
  RAISE EXCEPTION 'not authorised for this person' USING ERRCODE='P0002';
 END IF;
 -- Serialise per subject so the "current state" check cannot race.
 PERFORM 1 FROM platform_people WHERE id=p_subject_person_id FOR UPDATE;
 SELECT * INTO v_previous FROM platform_consent_events e
  WHERE e.person_id=p_subject_person_id AND e.purpose=p_purpose
    AND e.practitioner_id IS NOT DISTINCT FROM p_practitioner_id
  ORDER BY e.id DESC LIMIT 1;
 IF p_decision='WITHDRAWN' AND (v_previous.id IS NULL OR v_previous.decision<>'GRANTED') THEN
  RAISE EXCEPTION 'nothing to withdraw' USING ERRCODE='22023';
 END IF;
 IF p_decision='GRANTED' AND v_previous.decision='GRANTED'
    AND v_previous.policy_version=p_policy_version THEN
  RETURN v_previous.id;  -- idempotent repeat
 END IF;
 INSERT INTO platform_consent_events(person_id,actor_account_id,guardian_authority_id,practitioner_id,
   purpose,policy_version,decision,supersedes_event_id,recorded_at)
 VALUES (p_subject_person_id,v_actor,v_authority,p_practitioner_id,p_purpose,p_policy_version,
         p_decision,v_previous.id,now())
 RETURNING id INTO v_id;
 PERFORM audit(v_actor,'consent.'||lower(p_decision),'consent_event',v_id,p_purpose);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.consent_history(p_token_hash text, p_subject_person_id integer)
 RETURNS TABLE(event_id integer, purpose text, practitioner_id integer, policy_version text,
               decision text, by_guardian boolean, recorded_at timestamptz, is_current boolean)
 LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 IF NOT EXISTS (SELECT 1 FROM platform_accounts a WHERE a.id=v_actor AND a.person_id=p_subject_person_id)
    AND active_authority(v_actor, p_subject_person_id, NULL) IS NULL THEN
  RAISE EXCEPTION 'not authorised for this person' USING ERRCODE='P0002';
 END IF;
 RETURN QUERY
 SELECT e.id, e.purpose::text, e.practitioner_id, e.policy_version::text, e.decision::text,
        e.guardian_authority_id IS NOT NULL, e.recorded_at,
        e.id = (SELECT max(x.id) FROM platform_consent_events x WHERE x.person_id=e.person_id
                AND x.purpose=e.purpose AND x.practitioner_id IS NOT DISTINCT FROM e.practitioner_id)
 FROM platform_consent_events e WHERE e.person_id=p_subject_person_id ORDER BY e.id;
END $body$;

-- Current consent check used by later stages (link activation, bookings, programmes).
CREATE FUNCTION spps_identity_dev.consent_is_current(p_subject integer, p_practitioner integer, p_purpose text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT COALESCE((SELECT e.decision='GRANTED' FROM platform_consent_events e
   WHERE e.person_id=p_subject AND e.purpose=p_purpose
     AND e.practitioner_id IS NOT DISTINCT FROM p_practitioner ORDER BY e.id DESC LIMIT 1), false)
$body$;

-- ---------------------------------------------------------------- ownership + grants
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN (
           'session_account','account_has_role','require_actor','normalize_scopes','active_authority','audit',
           'guardian_add_dependant','guardian_list_dependants','guardian_request_authority',
           'guardian_submit_evidence','guardian_revoke_authority','reviewer_pending_authorities',
           'reviewer_decide_authority','reviewer_revoke_authority','consent_subject_authority',
           'consent_record','consent_history','consent_is_current') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  IF f.proname IN ('guardian_add_dependant','guardian_list_dependants','guardian_request_authority',
     'guardian_submit_evidence','guardian_revoke_authority','reviewer_pending_authorities',
     'reviewer_decide_authority','reviewer_revoke_authority','consent_record','consent_history') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0006_guardian_authority_consent.sql','ab451fcf1e48a293a1a112eb0ba1fcaefb20df26aff1d258849ce31f49273488') ON CONFLICT (name) DO NOTHING;
COMMIT;
