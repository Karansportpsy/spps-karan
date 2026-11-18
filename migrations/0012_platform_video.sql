-- Phase 9: video consultation for CONFIRMED platform bookings.
-- Each participant obtains a short-lived ticket from their OWN authenticated session
-- (practitioner session or portal session). Tickets are stored as SHA-256 hashes and
-- re-validated on every use: booking still CONFIRMED with video requested, within the
-- join window, link active, VIDEO_CONSULTATION consent current. No media passes the server.

CREATE FUNCTION spps_identity_dev.video_booking_open(b spps_identity_dev.platform_bookings)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT b.status='CONFIRMED' AND b.video_requested
    AND now() BETWEEN b.starts_at-interval '15 minutes' AND b.ends_at+interval '15 minutes'
    AND link_is_active(b.link_id)
    AND consent_is_current(b.person_id, b.practitioner_id, 'VIDEO_CONSULTATION')
$body$;

CREATE FUNCTION spps_identity_dev.video_issue(b spps_identity_dev.platform_bookings, p_role text, p_account integer, p_ticket_hash text)
 RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_exp timestamptz := b.ends_at+interval '15 minutes';
BEGIN
 IF NOT is_sha256_hex(p_ticket_hash) THEN RAISE EXCEPTION 'invalid ticket' USING ERRCODE='22023'; END IF;
 IF NOT video_booking_open(b) THEN
  RAISE EXCEPTION 'video room is not open for this booking' USING ERRCODE='P0004';
 END IF;
 -- One live ticket per role: issuing a new one revokes the previous (lost device / new tab).
 UPDATE platform_video_tickets SET revoked_at=now() WHERE booking_id=b.id AND role=p_role AND revoked_at IS NULL;
 INSERT INTO platform_video_tickets(booking_id,role,ticket_hash,issued_to_account_id,expires_at,created_at)
 VALUES (b.id,p_role,p_ticket_hash,p_account,v_exp,now());
 PERFORM audit(p_account,'video.ticket_issued','booking',b.id,p_role);
 RETURN v_exp;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_video_ticket(p_practitioner_id integer, p_booking_id integer, p_ticket_hash text)
 RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 RETURN video_issue(b, 'practitioner', NULL, p_ticket_hash);
END $body$;

CREATE FUNCTION spps_identity_dev.portal_video_ticket(p_token_hash text, p_booking_id integer, p_ticket_hash text)
 RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); b platform_bookings%ROWTYPE;
BEGIN
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id;
 IF NOT FOUND OR NOT booking_actor_allowed(v_actor, b.person_id, true) THEN
  RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002';
 END IF;
 RETURN video_issue(b, 'guest', v_actor, p_ticket_hash);
END $body$;

-- Re-validated on EVERY status/ice/consent/websocket use.
CREATE FUNCTION spps_identity_dev.video_ticket_check(p_ticket_hash text)
 RETURNS TABLE(booking_id integer, role text, consented boolean)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE t platform_video_tickets%ROWTYPE; b platform_bookings%ROWTYPE;
BEGIN
 IF NOT is_sha256_hex(p_ticket_hash) THEN RETURN; END IF;
 SELECT * INTO t FROM platform_video_tickets WHERE ticket_hash=p_ticket_hash AND revoked_at IS NULL AND expires_at>now();
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO b FROM platform_bookings WHERE id=t.booking_id;
 IF NOT video_booking_open(b) THEN RETURN; END IF;
 RETURN QUERY SELECT t.booking_id, t.role::text, t.consent_at IS NOT NULL;
END $body$;

CREATE FUNCTION spps_identity_dev.video_ticket_consent(p_ticket_hash text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM video_ticket_check(p_ticket_hash)) THEN RETURN false; END IF;
 UPDATE platform_video_tickets SET consent_at=coalesce(consent_at, now()) WHERE ticket_hash=p_ticket_hash;
 GET DIAGNOSTICS n = ROW_COUNT;
 RETURN n=1;
END $body$;

-- Practitioner ends the session: all tickets revoked.
CREATE FUNCTION spps_identity_dev.practitioner_end_video(p_practitioner_id integer, p_booking_id integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id) THEN
  RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002';
 END IF;
 UPDATE platform_video_tickets SET revoked_at=now() WHERE booking_id=p_booking_id AND revoked_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 RETURN n;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN (
           'video_booking_open','video_issue','practitioner_video_ticket','portal_video_ticket',
           'video_ticket_check','video_ticket_consent','practitioner_end_video') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  IF f.proname NOT IN ('video_booking_open','video_issue') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
