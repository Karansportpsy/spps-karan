-- Stage 13B: approval-first booking, provider-verified payment ledger, notification outbox.
-- Lifecycle: PENDING_APPROVAL -> APPROVED_AWAITING_PAYMENT -> CONFIRMED -> COMPLETED
--   alternatives: DECLINED, EXPIRED, CANCELLED, RESCHEDULE_PROPOSED, REFUND_PENDING -> REFUNDED
-- Payment orders can only exist for APPROVED_AWAITING_PAYMENT bookings. Captures are
-- recorded only after provider verification by the API, idempotently by payment id.
-- Practitioner functions receive server-verified practitioner_id; portal functions derive
-- the actor from the session token hash. EXECUTE granted to spps_identity_auth only.

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Atomic slot protection: no two holding bookings may overlap for one practitioner,
-- and one person cannot hold overlapping appointments (reschedule proposals excepted).
ALTER TABLE spps_identity_dev.platform_bookings ADD CONSTRAINT ex_booking_practitioner_time
 EXCLUDE USING gist (practitioner_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
 WHERE (status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED','RESCHEDULE_PROPOSED','COMPLETED'));
ALTER TABLE spps_identity_dev.platform_bookings ADD CONSTRAINT ex_booking_person_time
 EXCLUDE USING gist (person_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
 WHERE (status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED'));
CREATE UNIQUE INDEX uq_one_open_reschedule ON spps_identity_dev.platform_bookings (rescheduled_from_id)
 WHERE status='RESCHEDULE_PROPOSED';

-- ---------------------------------------------------------------- helpers
CREATE FUNCTION spps_identity_dev.notify_account(p_account integer, p_kind text, p_type text, p_id integer)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 INSERT INTO platform_notifications(account_id,kind,object_type,object_id,created_at) VALUES (p_account,p_kind,p_type,p_id,now())
$body$;
CREATE FUNCTION spps_identity_dev.notify_practitioner(p_practitioner integer, p_kind text, p_type text, p_id integer)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 INSERT INTO platform_notifications(practitioner_id,kind,object_type,object_id,created_at) VALUES (p_practitioner,p_kind,p_type,p_id,now())
$body$;

-- Lazy expiry: every booking entry point calls this first, so stale holds never block
-- a new claim and an expired approval can never be paid into CONFIRMED.
CREATE FUNCTION spps_identity_dev.booking_expire_due()
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE r record; n integer := 0;
BEGIN
 FOR r IN UPDATE platform_bookings SET status='EXPIRED', updated_at=now()
   WHERE status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','RESCHEDULE_PROPOSED')
     AND action_deadline IS NOT NULL AND action_deadline<=now()
   RETURNING id, practitioner_id, requested_by_account_id LOOP
  PERFORM notify_account(r.requested_by_account_id,'booking.expired','booking',r.id);
  PERFORM notify_practitioner(r.practitioner_id,'booking.expired','booking',r.id);
  PERFORM audit(NULL,'booking.expired','booking',r.id,'');
  n := n + 1;
 END LOOP;
 RETURN n;
END $body$;

-- Which person/practitioner a portal actor may book for through this ACTIVE link.
CREATE FUNCTION spps_identity_dev.portal_booking_context(p_token_hash text, p_link_id integer)
 RETURNS TABLE(practitioner_id integer, tenant_id integer, person_id integer, authority_id integer, actor integer)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_link practitioner_athlete_links%ROWTYPE;
        v_allowed boolean; v_authority integer;
BEGIN
 SELECT * INTO v_link FROM practitioner_athlete_links WHERE id=p_link_id;
 IF NOT FOUND OR NOT link_is_active(p_link_id) THEN RAISE EXCEPTION 'link not found' USING ERRCODE='P0002'; END IF;
 SELECT c.allowed, c.authority_id INTO v_allowed, v_authority FROM acting_authority(v_actor, v_link.person_id, 'booking_management') c;
 IF v_allowed IS NOT TRUE THEN RAISE EXCEPTION 'link not found' USING ERRCODE='P0002'; END IF;
 RETURN QUERY SELECT v_link.practitioner_id, v_link.tenant_id, v_link.person_id, v_authority, v_actor;
END $body$;

-- May the actor act (booking_management) or view (appointment_metadata) for this booking's person?
CREATE FUNCTION spps_identity_dev.booking_actor_allowed(p_actor integer, p_person integer, p_manage boolean)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT EXISTS (SELECT 1 FROM acting_authority(p_actor, p_person, 'booking_management') x WHERE x.allowed)
     OR (NOT p_manage AND EXISTS (SELECT 1 FROM acting_authority(p_actor, p_person, 'appointment_metadata') x WHERE x.allowed))
$body$;

-- Slots currently held by platform bookings (used to hide claimed clinical slots).
CREATE FUNCTION spps_identity_dev.practitioner_held_slots(p_practitioner_id integer)
 RETURNS TABLE(slot_id integer, status text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY SELECT b.slot_id, b.status::text FROM platform_bookings b WHERE b.practitioner_id=p_practitioner_id
  AND b.status IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED','RESCHEDULE_PROPOSED','COMPLETED');
END $body$;

-- ---------------------------------------------------------------- portal side
CREATE FUNCTION spps_identity_dev.portal_request_booking(
 p_token_hash text, p_link_id integer, p_slot_id integer, p_starts_at timestamptz, p_duration_minutes integer,
 p_amount_paise integer, p_video boolean, p_note text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE c record; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO c FROM portal_booking_context(p_token_hash, p_link_id);
 IF p_slot_id<=0 OR p_duration_minutes NOT BETWEEN 20 AND 120 OR p_amount_paise NOT BETWEEN 10000 AND 10000000
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

CREATE FUNCTION spps_identity_dev.portal_list_bookings(p_token_hash text)
 RETURNS TABLE(booking_id integer, practitioner_id integer, person_id integer, subject_display_name text, slot_id integer,
               starts_at timestamptz, ends_at timestamptz, amount_paise integer, currency text, status text,
               video_requested boolean, action_deadline timestamptz, decision_reason text, rescheduled_from_id integer,
               can_manage boolean, paid boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY
 SELECT b.id, b.practitioner_id, b.person_id, COALESCE(NULLIF(p.display_name,''), a.display_name)::text, b.slot_id,
        b.starts_at, b.ends_at, b.amount_paise, b.currency::text, b.status::text, b.video_requested, b.action_deadline,
        b.decision_reason::text, b.rescheduled_from_id, booking_actor_allowed(v_actor, b.person_id, true),
        EXISTS (SELECT 1 FROM platform_booking_payments x WHERE x.booking_id=b.id)
 FROM platform_bookings b JOIN platform_people p ON p.id=b.person_id
 LEFT JOIN platform_accounts a ON a.person_id=b.person_id
 WHERE booking_actor_allowed(v_actor, b.person_id, false)
 ORDER BY b.starts_at DESC, b.id DESC;
END $body$;

-- Payment step: returns the amount to charge and any existing order (idempotent retries).
CREATE FUNCTION spps_identity_dev.portal_payment_target(p_token_hash text, p_booking_id integer)
 RETURNS TABLE(booking_id integer, amount_paise integer, currency text, provider text, provider_order_id text,
               action_deadline timestamptz)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL); b platform_bookings%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id;
 IF NOT FOUND OR NOT booking_actor_allowed(v_actor, b.person_id, true) THEN
  RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002';
 END IF;
 -- Approval-first: no payment for anything that is not approved and still within its window.
 IF b.status<>'APPROVED_AWAITING_PAYMENT' THEN
  RAISE EXCEPTION 'booking is not awaiting payment' USING ERRCODE='P0004';
 END IF;
 IF NOT link_is_active(b.link_id) THEN RAISE EXCEPTION 'link inactive' USING ERRCODE='P0002'; END IF;
 RETURN QUERY SELECT b.id, b.amount_paise, b.currency::text, b.provider::text, b.provider_order_id::text, b.action_deadline;
END $body$;

-- Server-side after the provider created an order. First writer wins; retries return the stored order.
CREATE FUNCTION spps_identity_dev.booking_attach_order(p_booking_id integer, p_provider text, p_order_id text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id FOR UPDATE;
 IF NOT FOUND OR b.status<>'APPROVED_AWAITING_PAYMENT' THEN
  RAISE EXCEPTION 'booking is not awaiting payment' USING ERRCODE='P0004';
 END IF;
 IF p_provider NOT IN ('sandbox','razorpay') OR length(coalesce(p_order_id,'')) NOT BETWEEN 6 AND 100 THEN
  RAISE EXCEPTION 'invalid order' USING ERRCODE='22023';
 END IF;
 IF b.provider_order_id IS NOT NULL THEN RETURN b.provider_order_id; END IF;
 UPDATE platform_bookings SET provider=p_provider, provider_order_id=p_order_id, updated_at=now() WHERE id=p_booking_id;
 RETURN p_order_id;
END $body$;

-- Provider-VERIFIED capture (signature/readback checked by the API before calling).
-- Returns CONFIRMED, ALREADY_RECORDED or REFUND_REQUIRED (paid after expiry/cancellation).
CREATE FUNCTION spps_identity_dev.booking_record_capture(
 p_provider text, p_order_id text, p_payment_id text, p_amount_paise integer)
 RETURNS TABLE(booking_id integer, outcome text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE b platform_bookings%ROWTYPE; v_existing platform_booking_payments%ROWTYPE; v_platform integer; v_outcome text;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO v_existing FROM platform_booking_payments WHERE provider_payment_id=p_payment_id;
 IF FOUND THEN
  IF v_existing.provider_order_id<>p_order_id THEN RAISE EXCEPTION 'payment/order mismatch' USING ERRCODE='P0005'; END IF;
  RETURN QUERY SELECT v_existing.booking_id, 'ALREADY_RECORDED'::text; RETURN;
 END IF;
 SELECT * INTO b FROM platform_bookings WHERE provider_order_id=p_order_id AND provider=p_provider FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'order not found' USING ERRCODE='P0002'; END IF;
 IF p_amount_paise<>b.amount_paise THEN RAISE EXCEPTION 'amount mismatch' USING ERRCODE='P0005'; END IF;
 v_platform := (b.amount_paise*b.commission_bps+5000)/10000;
 IF b.status='APPROVED_AWAITING_PAYMENT' THEN
  INSERT INTO platform_booking_payments(booking_id,provider,provider_order_id,provider_payment_id,amount_paise,
    practitioner_paise,platform_paise,status,captured_at)
  VALUES (b.id,p_provider,p_order_id,p_payment_id,b.amount_paise,b.amount_paise-v_platform,v_platform,'CAPTURED',now());
  UPDATE platform_bookings SET status='CONFIRMED', action_deadline=NULL, updated_at=now() WHERE id=b.id;
  PERFORM notify_account(b.requested_by_account_id,'booking.confirmed','booking',b.id);
  PERFORM notify_practitioner(b.practitioner_id,'booking.confirmed','booking',b.id);
  v_outcome := 'CONFIRMED';
 ELSE
  -- Money arrived for a booking that can no longer be confirmed: never confirm, refund it.
  INSERT INTO platform_booking_payments(booking_id,provider,provider_order_id,provider_payment_id,amount_paise,
    practitioner_paise,platform_paise,status,captured_at)
  VALUES (b.id,p_provider,p_order_id,p_payment_id,b.amount_paise,b.amount_paise-v_platform,v_platform,'REFUND_PENDING',now());
  IF b.status IN ('EXPIRED','CANCELLED','DECLINED') THEN
   UPDATE platform_bookings SET status='REFUND_PENDING', updated_at=now() WHERE id=b.id;
  END IF;
  PERFORM notify_account(b.requested_by_account_id,'booking.payment_refund_required','booking',b.id);
  v_outcome := 'REFUND_REQUIRED';
 END IF;
 PERFORM audit(NULL,'booking.capture.'||lower(v_outcome),'booking',b.id,p_provider);
 RETURN QUERY SELECT b.id, v_outcome;
END $body$;

CREATE FUNCTION spps_identity_dev.booking_refunds_due()
 RETURNS TABLE(payment_id integer, booking_id integer, provider text, provider_payment_id text, amount_paise integer)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT id, booking_id, provider::text, provider_payment_id::text, amount_paise FROM platform_booking_payments
 WHERE status='REFUND_PENDING' ORDER BY id
$body$;

CREATE FUNCTION spps_identity_dev.booking_mark_refunded(p_provider_payment_id text, p_refund_id text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v platform_booking_payments%ROWTYPE;
BEGIN
 SELECT * INTO v FROM platform_booking_payments WHERE provider_payment_id=p_provider_payment_id FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF v.status='REFUNDED' THEN RETURN v.refund_id=p_refund_id; END IF;
 IF v.status<>'REFUND_PENDING' THEN RETURN false; END IF;
 UPDATE platform_booking_payments SET status='REFUNDED', refund_id=p_refund_id, refunded_at=now() WHERE id=v.id;
 UPDATE platform_bookings SET status='REFUNDED', updated_at=now() WHERE id=v.booking_id AND status='REFUND_PENDING';
 PERFORM notify_account((SELECT requested_by_account_id FROM platform_bookings WHERE id=v.booking_id),
                        'booking.refunded','booking',v.booking_id);
 PERFORM audit(NULL,'booking.refunded','booking',v.booking_id,'');
 RETURN true;
END $body$;

-- Cancel a booking's money side: CONFIRMED -> REFUND_PENDING (payment REFUND_PENDING).
CREATE FUNCTION spps_identity_dev.booking_cancel_internal(p_booking_id integer, p_reason text, p_actor integer)
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

CREATE FUNCTION spps_identity_dev.portal_cancel_booking(p_token_hash text, p_booking_id integer, p_reason text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); b platform_bookings%ROWTYPE; v text;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id;
 IF NOT FOUND OR NOT booking_actor_allowed(v_actor, b.person_id, true) THEN
  RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002';
 END IF;
 IF b.status='RESCHEDULE_PROPOSED' THEN RAISE EXCEPTION 'respond to the proposal instead' USING ERRCODE='P0004'; END IF;
 v := booking_cancel_internal(p_booking_id, p_reason, v_actor);
 PERFORM notify_practitioner(b.practitioner_id,'booking.cancelled_by_client','booking',b.id);
 PERFORM audit(v_actor,'booking.cancelled_by_client','booking',b.id,v);
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.portal_respond_reschedule(p_token_hash text, p_proposal_id integer, p_accept boolean)
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
 v_new := CASE WHEN o.status='CONFIRMED' THEN 'CONFIRMED' ELSE 'APPROVED_AWAITING_PAYMENT' END;
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

-- ---------------------------------------------------------------- practitioner side
CREATE FUNCTION spps_identity_dev.practitioner_list_bookings(p_practitioner_id integer)
 RETURNS TABLE(booking_id integer, slot_id integer, starts_at timestamptz, ends_at timestamptz, amount_paise integer,
               status text, subject_display_name text, via_guardian boolean, video_requested boolean, client_note text,
               action_deadline timestamptz, rescheduled_from_id integer, paid boolean, payment_status text,
               practitioner_paise integer, platform_paise integer)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY
 SELECT b.id, b.slot_id, b.starts_at, b.ends_at, b.amount_paise, b.status::text,
        COALESCE(NULLIF(p.display_name,''), a.display_name)::text, b.guardian_authority_id IS NOT NULL,
        b.video_requested, b.client_note::text, b.action_deadline, b.rescheduled_from_id,
        pay.id IS NOT NULL, pay.status::text, pay.practitioner_paise, pay.platform_paise
 FROM platform_bookings b JOIN platform_people p ON p.id=b.person_id
 LEFT JOIN platform_accounts a ON a.person_id=b.person_id
 LEFT JOIN LATERAL (SELECT * FROM platform_booking_payments x WHERE x.booking_id=b.id ORDER BY x.id DESC LIMIT 1) pay ON true
 WHERE b.practitioner_id=p_practitioner_id
 ORDER BY b.starts_at DESC, b.id DESC;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_decide_booking(
 p_practitioner_id integer, p_booking_id integer, p_decision text, p_reason text, p_payment_window_hours integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF p_decision='APPROVE' THEN
  IF b.status<>'PENDING_APPROVAL' THEN RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004'; END IF;
  IF NOT link_is_active(b.link_id) THEN RAISE EXCEPTION 'portal link is no longer active' USING ERRCODE='P0004'; END IF;
  IF p_payment_window_hours NOT BETWEEN 1 AND 72 THEN RAISE EXCEPTION 'invalid payment window' USING ERRCODE='22023'; END IF;
  UPDATE platform_bookings SET status='APPROVED_AWAITING_PAYMENT', decision_reason=nullif(left(btrim(coalesce(p_reason,'')),240),''),
    action_deadline=least(now()+make_interval(hours=>p_payment_window_hours), b.starts_at-interval '1 hour'), updated_at=now()
   WHERE id=b.id;
  PERFORM notify_account(b.requested_by_account_id,'booking.approved_payment_due','booking',b.id);
  PERFORM audit(NULL,'booking.approved','booking',b.id,'practitioner '||p_practitioner_id);
  RETURN 'APPROVED_AWAITING_PAYMENT';
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

CREATE FUNCTION spps_identity_dev.practitioner_propose_reschedule(
 p_practitioner_id integer, p_booking_id integer, p_slot_id integer, p_starts_at timestamptz,
 p_duration_minutes integer, p_amount_paise integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF b.status NOT IN ('PENDING_APPROVAL','APPROVED_AWAITING_PAYMENT','CONFIRMED') THEN
  RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004';
 END IF;
 IF p_starts_at <= now()+interval '2 hours' OR p_duration_minutes NOT BETWEEN 20 AND 120
    OR p_amount_paise NOT BETWEEN 10000 AND 10000000 OR p_slot_id=b.slot_id THEN
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

CREATE FUNCTION spps_identity_dev.practitioner_cancel_booking(p_practitioner_id integer, p_booking_id integer, p_reason text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE; v text;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF length(btrim(coalesce(p_reason,''))) < 5 THEN RAISE EXCEPTION 'reason required' USING ERRCODE='22023'; END IF;
 v := booking_cancel_internal(p_booking_id, p_reason, NULL);
 PERFORM notify_account(b.requested_by_account_id,'booking.cancelled_by_practitioner','booking',b.id);
 PERFORM audit(NULL,'booking.cancelled_by_practitioner','booking',b.id,v);
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_complete_booking(p_practitioner_id integer, p_booking_id integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE;
BEGIN
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF b.status<>'CONFIRMED' OR b.starts_at > now()+interval '15 minutes' THEN
  RAISE EXCEPTION 'only a confirmed appointment that has started can be completed' USING ERRCODE='P0004';
 END IF;
 UPDATE platform_bookings SET status='COMPLETED', updated_at=now() WHERE id=b.id;
 PERFORM audit(NULL,'booking.completed','booking',b.id,'');
 RETURN 'COMPLETED';
END $body$;

-- ---------------------------------------------------------------- notifications
CREATE FUNCTION spps_identity_dev.portal_notifications(p_token_hash text, p_mark_read boolean)
 RETURNS TABLE(notification_id integer, kind text, object_type text, object_id integer, created_at timestamptz, read boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY SELECT n.id, n.kind::text, n.object_type::text, n.object_id, n.created_at, n.read_at IS NOT NULL
  FROM platform_notifications n WHERE n.account_id=v_actor ORDER BY n.id DESC LIMIT 50;
 IF p_mark_read THEN UPDATE platform_notifications SET read_at=now() WHERE account_id=v_actor AND read_at IS NULL; END IF;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_notifications(p_practitioner_id integer, p_mark_read boolean)
 RETURNS TABLE(notification_id integer, kind text, object_type text, object_id integer, created_at timestamptz, read boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 PERFORM booking_expire_due();
 RETURN QUERY SELECT n.id, n.kind::text, n.object_type::text, n.object_id, n.created_at, n.read_at IS NOT NULL
  FROM platform_notifications n WHERE n.practitioner_id=p_practitioner_id ORDER BY n.id DESC LIMIT 50;
 IF p_mark_read THEN UPDATE platform_notifications SET read_at=now() WHERE practitioner_id=p_practitioner_id AND read_at IS NULL; END IF;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN (
           'notify_account','notify_practitioner','booking_expire_due','portal_booking_context','booking_actor_allowed',
           'practitioner_held_slots','portal_request_booking','portal_list_bookings','portal_payment_target',
           'booking_attach_order','booking_record_capture','booking_refunds_due','booking_mark_refunded',
           'booking_cancel_internal','portal_cancel_booking','portal_respond_reschedule','practitioner_list_bookings',
           'practitioner_decide_booking','practitioner_propose_reschedule','practitioner_cancel_booking',
           'practitioner_complete_booking','portal_notifications','practitioner_notifications') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  IF f.proname NOT IN ('notify_account','notify_practitioner','booking_actor_allowed','booking_cancel_internal') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
