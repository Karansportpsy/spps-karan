-- SPPS-VIDEO-001 follow-up: ending a video consultation is final.
-- Before: practitioner_end_video only revoked the CURRENT tickets, so either participant could
-- immediately obtain a NEW ticket from their own session and reopen the ended consultation.
-- After: the booking records video_closed_at; video_booking_open (used by ticket issue AND every
-- ticket check / WebSocket join) refuses a closed room. Additive and idempotent: one nullable
-- column, no data rewrite, no table count change.

ALTER TABLE spps_identity_dev.platform_bookings ADD COLUMN IF NOT EXISTS video_closed_at timestamptz;

CREATE OR REPLACE FUNCTION spps_identity_dev.video_booking_open(b spps_identity_dev.platform_bookings)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT b.status='CONFIRMED' AND b.video_requested AND b.video_closed_at IS NULL
    AND now() BETWEEN b.starts_at-interval '15 minutes' AND b.ends_at+interval '15 minutes'
    AND link_is_active(b.link_id)
    AND consent_is_current(b.person_id, b.practitioner_id, 'VIDEO_CONSULTATION')
$body$;

CREATE OR REPLACE FUNCTION spps_identity_dev.video_issue(b spps_identity_dev.platform_bookings, p_role text, p_account integer, p_ticket_hash text)
 RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_exp timestamptz := b.ends_at+interval '15 minutes';
BEGIN
 IF NOT is_sha256_hex(p_ticket_hash) THEN RAISE EXCEPTION 'invalid ticket' USING ERRCODE='22023'; END IF;
 IF b.video_closed_at IS NOT NULL THEN
  RAISE EXCEPTION 'video consultation has ended' USING ERRCODE='P0005';
 END IF;
 IF NOT video_booking_open(b) THEN
  RAISE EXCEPTION 'video room is not open for this booking' USING ERRCODE='P0004';
 END IF;
 UPDATE platform_video_tickets SET revoked_at=now() WHERE booking_id=b.id AND role=p_role AND revoked_at IS NULL;
 INSERT INTO platform_video_tickets(booking_id,role,ticket_hash,issued_to_account_id,expires_at,created_at)
 VALUES (b.id,p_role,p_ticket_hash,p_account,v_exp,now());
 PERFORM audit(p_account,'video.ticket_issued','booking',b.id,p_role);
 RETURN v_exp;
END $body$;

CREATE OR REPLACE FUNCTION spps_identity_dev.practitioner_end_video(p_practitioner_id integer, p_booking_id integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id) THEN
  RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002';
 END IF;
 UPDATE platform_bookings SET video_closed_at=coalesce(video_closed_at, now()), updated_at=now()
  WHERE id=p_booking_id AND practitioner_id=p_practitioner_id;
 UPDATE platform_video_tickets SET revoked_at=now() WHERE booking_id=p_booking_id AND revoked_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 PERFORM audit(NULL,'video.room_closed','booking',p_booking_id,'practitioner '||p_practitioner_id);
 RETURN n;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN ('video_booking_open','video_issue','practitioner_end_video') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
 END LOOP;
 EXECUTE 'GRANT EXECUTE ON FUNCTION spps_identity_dev.practitioner_end_video(integer, integer) TO spps_identity_auth';
END $$;

-- Booking lists expose whether the room was closed, so clients hide "Join video" after "End video".
-- Same bodies as migration 0009 plus one trailing output column (OUT columns change => DROP + CREATE).
DROP FUNCTION spps_identity_dev.portal_list_bookings(text);
DROP FUNCTION spps_identity_dev.practitioner_list_bookings(integer);
CREATE FUNCTION spps_identity_dev.portal_list_bookings(p_token_hash text)
 RETURNS TABLE(booking_id integer, practitioner_id integer, person_id integer, subject_display_name text, slot_id integer,
               starts_at timestamptz, ends_at timestamptz, amount_paise integer, currency text, status text,
               video_requested boolean, action_deadline timestamptz, decision_reason text, rescheduled_from_id integer,
               can_manage boolean, paid boolean, video_closed boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY
 SELECT b.id, b.practitioner_id, b.person_id, COALESCE(NULLIF(p.display_name,''), a.display_name)::text, b.slot_id,
        b.starts_at, b.ends_at, b.amount_paise, b.currency::text, b.status::text, b.video_requested, b.action_deadline,
        b.decision_reason::text, b.rescheduled_from_id, booking_actor_allowed(v_actor, b.person_id, true),
        EXISTS (SELECT 1 FROM platform_booking_payments x WHERE x.booking_id=b.id),
        b.video_closed_at IS NOT NULL
 FROM platform_bookings b JOIN platform_people p ON p.id=b.person_id
 LEFT JOIN platform_accounts a ON a.person_id=b.person_id
 WHERE booking_actor_allowed(v_actor, b.person_id, false)
 ORDER BY b.starts_at DESC, b.id DESC;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_list_bookings(p_practitioner_id integer)
 RETURNS TABLE(booking_id integer, slot_id integer, starts_at timestamptz, ends_at timestamptz, amount_paise integer,
               status text, subject_display_name text, via_guardian boolean, video_requested boolean, client_note text,
               action_deadline timestamptz, rescheduled_from_id integer, paid boolean, payment_status text,
               practitioner_paise integer, platform_paise integer, video_closed boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY
 SELECT b.id, b.slot_id, b.starts_at, b.ends_at, b.amount_paise, b.status::text,
        COALESCE(NULLIF(p.display_name,''), a.display_name)::text, b.guardian_authority_id IS NOT NULL,
        b.video_requested, b.client_note::text, b.action_deadline, b.rescheduled_from_id,
        pay.id IS NOT NULL, pay.status::text, pay.practitioner_paise, pay.platform_paise,
        b.video_closed_at IS NOT NULL
 FROM platform_bookings b JOIN platform_people p ON p.id=b.person_id
 LEFT JOIN platform_accounts a ON a.person_id=b.person_id
 LEFT JOIN LATERAL (SELECT * FROM platform_booking_payments x WHERE x.booking_id=b.id ORDER BY x.id DESC LIMIT 1) pay ON true
 WHERE b.practitioner_id=p_practitioner_id
 ORDER BY b.starts_at DESC, b.id DESC;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN ('portal_list_bookings','practitioner_list_bookings') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
