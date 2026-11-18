-- SPPS production database, step 15 of the serial set: 0013_notification_delivery
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0013_notification_delivery.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
-- P1: durable notification delivery (outbox worker). Payloads stay identifier-only;
-- the worker renders generic, content-free messages ("you have an update; sign in").
-- Claims use FOR UPDATE SKIP LOCKED so several workers never double-send.
-- Retry with exponential backoff; FAILED after 6 attempts (dead letter, alertable).

CREATE INDEX ix_notifications_due ON spps_identity_dev.platform_notifications (delivery_status, next_attempt_at);

CREATE FUNCTION spps_identity_dev.notification_claim(p_limit integer, p_lease_seconds integer)
 RETURNS TABLE(notification_id integer, kind text, object_type text, object_id integer,
               account_email text, practitioner_id integer, attempts integer)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
BEGIN
 IF p_limit NOT BETWEEN 1 AND 500 OR p_lease_seconds NOT BETWEEN 10 AND 3600 THEN
  RAISE EXCEPTION 'invalid claim' USING ERRCODE='22023';
 END IF;
 -- A crashed worker's SENDING lease expires and the message becomes due again.
 RETURN QUERY
 WITH due AS (
  SELECT n.id FROM platform_notifications n
  WHERE (n.delivery_status='PENDING' AND (n.next_attempt_at IS NULL OR n.next_attempt_at<=now()))
     OR (n.delivery_status='SENDING' AND n.next_attempt_at<=now())
  ORDER BY n.id LIMIT p_limit FOR UPDATE SKIP LOCKED
 ), claimed AS (
  UPDATE platform_notifications n SET delivery_status='SENDING', delivery_attempts=n.delivery_attempts+1,
    next_attempt_at=now()+make_interval(secs=>p_lease_seconds)
  FROM due WHERE n.id=due.id
  RETURNING n.id, n.kind, n.object_type, n.object_id, n.account_id, n.practitioner_id, n.delivery_attempts
 )
 SELECT c.id, c.kind::text, c.object_type::text, c.object_id, a.email_normalized::text, c.practitioner_id, c.delivery_attempts
 FROM claimed c LEFT JOIN platform_accounts a ON a.id=c.account_id AND a.status<>'DISABLED';
 -- Every claimed row is returned; a missing recipient is resolved by the worker (retry -> FAILED).
END $body$;

CREATE FUNCTION spps_identity_dev.notification_result(p_id integer, p_delivered boolean)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n platform_notifications%ROWTYPE; v text;
BEGIN
 SELECT * INTO n FROM platform_notifications WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR n.delivery_status<>'SENDING' THEN RETURN coalesce(n.delivery_status,'MISSING'); END IF;
 IF p_delivered THEN
  UPDATE platform_notifications SET delivery_status='DELIVERED', delivered_at=now(), next_attempt_at=NULL WHERE id=p_id;
  v := 'DELIVERED';
 ELSIF n.delivery_attempts>=6 THEN
  UPDATE platform_notifications SET delivery_status='FAILED', next_attempt_at=NULL WHERE id=p_id;
  v := 'FAILED';
 ELSE
  UPDATE platform_notifications SET delivery_status='PENDING',
    next_attempt_at=now()+make_interval(secs=>least(3600, 30*power(2, n.delivery_attempts)::integer)) WHERE id=p_id;
  v := 'PENDING';
 END IF;
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.notification_health()
 RETURNS TABLE(pending integer, sending integer, failed integer, oldest_pending_seconds integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT count(*) FILTER (WHERE delivery_status='PENDING')::integer,
        count(*) FILTER (WHERE delivery_status='SENDING')::integer,
        count(*) FILTER (WHERE delivery_status='FAILED')::integer,
        COALESCE(extract(epoch FROM now()-min(created_at) FILTER (WHERE delivery_status='PENDING'))::integer, 0)
 FROM platform_notifications
$body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN ('notification_claim','notification_result','notification_health') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0013_notification_delivery.sql','251448ce275906da9c91f94708f98a51721a9a76629b5782482e7998cea9ac9b') ON CONFLICT (name) DO NOTHING;
COMMIT;
