BEGIN;
-- Phase 3: appointments and the video join window. Expand-only; no tables or grants widened.
-- Replace identity functions as their owning NOLOGIN role (also works for a non-superuser installer).
SET LOCAL ROLE spps_identity_owner;

CREATE OR REPLACE FUNCTION spps_identity_dev.video_booking_open(b spps_identity_dev.platform_bookings)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT b.status='CONFIRMED' AND b.video_requested AND b.video_closed_at IS NULL
    AND now() BETWEEN b.starts_at-interval '10 minutes' AND b.ends_at+interval '15 minutes'
    AND link_is_active(b.link_id)
    AND consent_is_current(b.person_id, b.practitioner_id, 'VIDEO_CONSULTATION')
$body$;

-- Extra booking metadata for the authenticated athlete/authorised guardian only.
-- No clinical tables are read; the service name is the immutable booking snapshot.
CREATE FUNCTION spps_identity_dev.portal_booking_details(p_token_hash text)
 RETURNS TABLE(booking_id integer, service_name text)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 RETURN QUERY SELECT b.id, b.service_name::text FROM platform_bookings b
 WHERE booking_actor_allowed(v_actor,b.person_id,false);
END $body$;
REVOKE ALL ON FUNCTION spps_identity_dev.portal_booking_details(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.portal_booking_details(text) TO spps_identity_auth;

-- Keep approval-first and guardian authority logic; refuse past changes and retain service snapshots.
CREATE OR REPLACE FUNCTION spps_identity_dev.free_practitioner_decide_booking(
 p_practitioner_id integer, p_booking_id integer, p_decision text, p_reason text, p_payment_window_hours integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF b.starts_at <= now() THEN RAISE EXCEPTION 'appointment has started' USING ERRCODE='P0004'; END IF;
 IF p_decision='APPROVE' THEN
  IF b.status<>'PENDING_APPROVAL' THEN RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004'; END IF;
  IF b.amount_paise<>0 OR EXISTS (SELECT 1 FROM platform_booking_payments WHERE booking_id=b.id) THEN
   RAISE EXCEPTION 'not a free booking' USING ERRCODE='P0004';
  END IF;
  IF NOT link_is_active(b.link_id) THEN RAISE EXCEPTION 'portal link is no longer active' USING ERRCODE='P0004'; END IF;
  UPDATE platform_bookings SET status='CONFIRMED', decision_reason=nullif(left(btrim(coalesce(p_reason,'')),240),''),
    action_deadline=NULL, updated_at=now()
   WHERE id=b.id;
  PERFORM notify_account(b.requested_by_account_id,'booking.confirmed','booking',b.id);
  PERFORM audit(NULL,'booking.approved','booking',b.id,'practitioner '||p_practitioner_id);
  RETURN 'CONFIRMED';
 ELSIF p_decision='DECLINE' THEN
  -- Decline happens before any payment exists: never a refund path.
  IF b.status NOT IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT') THEN
   RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004';
  END IF;
  IF EXISTS (SELECT 1 FROM platform_booking_payments WHERE booking_id=b.id) THEN
   RAISE EXCEPTION 'paid booking must be cancelled with refund' USING ERRCODE='P0004';
  END IF;
  UPDATE platform_bookings SET status='DECLINED', decision_reason=nullif(left(btrim(coalesce(p_reason,'')),240),''),
    action_deadline=NULL, updated_at=now() WHERE id=b.id;
  PERFORM notify_account(b.requested_by_account_id,'booking.declined','booking',b.id);
  PERFORM audit(NULL,'booking.declined','booking',b.id,'practitioner '||p_practitioner_id);
  RETURN 'DECLINED';
 END IF;
 RAISE EXCEPTION 'invalid decision' USING ERRCODE='22023';
END $body$;

CREATE OR REPLACE FUNCTION spps_identity_dev.free_practitioner_propose_reschedule(
 p_practitioner_id integer, p_booking_id integer, p_slot_id integer, p_starts_at timestamptz,
 p_duration_minutes integer, p_amount_paise integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF b.starts_at <= now() THEN RAISE EXCEPTION 'appointment has started' USING ERRCODE='P0004'; END IF;
 IF b.amount_paise<>0 THEN RAISE EXCEPTION 'not a free booking' USING ERRCODE='P0004'; END IF;
 IF b.status NOT IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED') THEN
  RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004';
 END IF;
 IF p_starts_at <= now()+interval '2 hours' OR p_duration_minutes NOT BETWEEN 20 AND 120
    OR p_amount_paise <> 0 OR p_slot_id=b.slot_id THEN
  RAISE EXCEPTION 'invalid proposal' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_bookings(practitioner_id,tenant_id,slot_id,starts_at,ends_at,amount_paise,person_id,link_id,
   requested_by_account_id,guardian_authority_id,status,video_requested,client_note,action_deadline,rescheduled_from_id,
   created_at,updated_at,service_id,service_name)
 VALUES (b.practitioner_id,b.tenant_id,p_slot_id,p_starts_at,p_starts_at+make_interval(mins=>p_duration_minutes),
   CASE WHEN b.status='CONFIRMED' THEN b.amount_paise ELSE p_amount_paise END,b.person_id,b.link_id,
   b.requested_by_account_id,b.guardian_authority_id,'RESCHEDULE_PROPOSED',b.video_requested,b.client_note,
   least(now()+interval '48 hours', p_starts_at-interval '2 hours'),b.id,now(),now(),b.service_id,b.service_name)
 RETURNING id INTO v_id;
 PERFORM notify_account(b.requested_by_account_id,'booking.reschedule_proposed','booking',v_id);
 PERFORM audit(NULL,'booking.reschedule_proposed','booking',v_id,'from '||b.id);
 RETURN v_id;
END $body$;

CREATE OR REPLACE FUNCTION spps_identity_dev.free_portal_respond_reschedule(p_token_hash text, p_proposal_id integer, p_accept boolean)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); p platform_bookings%ROWTYPE; o platform_bookings%ROWTYPE;
        v_new text;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO p FROM platform_bookings WHERE id=p_proposal_id AND status='RESCHEDULE_PROPOSED' FOR UPDATE;
 IF NOT FOUND OR NOT booking_actor_allowed(v_actor, p.person_id, true) THEN
  RAISE EXCEPTION 'proposal not found' USING ERRCODE='P0002';
 END IF;
 SELECT * INTO o FROM platform_bookings WHERE id=p.rescheduled_from_id FOR UPDATE;
 IF p.starts_at <= now() OR o.starts_at <= now()
    OR o.status NOT IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED') THEN
  RAISE EXCEPTION 'appointment cannot change' USING ERRCODE='P0004';
 END IF;
 IF NOT p_accept THEN
  UPDATE platform_bookings SET status='CANCELLED', updated_at=now() WHERE id=p.id;
  PERFORM notify_practitioner(p.practitioner_id,'booking.reschedule_declined','booking',p.id);
  RETURN 'DECLINED';
 END IF;
 v_new := CASE WHEN o.status='CONFIRMED' THEN 'CONFIRMED' ELSE 'PENDING_APPROVAL' END;
 IF o.amount_paise<>0 OR p.amount_paise<>0 OR EXISTS (SELECT 1 FROM platform_booking_payments WHERE booking_id IN (o.id,p.id)) THEN
  RAISE EXCEPTION 'not a free booking' USING ERRCODE='P0004';
 END IF;
 -- Release the original first so the person-overlap constraint admits the new time.
 UPDATE platform_bookings SET status='CANCELLED', decision_reason='Rescheduled', action_deadline=NULL, updated_at=now()
  WHERE id=o.id;
 UPDATE platform_booking_payments SET booking_id=p.id WHERE booking_id=o.id;
 UPDATE platform_bookings SET status=v_new, updated_at=now(),
   amount_paise=CASE WHEN v_new='CONFIRMED' THEN o.amount_paise ELSE p.amount_paise END,
   action_deadline=CASE WHEN v_new='CONFIRMED' THEN NULL ELSE least(now()+interval '24 hours', p.starts_at-interval '1 hour') END
  WHERE id=p.id;
 PERFORM notify_practitioner(p.practitioner_id,'booking.reschedule_accepted','booking',p.id);
 PERFORM audit(v_actor,'booking.reschedule_accepted','booking',p.id,v_new);
 RETURN v_new;
END $body$;

-- Recheck guardian booking authority on ticket use as well as on issue.
CREATE OR REPLACE FUNCTION spps_identity_dev.video_ticket_check(p_ticket_hash text)
 RETURNS TABLE(booking_id integer, role text, consented boolean)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE t platform_video_tickets%ROWTYPE; b platform_bookings%ROWTYPE;
BEGIN
 IF NOT is_sha256_hex(p_ticket_hash) THEN RETURN; END IF;
 SELECT * INTO t FROM platform_video_tickets WHERE ticket_hash=p_ticket_hash AND revoked_at IS NULL AND expires_at>=now();
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO b FROM platform_bookings WHERE id=t.booking_id;
 IF NOT video_booking_open(b) THEN RETURN; END IF;
 IF t.role='guest' AND NOT booking_actor_allowed(t.issued_to_account_id,b.person_id,true) THEN RETURN; END IF;
 RETURN QUERY SELECT t.booking_id, t.role::text, t.consent_at IS NOT NULL;
END $body$;

RESET ROLE;
COMMIT;
