-- SPPS production database, step 48 of the serial set: 0047_v2_guest_booking
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0047_v2_guest_booking.sql
BEGIN;
-- 0047 (guest booking): a visitor can ask for an appointment from the public directory WITHOUT an account.
-- EXPAND-ONLY: two new tables, two new nullable/defaulted columns, new functions. Nothing existing changes.
--
-- Flow:
--  1. guest_request_create    stores the request (no account yet) and a hash of a one-time confirmation token. The
--                             server e-mails the token to the address given, so nobody can book in someone else's name.
--  2. guest_request_confirm   (the visitor clicked the link) creates a "shell" account for that address, verified
--                             because the mailbox was proven, with a random password nobody knows, records the
--                             consents the form showed, and creates the usual PENDING_APPROVAL booking, so the
--                             practitioner sees it exactly like any other request.
--  3. guest_claim_issue       (worker, when the practitioner approves) issues a one-time claim token for a shell
--                             account that has not been claimed yet; the approval e-mail carries the link.
--  4. guest_claim_account     the visitor chooses a password; the account is then a normal account that already
--                             holds the appointment, the link to the practitioner and the video room.
--
-- The application roles have NO table privileges: every read and write goes through these SECURITY DEFINER functions.
-- Tokens are stored only as SHA-256 hashes. Unconfirmed requests are purged by guest_requests_purge.

SET LOCAL ROLE spps_identity_owner;

ALTER TABLE spps_identity_dev.platform_accounts ADD COLUMN guest_origin boolean NOT NULL DEFAULT false;
ALTER TABLE spps_identity_dev.platform_accounts ADD COLUMN claimed_at timestamptz;

CREATE TABLE spps_identity_dev.guest_booking_requests (
    id               serial PRIMARY KEY,
    email_normalized text NOT NULL CHECK (char_length(email_normalized) BETWEEN 6 AND 250),
    display_name     text NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 2 AND 120),
    practitioner_id  integer NOT NULL,
    tenant_id        integer NOT NULL,
    slot_id          integer NOT NULL,
    starts_at        timestamptz NOT NULL,
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 20 AND 120),
    video            boolean NOT NULL DEFAULT false,
    note             text NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
    service_id       integer NOT NULL,
    service_name     text NOT NULL CHECK (char_length(btrim(service_name)) BETWEEN 3 AND 80),
    token_hash       text NOT NULL UNIQUE CHECK (char_length(token_hash) = 64),
    status           text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CONFIRMED', 'EXPIRED', 'CANCELLED')),
    expires_at       timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    confirmed_at     timestamptz,
    account_id       integer REFERENCES spps_identity_dev.platform_accounts(id) ON DELETE SET NULL,
    booking_id       integer REFERENCES spps_identity_dev.platform_bookings(id) ON DELETE SET NULL
);
CREATE INDEX ix_guest_booking_requests_email ON spps_identity_dev.guest_booking_requests(email_normalized, status);
CREATE INDEX ix_guest_booking_requests_account ON spps_identity_dev.guest_booking_requests(account_id);
CREATE INDEX ix_guest_booking_requests_booking ON spps_identity_dev.guest_booking_requests(booking_id);

CREATE TABLE spps_identity_dev.guest_claim_tokens (
    id          serial PRIMARY KEY,
    account_id  integer NOT NULL REFERENCES spps_identity_dev.platform_accounts(id) ON DELETE CASCADE,
    token_hash  text NOT NULL UNIQUE CHECK (char_length(token_hash) = 64),
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_guest_claim_tokens_account ON spps_identity_dev.guest_claim_tokens(account_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['guest_booking_requests', 'guest_claim_tokens'] LOOP
    EXECUTE format('REVOKE ALL ON spps_identity_dev.%I FROM PUBLIC', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY identity_owner_read ON spps_identity_dev.%I FOR SELECT TO spps_identity_owner USING (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_insert ON spps_identity_dev.%I FOR INSERT TO spps_identity_owner WITH CHECK (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_update ON spps_identity_dev.%I FOR UPDATE TO spps_identity_owner USING (true) WITH CHECK (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_delete ON spps_identity_dev.%I FOR DELETE TO spps_identity_owner USING (true)', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- 1. request (no account yet)
-- Returns CREATED, EXISTING_ACCOUNT (the address already belongs to a claimed account: the server sends a
-- "sign in instead" notice) or LIMITED (too many open requests for this address). The server answers every outcome
-- the same way, so the response never reveals whether an address has an account.
CREATE FUNCTION spps_identity_dev.guest_request_create(
 p_email text, p_name text, p_practitioner_id integer, p_tenant_id integer, p_slot_id integer,
 p_starts_at timestamptz, p_duration_minutes integer, p_video boolean, p_note text,
 p_service_id integer, p_service_name text, p_token_hash text, p_ttl_seconds integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_email text := lower(btrim(p_email)); v_claimed boolean;
BEGIN
 IF v_email !~ '^[^@\s]{1,64}@[^@\s]{1,185}\.[^@\s]{2,63}$' OR length(v_email) > 250
    OR length(btrim(coalesce(p_name,''))) NOT BETWEEN 2 AND 120
    OR p_practitioner_id IS NULL OR p_practitioner_id <= 0 OR p_tenant_id IS NULL OR p_tenant_id <= 0
    OR p_slot_id IS NULL OR p_slot_id <= 0 OR p_service_id IS NULL OR p_service_id <= 0
    OR length(btrim(coalesce(p_service_name,''))) NOT BETWEEN 3 AND 80
    OR p_duration_minutes NOT BETWEEN 20 AND 120 OR length(coalesce(p_note,'')) > 300
    OR NOT is_sha256_hex(p_token_hash) OR p_ttl_seconds NOT BETWEEN 300 AND 86400 THEN
  RAISE EXCEPTION 'invalid request' USING ERRCODE='22023';
 END IF;
 IF p_starts_at IS NULL OR p_starts_at <= now() + interval '2 hours' THEN
  RAISE EXCEPTION 'slot too soon or past' USING ERRCODE='22023';
 END IF;
 UPDATE guest_booking_requests SET status='EXPIRED' WHERE status='PENDING' AND expires_at <= now();
 SELECT (a.guest_origin AND a.claimed_at IS NULL) IS NOT TRUE INTO v_claimed
  FROM platform_accounts a WHERE a.email_normalized = v_email;
 IF v_claimed IS TRUE THEN RETURN 'EXISTING_ACCOUNT'; END IF;
 IF (SELECT count(*) FROM guest_booking_requests r WHERE r.email_normalized = v_email AND r.status='PENDING') >= 3 THEN
  RETURN 'LIMITED';
 END IF;
 INSERT INTO guest_booking_requests(email_normalized,display_name,practitioner_id,tenant_id,slot_id,starts_at,
   duration_minutes,video,note,service_id,service_name,token_hash,expires_at)
 VALUES (v_email,btrim(p_name),p_practitioner_id,p_tenant_id,p_slot_id,p_starts_at,p_duration_minutes,
   coalesce(p_video,false),btrim(coalesce(p_note,'')),p_service_id,btrim(p_service_name),p_token_hash,
   now()+make_interval(secs=>p_ttl_seconds));
 RETURN 'CREATED';
END $body$;

-- What a still-valid token asks for, without using it up: the server checks that the time is still open first.
CREATE FUNCTION spps_identity_dev.guest_request_peek(p_token_hash text)
 RETURNS TABLE(practitioner_id integer, tenant_id integer, slot_id integer, service_id integer,
               starts_at timestamptz, duration_minutes integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT g.practitioner_id, g.tenant_id, g.slot_id, g.service_id, g.starts_at, g.duration_minutes
 FROM guest_booking_requests g
 WHERE is_sha256_hex(p_token_hash) AND g.token_hash=p_token_hash AND g.status='PENDING' AND g.expires_at>now()
$body$;

-- ---------------------------------------------------------------- 2. confirm (the visitor clicked the e-mail link)
-- Outcomes: OK, INVALID (unknown, used or expired token), EXISTING_ACCOUNT, SLOT_TAKEN, ALREADY (a request from this
-- person to this practitioner is already waiting), TOO_SOON (the slot is no longer far enough ahead).
CREATE FUNCTION spps_identity_dev.guest_request_confirm(p_token_hash text, p_policy_version text)
 RETURNS TABLE(outcome text, booking_id integer, account_id integer)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE r guest_booking_requests%ROWTYPE; a platform_accounts%ROWTYPE; v_person integer; v_account integer;
        v_link integer; v_id integer; v_purpose text;
BEGIN
 IF NOT is_sha256_hex(p_token_hash) OR p_policy_version !~ '^[a-z0-9][a-z0-9._-]{0,39}$' THEN
  RETURN QUERY SELECT 'INVALID'::text, NULL::integer, NULL::integer; RETURN;
 END IF;
 PERFORM booking_expire_due();
 SELECT * INTO r FROM guest_booking_requests g
  WHERE g.token_hash = p_token_hash AND g.status = 'PENDING' AND g.expires_at > now() FOR UPDATE;
 IF NOT FOUND THEN RETURN QUERY SELECT 'INVALID'::text, NULL::integer, NULL::integer; RETURN; END IF;
 IF r.starts_at <= now() + interval '2 hours' THEN
  UPDATE guest_booking_requests SET status='EXPIRED' WHERE id = r.id;
  RETURN QUERY SELECT 'TOO_SOON'::text, NULL::integer, NULL::integer; RETURN;
 END IF;
 SELECT * INTO a FROM platform_accounts x WHERE x.email_normalized = r.email_normalized;
 IF FOUND THEN
  IF NOT (a.guest_origin AND a.claimed_at IS NULL) THEN
   UPDATE guest_booking_requests SET status='CANCELLED' WHERE id = r.id;
   RETURN QUERY SELECT 'EXISTING_ACCOUNT'::text, NULL::integer, NULL::integer; RETURN;
  END IF;
  v_account := a.id; v_person := a.person_id;
 ELSE
  INSERT INTO platform_people(created_at) VALUES (now()) RETURNING id INTO v_person;
  -- A random password hash nobody can match: the account cannot be signed into until the person claims it.
  INSERT INTO platform_accounts(person_id,email_normalized,password_hash,display_name,status,verified_at,
                                adult_attested_at,password_changed_at,created_at,guest_origin)
  VALUES (v_person,r.email_normalized,
          'pbkdf2_sha256$600000$'||md5(random()::text||clock_timestamp()::text)||'$'
            ||encode(sha256((random()::text||clock_timestamp()::text||r.token_hash)::bytea),'hex'),
          r.display_name,'VERIFIED',now(),now(),now(),now(),true)
  RETURNING id INTO v_account;
  INSERT INTO platform_role_grants(account_id,role,status,created_at) VALUES (v_account,'ATHLETE','ACTIVE',now());
  PERFORM audit(v_account,'guest.account_created','account',v_account,'');
 END IF;
 -- The consents the booking form showed, recorded by the person for themselves.
 FOREACH v_purpose IN ARRAY ARRAY['PLATFORM_TERMS','PRIVACY_NOTICE','CASE_LINK','APPOINTMENT_BOOKING','VIDEO_CONSULTATION'] LOOP
  CONTINUE WHEN v_purpose = 'VIDEO_CONSULTATION' AND NOT r.video;
  IF NOT consent_is_current(v_person, CASE WHEN v_purpose IN ('PLATFORM_TERMS','PRIVACY_NOTICE') THEN NULL
                                            ELSE r.practitioner_id END, v_purpose) THEN
   INSERT INTO platform_consent_events(person_id,actor_account_id,guardian_authority_id,practitioner_id,purpose,
     policy_version,decision,supersedes_event_id,recorded_at)
   VALUES (v_person,v_account,NULL,
           CASE WHEN v_purpose IN ('PLATFORM_TERMS','PRIVACY_NOTICE') THEN NULL ELSE r.practitioner_id END,
           v_purpose,p_policy_version,'GRANTED',
           (SELECT max(e.id) FROM platform_consent_events e WHERE e.person_id=v_person AND e.purpose=v_purpose
              AND e.practitioner_id IS NOT DISTINCT FROM
                  CASE WHEN v_purpose IN ('PLATFORM_TERMS','PRIVACY_NOTICE') THEN NULL ELSE r.practitioner_id END),
           now());
  END IF;
 END LOOP;
 -- The link and the booking: the same rows portal_request_directory_booking creates.
 SELECT l.id INTO v_link FROM practitioner_athlete_links l
  WHERE l.person_id=v_person AND l.practitioner_id=r.practitioner_id
    AND l.status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE')
  ORDER BY l.id DESC LIMIT 1;
 IF v_link IS NULL THEN
  INSERT INTO practitioner_athlete_links(person_id,practitioner_id,athlete_record_id,tenant_id,status,origin,
    requested_by_account_id,guardian_authority_id,requested_at)
  VALUES (v_person,r.practitioner_id,NULL,r.tenant_id,'PRACTITIONER_REVIEW','directory',v_account,NULL,now())
  RETURNING id INTO v_link;
  PERFORM audit(v_account,'link.requested_from_directory','link',v_link,'guest');
 ELSIF EXISTS (SELECT 1 FROM platform_bookings b WHERE b.link_id=v_link AND b.status='PENDING_APPROVAL') THEN
  UPDATE guest_booking_requests SET status='CANCELLED' WHERE id = r.id;
  RETURN QUERY SELECT 'ALREADY'::text, NULL::integer, v_account; RETURN;
 END IF;
 BEGIN
  INSERT INTO platform_bookings(practitioner_id,tenant_id,slot_id,starts_at,ends_at,amount_paise,person_id,link_id,
    requested_by_account_id,guardian_authority_id,status,video_requested,client_note,action_deadline,created_at,
    updated_at,service_id,service_name)
  VALUES (r.practitioner_id,r.tenant_id,r.slot_id,r.starts_at,r.starts_at+make_interval(mins=>r.duration_minutes),0,
    v_person,v_link,v_account,NULL,'PENDING_APPROVAL',r.video,r.note,
    least(now()+interval '48 hours', r.starts_at-interval '2 hours'),now(),now(),r.service_id,r.service_name)
  RETURNING id INTO v_id;
 EXCEPTION WHEN exclusion_violation OR unique_violation THEN
  UPDATE guest_booking_requests SET status='CANCELLED' WHERE id = r.id;
  RETURN QUERY SELECT 'SLOT_TAKEN'::text, NULL::integer, v_account; RETURN;
 END;
 PERFORM notify_practitioner(r.practitioner_id,'booking.requested','booking',v_id);
 PERFORM audit(v_account,'booking.requested','booking',v_id,'guest');
 UPDATE guest_booking_requests SET status='CONFIRMED', confirmed_at=now(), account_id=v_account, booking_id=v_id
  WHERE id = r.id;
 RETURN QUERY SELECT 'OK'::text, v_id, v_account;
END $body$;

-- ---------------------------------------------------------------- 3. claim link (issued by the worker on approval)
-- Takes the id of a booking.confirmed notification. TRUE only when it belongs to a shell account that has not been
-- claimed; any earlier unused token for that account is retired. The worker then puts the link in the e-mail.
CREATE FUNCTION spps_identity_dev.guest_claim_issue(p_notification_id integer, p_token_hash text, p_ttl_seconds integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_token_hash) OR p_ttl_seconds NOT BETWEEN 3600 AND 2592000 THEN
  RAISE EXCEPTION 'invalid parameters' USING ERRCODE='22023';
 END IF;
 SELECT a.id INTO v_account FROM platform_notifications n JOIN platform_accounts a ON a.id=n.account_id
  WHERE n.id=p_notification_id AND n.kind='booking.confirmed' AND a.guest_origin
    AND a.claimed_at IS NULL AND a.status='VERIFIED';
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE guest_claim_tokens SET consumed_at=now() WHERE account_id=v_account AND consumed_at IS NULL;
 INSERT INTO guest_claim_tokens(account_id,token_hash,expires_at)
 VALUES (v_account,p_token_hash,now()+make_interval(secs=>p_ttl_seconds));
 RETURN true;
END $body$;

-- ---------------------------------------------------------------- 4. claim (the visitor chooses a password)
CREATE FUNCTION spps_identity_dev.guest_claim_account(p_token_hash text, p_new_hash text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_account integer;
BEGIN
 IF NOT is_sha256_hex(p_token_hash)
    OR p_new_hash !~ '^pbkdf2_sha256\$[0-9]{6,7}\$[0-9a-f]{32}\$[0-9a-f]{64}$' THEN
  RETURN false;
 END IF;
 UPDATE guest_claim_tokens SET consumed_at=now()
  WHERE token_hash=p_token_hash AND consumed_at IS NULL AND expires_at>now()
  RETURNING account_id INTO v_account;
 IF v_account IS NULL THEN RETURN false; END IF;
 UPDATE platform_accounts SET password_hash=p_new_hash, password_changed_at=now(), claimed_at=now()
  WHERE id=v_account AND guest_origin AND status='VERIFIED';
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM audit(v_account,'guest.account_claimed','account',v_account,'');
 RETURN true;
END $body$;

-- ---------------------------------------------------------------- video join window: 15 minutes before the start
-- (was 10, migration 0045). Same function, same owner and grants; only the opening time moves. The practitioner can
-- enter first and the guest waits on the other side of the door (web/room.js).
CREATE OR REPLACE FUNCTION spps_identity_dev.video_booking_open(b spps_identity_dev.platform_bookings)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT b.status='CONFIRMED' AND b.video_requested AND b.video_closed_at IS NULL
    AND now() BETWEEN b.starts_at-interval '15 minutes' AND b.ends_at+interval '15 minutes'
    AND link_is_active(b.link_id)
    AND consent_is_current(b.person_id, b.practitioner_id, 'VIDEO_CONSULTATION')
$body$;

-- ---------------------------------------------------------------- 5. housekeeping
-- Unconfirmed requests hold an address and a name; they are removed a week after they expire.
CREATE FUNCTION spps_identity_dev.guest_requests_purge()
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 DELETE FROM guest_booking_requests WHERE status IN ('PENDING','EXPIRED','CANCELLED')
   AND expires_at < now() - interval '7 days';
 GET DIAGNOSTICS n = ROW_COUNT;
 DELETE FROM guest_claim_tokens WHERE expires_at < now() - interval '30 days';
 RETURN n;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_identity_dev'
    AND p.proname IN ('guest_request_create','guest_request_peek','guest_request_confirm','guest_claim_issue','guest_claim_account',
                      'guest_requests_purge') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0047_v2_guest_booking.sql','0e8bf66119fe88affd00c046095194c4f90b28c0f6c98d04b6f19b9559e76d5f') ON CONFLICT (name) DO NOTHING;
COMMIT;
