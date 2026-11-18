-- SPPS production database, step 10 of the serial set: 0007_practitioner_links
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0007_practitioner_links.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
-- Stage 13A.7: practitioner–athlete linking (identity database only).
-- Practitioner-side functions take practitioner_id/tenant_id/athlete_record_id from
-- the API, which has ALREADY verified ownership in the clinical database under its
-- own FORCE RLS (server/main.py athlete_for). Portal-side functions derive the actor
-- from the session token hash. EXECUTE only for spps_identity_auth.

-- One pending/active link per practitioner-owned athlete record and per person+practitioner.
CREATE UNIQUE INDEX uq_link_open_record ON spps_identity_dev.practitioner_athlete_links
 (practitioner_id, athlete_record_id)
 WHERE status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE');
CREATE UNIQUE INDEX uq_link_open_person ON spps_identity_dev.practitioner_athlete_links
 (person_id, practitioner_id)
 WHERE status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE');
-- Replace the 13A.4 unconditional unique so a person can re-link after revocation.
ALTER TABLE spps_identity_dev.practitioner_athlete_links DROP CONSTRAINT IF EXISTS uq_platform_person_practitioner_case;

-- Live link check reused by 13B/13C projections: ACTIVE, unexpired, consent current,
-- and (for a guardian-held link) the guardian authority still verified.
CREATE FUNCTION spps_identity_dev.link_is_active(p_link_id integer)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT COALESCE((
  SELECT l.status='ACTIVE' AND l.revoked_at IS NULL AND l.approved_at IS NOT NULL
     AND (l.expires_at IS NULL OR l.expires_at>now())
     AND consent_is_current(l.person_id, l.practitioner_id, 'CASE_LINK')
  FROM practitioner_athlete_links l WHERE l.id=p_link_id), false)
$body$;

-- Can this account act for this person (self as athlete, or verified guardian with scope)?
CREATE FUNCTION spps_identity_dev.acting_authority(p_account integer, p_person integer, p_scope text)
 RETURNS TABLE(allowed boolean, authority_id integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT true, NULL::integer FROM platform_accounts a
  WHERE a.id=p_account AND a.person_id=p_person AND account_has_role(p_account,'ATHLETE')
 UNION ALL
 SELECT true, active_authority(p_account, p_person, p_scope)
  WHERE active_authority(p_account, p_person, p_scope) IS NOT NULL
 LIMIT 1
$body$;

-- ---------------------------------------------------------------- practitioner side
CREATE FUNCTION spps_identity_dev.practitioner_create_invitation(
 p_practitioner_id integer, p_tenant_id integer, p_athlete_record_id integer,
 p_code_hash text, p_ttl_seconds integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_id integer;
BEGIN
 IF p_practitioner_id<=0 OR p_tenant_id<=0 OR p_athlete_record_id<=0 OR NOT is_sha256_hex(p_code_hash)
    OR p_ttl_seconds NOT BETWEEN 600 AND 1209600 THEN
  RAISE EXCEPTION 'invalid invitation' USING ERRCODE='22023';
 END IF;
 IF EXISTS (SELECT 1 FROM practitioner_athlete_links WHERE practitioner_id=p_practitioner_id
            AND athlete_record_id=p_athlete_record_id AND status IN ('PRACTITIONER_REVIEW','ACTIVE')) THEN
  RAISE EXCEPTION 'athlete record already linked' USING ERRCODE='23505';
 END IF;
 -- Only one outstanding invitation per athlete record.
 UPDATE platform_link_invitations SET revoked_at=now()
  WHERE practitioner_id=p_practitioner_id AND athlete_record_id=p_athlete_record_id
    AND consumed_at IS NULL AND revoked_at IS NULL;
 INSERT INTO platform_link_invitations(practitioner_id,tenant_id,athlete_record_id,code_hash,created_at,expires_at)
 VALUES (p_practitioner_id,p_tenant_id,p_athlete_record_id,p_code_hash,now(),now()+make_interval(secs=>p_ttl_seconds))
 RETURNING id INTO v_id;
 PERFORM audit(NULL,'link_invitation.created','link_invitation',v_id,'practitioner '||p_practitioner_id);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_list_links(p_practitioner_id integer, p_athlete_record_id integer)
 RETURNS TABLE(link_id integer, athlete_record_id integer, status text, requester_display_name text,
               via_guardian boolean, subject_display_name text, consent_current boolean,
               requested_at timestamptz, activated_at timestamptz, revoked_at timestamptz,
               invitation_pending boolean, invitation_expires_at timestamptz)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 RETURN QUERY
 SELECT l.id, l.athlete_record_id, l.status::text, a.display_name::text, l.guardian_authority_id IS NOT NULL,
        COALESCE(NULLIF(p.display_name,''), sa.display_name)::text,
        consent_is_current(l.person_id, l.practitioner_id, 'CASE_LINK'),
        l.requested_at, l.activated_at, l.revoked_at, false, NULL::timestamptz
 FROM practitioner_athlete_links l
 LEFT JOIN platform_accounts a ON a.id=l.requested_by_account_id
 LEFT JOIN platform_people p ON p.id=l.person_id
 LEFT JOIN platform_accounts sa ON sa.person_id=l.person_id
 WHERE l.practitioner_id=p_practitioner_id
   AND (p_athlete_record_id IS NULL OR l.athlete_record_id=p_athlete_record_id)
 UNION ALL
 SELECT NULL, i.athlete_record_id, 'INVITED', NULL, false, NULL, false, i.created_at, NULL, NULL, true, i.expires_at
 FROM platform_link_invitations i
 WHERE i.practitioner_id=p_practitioner_id AND i.consumed_at IS NULL AND i.revoked_at IS NULL
   AND i.expires_at>now() AND (p_athlete_record_id IS NULL OR i.athlete_record_id=p_athlete_record_id)
 ORDER BY 8 DESC NULLS LAST;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_decide_link(
 p_practitioner_id integer, p_link_id integer, p_decision text, p_reason text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_link practitioner_athlete_links%ROWTYPE;
BEGIN
 IF p_decision NOT IN ('APPROVE','DECLINE','REVOKE') THEN
  RAISE EXCEPTION 'invalid decision' USING ERRCODE='22023';
 END IF;
 SELECT * INTO v_link FROM practitioner_athlete_links
  WHERE id=p_link_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'link not found' USING ERRCODE='P0002'; END IF;
 IF p_decision='APPROVE' THEN
  IF v_link.status<>'PRACTITIONER_REVIEW' THEN RETURN v_link.status; END IF;
  IF NOT consent_is_current(v_link.person_id, v_link.practitioner_id, 'CASE_LINK') THEN
   UPDATE practitioner_athlete_links SET status='CONSENT_PENDING' WHERE id=p_link_id;
   RETURN 'CONSENT_PENDING';
  END IF;
  IF v_link.guardian_authority_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM guardian_authorities g WHERE g.id=v_link.guardian_authority_id AND g.status='VERIFIED'
      AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>now())) THEN
   RAISE EXCEPTION 'guardian authority no longer valid' USING ERRCODE='42501';
  END IF;
  UPDATE practitioner_athlete_links SET status='ACTIVE', approved_at=now(), activated_at=now(),
    consent_event_id=(SELECT max(id) FROM platform_consent_events e WHERE e.person_id=v_link.person_id
                      AND e.practitioner_id=v_link.practitioner_id AND e.purpose='CASE_LINK' AND e.decision='GRANTED')
   WHERE id=p_link_id;
  PERFORM audit(NULL,'link.approved','link',p_link_id,'practitioner '||p_practitioner_id);
  RETURN 'ACTIVE';
 ELSIF p_decision='DECLINE' THEN
  IF v_link.status NOT IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW') THEN RETURN v_link.status; END IF;
  UPDATE practitioner_athlete_links SET status='DECLINED', decided_reason=left(btrim(coalesce(p_reason,'')),240)
   WHERE id=p_link_id;
  PERFORM audit(NULL,'link.declined','link',p_link_id,'practitioner '||p_practitioner_id);
  RETURN 'DECLINED';
 ELSE
  IF v_link.status NOT IN ('ACTIVE','PRACTITIONER_REVIEW','CONSENT_PENDING') THEN RETURN v_link.status; END IF;
  UPDATE practitioner_athlete_links SET status='REVOKED', revoked_at=now(),
    decided_reason=left(btrim(coalesce(p_reason,'')),240) WHERE id=p_link_id;
  PERFORM audit(NULL,'link.revoked_by_practitioner','link',p_link_id,'practitioner '||p_practitioner_id);
  RETURN 'REVOKED';
 END IF;
END $body$;

-- Practitioner-side check used by later stages: active link for this record?
CREATE FUNCTION spps_identity_dev.practitioner_active_link(p_practitioner_id integer, p_athlete_record_id integer)
 RETURNS TABLE(link_id integer, person_id integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT l.id, l.person_id FROM practitioner_athlete_links l
 WHERE l.practitioner_id=p_practitioner_id AND l.athlete_record_id=p_athlete_record_id
   AND link_is_active(l.id) LIMIT 1
$body$;

-- ---------------------------------------------------------------- portal side
CREATE FUNCTION spps_identity_dev.portal_redeem_invitation(p_token_hash text, p_code_hash text, p_subject_person_id integer)
 RETURNS TABLE(link_id integer, practitioner_id integer, status text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_inv platform_link_invitations%ROWTYPE;
        v_allowed boolean; v_authority integer; v_link integer;
BEGIN
 SELECT c.allowed, c.authority_id INTO v_allowed, v_authority
  FROM acting_authority(v_actor, p_subject_person_id, 'consent_management') c;
 IF v_allowed IS NOT TRUE THEN RAISE EXCEPTION 'not authorised for this person' USING ERRCODE='P0002'; END IF;
 IF NOT is_sha256_hex(p_code_hash) THEN RAISE EXCEPTION 'invalid code' USING ERRCODE='P0001'; END IF;
 SELECT * INTO v_inv FROM platform_link_invitations
  WHERE code_hash=p_code_hash AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'invalid code' USING ERRCODE='P0001'; END IF;
 IF NOT consent_is_current(p_subject_person_id, v_inv.practitioner_id, 'CASE_LINK') THEN
  RAISE EXCEPTION 'case-link consent required first' USING ERRCODE='P0003';
 END IF;
 BEGIN
  INSERT INTO practitioner_athlete_links(person_id,practitioner_id,athlete_record_id,tenant_id,status,
    requested_by_account_id,guardian_authority_id,requested_at)
  VALUES (p_subject_person_id,v_inv.practitioner_id,v_inv.athlete_record_id,v_inv.tenant_id,
          'PRACTITIONER_REVIEW',v_actor,v_authority,now())
  RETURNING id INTO v_link;
 EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'already linked or pending' USING ERRCODE='23505';
 END;
 UPDATE platform_link_invitations SET consumed_at=now(), link_id=v_link WHERE id=v_inv.id;
 PERFORM audit(v_actor,'link.requested','link',v_link,'');
 RETURN QUERY SELECT v_link, v_inv.practitioner_id, 'PRACTITIONER_REVIEW'::text;
END $body$;

CREATE FUNCTION spps_identity_dev.portal_list_links(p_token_hash text)
 RETURNS TABLE(link_id integer, person_id integer, subject_display_name text, practitioner_id integer,
               status text, active boolean, via_guardian boolean, requested_at timestamptz, activated_at timestamptz)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 RETURN QUERY
 SELECT l.id, l.person_id, COALESCE(NULLIF(p.display_name,''), me.display_name)::text, l.practitioner_id,
        l.status::text, link_is_active(l.id), l.guardian_authority_id IS NOT NULL, l.requested_at, l.activated_at
 FROM practitioner_athlete_links l
 JOIN platform_people p ON p.id=l.person_id
 LEFT JOIN platform_accounts me ON me.person_id=l.person_id
 WHERE (me.id=v_actor AND account_has_role(v_actor,'ATHLETE'))
    OR EXISTS (SELECT 1 FROM acting_authority(v_actor, l.person_id, NULL) x WHERE x.authority_id IS NOT NULL)
 ORDER BY l.id;
END $body$;

CREATE FUNCTION spps_identity_dev.portal_revoke_link(p_token_hash text, p_link_id integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_link practitioner_athlete_links%ROWTYPE;
        v_allowed boolean;
BEGIN
 SELECT * INTO v_link FROM practitioner_athlete_links WHERE id=p_link_id FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT c.allowed INTO v_allowed FROM acting_authority(v_actor, v_link.person_id, 'consent_management') c;
 IF v_allowed IS NOT TRUE OR v_link.status NOT IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE') THEN
  RETURN false;
 END IF;
 UPDATE practitioner_athlete_links SET status='REVOKED', revoked_at=now() WHERE id=p_link_id;
 PERFORM audit(v_actor,'link.revoked_by_subject','link',p_link_id,'');
 RETURN true;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN (
           'link_is_active','acting_authority','practitioner_create_invitation','practitioner_list_links',
           'practitioner_decide_link','practitioner_active_link','portal_redeem_invitation',
           'portal_list_links','portal_revoke_link') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  IF f.proname NOT IN ('link_is_active','acting_authority') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0007_practitioner_links.sql','b5978df2208777ebe63718eb4177ef9587ba3cc9c3cd558cb85e13652b7d6344') ON CONFLICT (name) DO NOTHING;
COMMIT;
