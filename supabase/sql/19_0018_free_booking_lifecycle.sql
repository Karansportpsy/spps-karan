-- SPPS production database, step 19 of the serial set: 0018_free_booking_lifecycle
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0018_free_booking_lifecycle.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
SET LOCAL search_path=spps_identity_dev;
SET LOCAL search_path=spps_identity_dev;
-- SPPS Beta v1.0: additive zero-charge booking lifecycle. History, paid ledgers and old procedures retained.
-- Enable only in a disposable identity database first. Review RLS grants and backup before hosted rollout.
ALTER TABLE spps_identity_dev.platform_bookings DROP CONSTRAINT ck_platform_booking_amount;
ALTER TABLE spps_identity_dev.platform_bookings ADD CONSTRAINT ck_platform_booking_amount CHECK (amount_paise >= 0 AND commission_bps BETWEEN 0 AND 10000);

CREATE FUNCTION spps_identity_dev.free_portal_request_booking(
 p_token_hash text, p_link_id integer, p_slot_id integer, p_starts_at timestamptz, p_duration_minutes integer,
 p_amount_paise integer, p_video boolean, p_note text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE c record; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO c FROM portal_booking_context(p_token_hash, p_link_id);
 IF p_slot_id<=0 OR p_duration_minutes NOT BETWEEN 20 AND 120 OR p_amount_paise <> 0
    OR length(coalesce(p_note,''))>300 THEN
  RAISE EXCEPTION 'invalid booking' USING ERRCODE='22023';
 END IF;
 IF p_starts_at <= now()+interval '2 hours' THEN
  RAISE EXCEPTION 'slot too soon or past' USING ERRCODE='22023';
 END IF;
 IF NOT consent_is_current(c.person_id, c.practitioner_id, 'APPOINTMENT_BOOKING')
    OR (p_video AND NOT consent_is_current(c.person_id, c.practitioner_id, 'VIDEO_CONSULTATION')) THEN
  RAISE EXCEPTION 'booking consent required' USING ERRCODE='P0003';
 END IF;
 INSERT INTO platform_bookings(practitioner_id,tenant_id,slot_id,starts_at,ends_at,amount_paise,person_id,link_id,
   requested_by_account_id,guardian_authority_id,status,video_requested,client_note,action_deadline,created_at,updated_at)
 VALUES (c.practitioner_id,c.tenant_id,p_slot_id,p_starts_at,p_starts_at+make_interval(mins=>p_duration_minutes),p_amount_paise,
   c.person_id,p_link_id,c.actor,c.authority_id,'PENDING_APPROVAL',coalesce(p_video,false),btrim(coalesce(p_note,'')),
   least(now()+interval '48 hours', p_starts_at-interval '2 hours'),now(),now())
 RETURNING id INTO v_id;
 PERFORM notify_practitioner(c.practitioner_id,'booking.requested','booking',v_id);
 PERFORM audit(c.actor,'booking.requested','booking',v_id,'');
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.free_practitioner_decide_booking(
 p_practitioner_id integer, p_booking_id integer, p_decision text, p_reason text, p_payment_window_hours integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
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

CREATE FUNCTION spps_identity_dev.free_practitioner_propose_reschedule(
 p_practitioner_id integer, p_booking_id integer, p_slot_id integer, p_starts_at timestamptz,
 p_duration_minutes integer, p_amount_paise integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
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
   created_at,updated_at)
 VALUES (b.practitioner_id,b.tenant_id,p_slot_id,p_starts_at,p_starts_at+make_interval(mins=>p_duration_minutes),
   CASE WHEN b.status='CONFIRMED' THEN b.amount_paise ELSE p_amount_paise END,b.person_id,b.link_id,
   b.requested_by_account_id,b.guardian_authority_id,'RESCHEDULE_PROPOSED',b.video_requested,b.client_note,
   least(now()+interval '48 hours', p_starts_at-interval '2 hours'),b.id,now(),now())
 RETURNING id INTO v_id;
 PERFORM notify_account(b.requested_by_account_id,'booking.reschedule_proposed','booking',v_id);
 PERFORM audit(NULL,'booking.reschedule_proposed','booking',v_id,'from '||b.id);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.free_portal_respond_reschedule(p_token_hash text, p_proposal_id integer, p_accept boolean)
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
 IF NOT p_accept OR o.status NOT IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED') THEN
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

CREATE OR REPLACE FUNCTION spps_identity_dev.booking_cancel_internal(p_booking_id integer, p_reason text, p_actor integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id FOR UPDATE;
 IF b.status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT') THEN
  UPDATE platform_bookings SET status='CANCELLED', decision_reason=left(btrim(coalesce(p_reason,'')),240),
    action_deadline=NULL, updated_at=now() WHERE id=b.id;
  -- An open reschedule proposal for this booking is withdrawn too.
  UPDATE platform_bookings SET status='CANCELLED', updated_at=now() WHERE rescheduled_from_id=b.id AND status='RESCHEDULE_PROPOSED';
  RETURN 'CANCELLED';
 ELSIF b.status='CONFIRMED' AND b.amount_paise=0 AND
       NOT EXISTS (SELECT 1 FROM platform_booking_payments WHERE booking_id=b.id) THEN
  UPDATE platform_bookings SET status='CANCELLED', decision_reason=left(btrim(coalesce(p_reason,'')),240),
    action_deadline=NULL, updated_at=now() WHERE id=b.id;
  UPDATE platform_bookings SET status='CANCELLED', updated_at=now() WHERE rescheduled_from_id=b.id AND status='RESCHEDULE_PROPOSED';
  RETURN 'CANCELLED';
 ELSIF b.status='CONFIRMED' THEN
  UPDATE platform_bookings SET status='REFUND_PENDING', decision_reason=left(btrim(coalesce(p_reason,'')),240),
    updated_at=now() WHERE id=b.id;
  UPDATE platform_booking_payments SET status='REFUND_PENDING' WHERE booking_id=b.id AND status='CAPTURED';
  UPDATE platform_bookings SET status='CANCELLED', updated_at=now() WHERE rescheduled_from_id=b.id AND status='RESCHEDULE_PROPOSED';
  RETURN 'REFUND_PENDING';
 ELSIF b.status='RESCHEDULE_PROPOSED' THEN
  UPDATE platform_bookings SET status='CANCELLED', updated_at=now() WHERE id=b.id;
  RETURN 'CANCELLED';
 END IF;
 RAISE EXCEPTION 'booking cannot be cancelled in status %', b.status USING ERRCODE='P0004';
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_identity_dev'
    AND p.proname IN ('free_portal_request_booking','free_practitioner_decide_booking',
                      'free_practitioner_propose_reschedule','free_portal_respond_reschedule') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0018_free_booking_lifecycle.sql','8f081b5a6bd7d85f57400b0231b76d835bf29c5ca9d58b9cf6baa2e8bb1373ff') ON CONFLICT (name) DO NOTHING;
COMMIT;
