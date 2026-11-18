-- Stage 13A.5a: athlete/guardian authentication data layer (identity database only).
-- Executed ONLY by tools.identity_review_db after 0004, inside one transaction.
-- Security model:
--   * spps_identity_auth loses every table privilege; it may only EXECUTE the
--     auth_* functions below. It can never enumerate accounts or bulk-read hashes.
--   * Functions are SECURITY DEFINER, owned by NOLOGIN spps_identity_owner, with a
--     fixed search_path. The owner gets INSERT/UPDATE policies; there are NO
--     DELETE policies anywhere, so no function can delete identity rows.
--   * platform_consent_events is append-only (trigger + no UPDATE policy).
--   * Tokens arrive as SHA-256 hex digests computed by the API; plaintext
--     session/verification tokens are never stored.

-- 1. Remove 13A.4 broad read access for the auth role.
DROP POLICY auth_account_lookup ON spps_identity_dev.platform_accounts;
DROP POLICY auth_session_lookup ON spps_identity_dev.platform_account_sessions;
DROP POLICY auth_roles_lookup ON spps_identity_dev.platform_role_grants;
REVOKE ALL ON ALL TABLES IN SCHEMA spps_identity_dev FROM spps_identity_auth;

-- 2. Owner write policies (reachable only through SECURITY DEFINER functions).
DO $$ DECLARE t record; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='spps_identity_dev' LOOP
  EXECUTE format('CREATE POLICY identity_owner_insert ON spps_identity_dev.%I FOR INSERT TO spps_identity_owner WITH CHECK (true)',t.tablename);
  IF t.tablename <> 'platform_consent_events' THEN
   EXECUTE format('CREATE POLICY identity_owner_update ON spps_identity_dev.%I FOR UPDATE TO spps_identity_owner USING (true) WITH CHECK (true)',t.tablename);
  END IF;
 END LOOP;
END $$;

-- 3. Append-only consent ledger.
CREATE FUNCTION spps_identity_dev.consent_events_append_only() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 RAISE EXCEPTION 'platform_consent_events is append-only' USING ERRCODE='42501';
END $body$;
ALTER FUNCTION spps_identity_dev.consent_events_append_only() OWNER TO spps_identity_owner;
REVOKE ALL ON FUNCTION spps_identity_dev.consent_events_append_only() FROM PUBLIC;
CREATE TRIGGER consent_events_no_update BEFORE UPDATE OR DELETE
 ON spps_identity_dev.platform_consent_events FOR EACH ROW
 EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();
CREATE TRIGGER consent_events_no_truncate BEFORE TRUNCATE
 ON spps_identity_dev.platform_consent_events FOR EACH STATEMENT
 EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();

-- 4. Portal may also read its own display name.
GRANT SELECT (display_name) ON spps_identity_dev.platform_accounts TO spps_identity_portal;

-- Helper: strict SHA-256 hex validation.
CREATE FUNCTION spps_identity_dev.is_sha256_hex(v text) RETURNS boolean
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
 SELECT v IS NOT NULL AND v ~ '^[0-9a-f]{64}$'
$body$;
ALTER FUNCTION spps_identity_dev.is_sha256_hex(text) OWNER TO spps_identity_owner;

-- 5. Distributed fixed-window throttle. Returns TRUE when the attempt is allowed.
CREATE FUNCTION spps_identity_dev.auth_throttle_hit(p_key text, p_window_seconds integer, p_limit integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_hits integer;
BEGIN
 IF NOT is_sha256_hex(p_key) OR p_window_seconds NOT BETWEEN 1 AND 86400 OR p_limit NOT BETWEEN 1 AND 10000 THEN
  RAISE EXCEPTION 'invalid throttle parameters' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_auth_throttle AS t(key_hash,window_start,hits) VALUES (p_key,now(),1)
 ON CONFLICT (key_hash) DO UPDATE SET
  hits = CASE WHEN t.window_start <= now()-make_interval(secs=>p_window_seconds) THEN 1 ELSE t.hits+1 END,
  window_start = CASE WHEN t.window_start <= now()-make_interval(secs=>p_window_seconds) THEN now() ELSE t.window_start END
 RETURNING hits INTO v_hits;
 RETURN v_hits <= p_limit;
END $body$;

-- 6. Registration. Returns new account id, or NULL when the e-mail is taken
--    (the API returns an identical response either way).
CREATE FUNCTION spps_identity_dev.auth_register(
 p_email text, p_password_hash text, p_display_name text, p_role text,
 p_adult_attested boolean, p_challenge_hash text, p_challenge_ttl_seconds integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_email text := lower(btrim(p_email)); v_person integer; v_account integer; v_constraint text;
BEGIN
 IF v_email !~ '^[^@\s]{1,64}@[^@\s]{1,185}\.[^@\s]{2,63}$' OR length(v_email) > 250
    OR p_password_hash !~ '^pbkdf2_sha256\$[0-9]{6,7}\$[0-9a-f]{32}\$[0-9a-f]{64}$'
    OR p_role NOT IN ('ATHLETE','GUARDIAN')
    OR length(btrim(coalesce(p_display_name,''))) NOT BETWEEN 2 AND 120
    OR NOT is_sha256_hex(p_challenge_hash)
    OR p_challenge_ttl_seconds NOT BETWEEN 60 AND 172800 THEN
  RAISE EXCEPTION 'invalid registration parameters' USING ERRCODE='22023';
 END IF;
 -- Self-registration is adults only (athletes and guardians). Minors are
 -- represented as dependants managed through verified guardian authority.
 IF p_adult_attested IS NOT TRUE THEN
  RAISE EXCEPTION 'self-registration requires adult attestation' USING ERRCODE='22023';
 END IF;
 IF EXISTS (SELECT 1 FROM platform_accounts WHERE email_normalized=v_email) THEN
  RETURN NULL;
 END IF;
 INSERT INTO platform_people(created_at) VALUES (now()) RETURNING id INTO v_person;
 BEGIN
  INSERT INTO platform_accounts(person_id,email_normalized,password_hash,display_name,status,
                                adult_attested_at,password_changed_at,created_at)
  VALUES (v_person,v_email,p_password_hash,btrim(p_display_name),'REGISTERED_UNVERIFIED',now(),now(),now())
  RETURNING id INTO v_account;
 EXCEPTION WHEN unique_violation THEN
  GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
  IF v_constraint <> 'platform_accounts_email_normalized_key' THEN RAISE; END IF;
  RETURN NULL;  -- concurrent duplicate e-mail
 END;
 INSERT INTO platform_role_grants(account_id,role,status,created_at) VALUES (v_account,p_role,'ACTIVE',now());
 INSERT INTO platform_verification_challenges(account_id,token_hash,purpose,expires_at,attempts)
 VALUES (v_account,p_challenge_hash,'EMAIL_VERIFY',now()+make_interval(secs=>p_challenge_ttl_seconds),0);
 RETURN v_account;
END $body$;

-- 7. Re-issue a verification challenge for an unverified account only.
CREATE FUNCTION spps_identity_dev.auth_resend_verification(p_email text, p_challenge_hash text, p_ttl integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_challenge_hash) OR p_ttl NOT BETWEEN 60 AND 172800 THEN
  RAISE EXCEPTION 'invalid parameters' USING ERRCODE='22023';
 END IF;
 SELECT id INTO v_account FROM platform_accounts
  WHERE email_normalized=lower(btrim(p_email)) AND status='REGISTERED_UNVERIFIED';
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE platform_verification_challenges SET consumed_at=now()
  WHERE account_id=v_account AND purpose='EMAIL_VERIFY' AND consumed_at IS NULL;
 INSERT INTO platform_verification_challenges(account_id,token_hash,purpose,expires_at,attempts)
 VALUES (v_account,p_challenge_hash,'EMAIL_VERIFY',now()+make_interval(secs=>p_ttl),0);
 RETURN true;
END $body$;

-- 8. Single-use, expiring e-mail verification.
CREATE FUNCTION spps_identity_dev.auth_verify_email(p_challenge_hash text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_challenge_hash) THEN RETURN NULL; END IF;
 UPDATE platform_verification_challenges SET consumed_at=now()
  WHERE token_hash=p_challenge_hash AND purpose='EMAIL_VERIFY'
    AND consumed_at IS NULL AND expires_at>now()
  RETURNING account_id INTO v_account;
 IF v_account IS NULL THEN RETURN NULL; END IF;
 UPDATE platform_accounts SET status='VERIFIED', verified_at=now()
  WHERE id=v_account AND status='REGISTERED_UNVERIFIED';
 RETURN v_account;
END $body$;

-- 9. Credential lookup for ONE exact e-mail (the API verifies PBKDF2 itself).
CREATE FUNCTION spps_identity_dev.auth_login_lookup(p_email text)
 RETURNS TABLE(account_id integer, password_hash text, account_status text)
 LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT a.id, a.password_hash::text, a.status::text FROM platform_accounts a
 WHERE a.email_normalized=lower(btrim(p_email)) LIMIT 1
$body$;

-- 10. Session creation bound to the credential that was just verified.
CREATE FUNCTION spps_identity_dev.auth_create_session(
 p_account_id integer, p_password_hash_seen text, p_token_hash text,
 p_csrf_hash text, p_ttl_seconds integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
BEGIN
 IF NOT is_sha256_hex(p_token_hash) OR NOT is_sha256_hex(p_csrf_hash)
    OR p_ttl_seconds NOT BETWEEN 300 AND 86400 THEN
  RAISE EXCEPTION 'invalid session parameters' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS (
  SELECT 1 FROM platform_accounts a
  WHERE a.id=p_account_id AND a.status='VERIFIED' AND a.verified_at IS NOT NULL
    AND a.password_hash=p_password_hash_seen
    AND EXISTS (SELECT 1 FROM platform_role_grants r WHERE r.account_id=a.id
                AND r.status='ACTIVE' AND r.revoked_at IS NULL)
 ) THEN
  RETURN false;
 END IF;
 INSERT INTO platform_account_sessions(account_id,token_hash,csrf_hash,expires_at,created_at,last_seen_at)
 VALUES (p_account_id,p_token_hash,p_csrf_hash,now()+make_interval(secs=>p_ttl_seconds),now(),now());
 -- Keep at most 10 concurrently active sessions per account.
 UPDATE platform_account_sessions SET revoked_at=now()
  WHERE account_id=p_account_id AND revoked_at IS NULL AND id NOT IN (
   SELECT id FROM platform_account_sessions WHERE account_id=p_account_id
    AND revoked_at IS NULL AND expires_at>now() ORDER BY id DESC LIMIT 10);
 RETURN true;
END $body$;

-- 11. Session resolution with idle timeout; touches last_seen_at.
CREATE FUNCTION spps_identity_dev.auth_session(p_token_hash text, p_idle_seconds integer)
 RETURNS TABLE(session_id integer, account_id integer, person_id integer, display_name text,
               email text, csrf_hash text, roles text[], expires_at timestamptz)
 LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_session platform_account_sessions%ROWTYPE;
BEGIN
 IF NOT is_sha256_hex(p_token_hash) OR p_idle_seconds NOT BETWEEN 60 AND 86400 THEN RETURN; END IF;
 SELECT * INTO v_session FROM platform_account_sessions s
  WHERE s.token_hash=p_token_hash AND s.revoked_at IS NULL AND s.expires_at>now();
 IF NOT FOUND THEN RETURN; END IF;
 IF v_session.last_seen_at <= now()-make_interval(secs=>p_idle_seconds) THEN
  UPDATE platform_account_sessions SET revoked_at=now() WHERE id=v_session.id;
  RETURN;
 END IF;
 UPDATE platform_account_sessions SET last_seen_at=now() WHERE id=v_session.id;
 RETURN QUERY
  SELECT v_session.id, a.id, a.person_id, a.display_name::text, a.email_normalized::text,
         v_session.csrf_hash::text,
         ARRAY(SELECT r.role::text FROM platform_role_grants r WHERE r.account_id=a.id
               AND r.status='ACTIVE' AND r.revoked_at IS NULL ORDER BY r.role),
         v_session.expires_at
  FROM platform_accounts a
  WHERE a.id=v_session.account_id AND a.status='VERIFIED' AND a.verified_at IS NOT NULL;
END $body$;

-- 12. Revocation.
CREATE FUNCTION spps_identity_dev.auth_revoke_session(p_token_hash text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 UPDATE platform_account_sessions SET revoked_at=now()
  WHERE token_hash=p_token_hash AND revoked_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 RETURN n > 0;
END $body$;

CREATE FUNCTION spps_identity_dev.auth_revoke_all_sessions(p_token_hash text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer; n integer;
BEGIN
 SELECT account_id INTO v_account FROM platform_account_sessions
  WHERE token_hash=p_token_hash AND revoked_at IS NULL AND expires_at>now();
 IF v_account IS NULL THEN RETURN 0; END IF;
 UPDATE platform_account_sessions SET revoked_at=now()
  WHERE account_id=v_account AND revoked_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 RETURN n;
END $body$;

-- 13. Password recovery: verified accounts only, single outstanding challenge.
CREATE FUNCTION spps_identity_dev.auth_request_password_reset(p_email text, p_challenge_hash text, p_ttl integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_challenge_hash) OR p_ttl NOT BETWEEN 60 AND 7200 THEN
  RAISE EXCEPTION 'invalid parameters' USING ERRCODE='22023';
 END IF;
 SELECT id INTO v_account FROM platform_accounts
  WHERE email_normalized=lower(btrim(p_email)) AND status='VERIFIED';
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE platform_verification_challenges SET consumed_at=now()
  WHERE account_id=v_account AND purpose='PASSWORD_RESET' AND consumed_at IS NULL;
 INSERT INTO platform_verification_challenges(account_id,token_hash,purpose,expires_at,attempts)
 VALUES (v_account,p_challenge_hash,'PASSWORD_RESET',now()+make_interval(secs=>p_ttl),0);
 RETURN true;
END $body$;

CREATE FUNCTION spps_identity_dev.auth_reset_password(p_challenge_hash text, p_new_hash text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_challenge_hash)
    OR p_new_hash !~ '^pbkdf2_sha256\$[0-9]{6,7}\$[0-9a-f]{32}\$[0-9a-f]{64}$' THEN
  RETURN false;
 END IF;
 UPDATE platform_verification_challenges SET consumed_at=now()
  WHERE token_hash=p_challenge_hash AND purpose='PASSWORD_RESET'
    AND consumed_at IS NULL AND expires_at>now()
  RETURNING account_id INTO v_account;
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE platform_accounts SET password_hash=p_new_hash, password_changed_at=now()
  WHERE id=v_account AND status='VERIFIED';
 UPDATE platform_account_sessions SET revoked_at=now()
  WHERE account_id=v_account AND revoked_at IS NULL;
 RETURN true;
END $body$;

-- 14. Authenticated password change; keeps only the calling session.
CREATE FUNCTION spps_identity_dev.auth_change_password(p_token_hash text, p_old_hash_seen text, p_new_hash text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer; v_session integer;
BEGIN
 IF p_new_hash !~ '^pbkdf2_sha256\$[0-9]{6,7}\$[0-9a-f]{32}\$[0-9a-f]{64}$' THEN RETURN false; END IF;
 SELECT s.id, s.account_id INTO v_session, v_account FROM platform_account_sessions s
  JOIN platform_accounts a ON a.id=s.account_id
  WHERE s.token_hash=p_token_hash AND s.revoked_at IS NULL AND s.expires_at>now()
    AND a.status='VERIFIED' AND a.password_hash=p_old_hash_seen;
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE platform_accounts SET password_hash=p_new_hash, password_changed_at=now() WHERE id=v_account;
 UPDATE platform_account_sessions SET revoked_at=now()
  WHERE account_id=v_account AND revoked_at IS NULL AND id<>v_session;
 RETURN true;
END $body$;

-- 15. Ownership and EXECUTE grants: auth role only, nothing for PUBLIC/portal.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND (p.proname LIKE 'auth\_%' OR p.proname='is_sha256_hex') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname LIKE 'auth\_%' LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
-- is_sha256_hex is called from definer functions as owner; owner needs execute.
GRANT EXECUTE ON FUNCTION spps_identity_dev.is_sha256_hex(text) TO spps_identity_owner;
