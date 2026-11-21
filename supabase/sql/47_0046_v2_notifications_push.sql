-- SPPS production database, step 47 of the serial set: 0046_v2_notifications_push
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0046_v2_notifications_push.sql
BEGIN;
-- 0046 (notifications, phase 4): devices that can receive push messages, per-person notification preferences, the
-- first-sign-in prompt state, and the reminder ledger. EXPAND-ONLY: new tables and functions, nothing existing changes.
--
-- Recipients are either a platform account (athlete or guardian, identified by a session token hash) or a practitioner
-- (identified by the already-authenticated practitioner id, exactly like the existing practitioner_* functions).
-- The owner and admins are practitioner rows, so they use the practitioner functions.
-- The application roles have NO table privileges here: every read and write goes through SECURITY DEFINER functions
-- that act only on the caller's own rows. A device subscription (endpoint and keys, or an FCM token) is stored but is
-- never returned by any function the application calls for a user; only the delivery worker reads it.
-- Payloads are content-free; see server/notifications.py.

SET LOCAL ROLE spps_identity_owner;

CREATE TABLE spps_identity_dev.notification_devices (
    id            serial PRIMARY KEY,
    account_id    integer REFERENCES spps_identity_dev.platform_accounts(id) ON DELETE CASCADE,
    practitioner_id integer,
    platform      text NOT NULL CHECK (platform IN ('web', 'android')),
    endpoint_hash text NOT NULL CHECK (char_length(endpoint_hash) = 64),
    subscription  text NOT NULL CHECK (char_length(subscription) BETWEEN 10 AND 4000),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz,
    CONSTRAINT ck_notification_device_owner CHECK ((account_id IS NULL) <> (practitioner_id IS NULL))
);
-- One row per physical device: signing in as someone else on the same browser moves the row, so a device never
-- receives two people's messages.
CREATE UNIQUE INDEX ux_notification_devices_endpoint ON spps_identity_dev.notification_devices(platform, endpoint_hash);
CREATE INDEX ix_notification_devices_account ON spps_identity_dev.notification_devices(account_id);
CREATE INDEX ix_notification_devices_practitioner ON spps_identity_dev.notification_devices(practitioner_id);

CREATE TABLE spps_identity_dev.notification_preferences (
    id              serial PRIMARY KEY,
    account_id      integer REFERENCES spps_identity_dev.platform_accounts(id) ON DELETE CASCADE,
    practitioner_id integer,
    category        text NOT NULL CHECK (category IN ('appointments', 'programme_tasks', 'account_security')),
    enabled         boolean NOT NULL DEFAULT true,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_notification_pref_owner CHECK ((account_id IS NULL) <> (practitioner_id IS NULL)),
    -- Account and security messages cannot be switched off.
    CONSTRAINT ck_notification_pref_security CHECK (category <> 'account_security' OR enabled)
);
CREATE UNIQUE INDEX ux_notification_prefs_account ON spps_identity_dev.notification_preferences(account_id, category);
CREATE UNIQUE INDEX ux_notification_prefs_practitioner ON spps_identity_dev.notification_preferences(practitioner_id, category);

CREATE TABLE spps_identity_dev.notification_prompts (
    id              serial PRIMARY KEY,
    account_id      integer REFERENCES spps_identity_dev.platform_accounts(id) ON DELETE CASCADE,
    practitioner_id integer,
    state           text NOT NULL CHECK (state IN ('ON', 'NOT_NOW')),
    answered_at     timestamptz NOT NULL DEFAULT now(),
    next_prompt_at  timestamptz,
    CONSTRAINT ck_notification_prompt_owner CHECK ((account_id IS NULL) <> (practitioner_id IS NULL))
);
CREATE UNIQUE INDEX ux_notification_prompts_account ON spps_identity_dev.notification_prompts(account_id);
CREATE UNIQUE INDEX ux_notification_prompts_practitioner ON spps_identity_dev.notification_prompts(practitioner_id);

CREATE TABLE spps_identity_dev.notification_reminders (
    booking_id integer PRIMARY KEY REFERENCES spps_identity_dev.platform_bookings(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['notification_devices', 'notification_preferences', 'notification_prompts',
                           'notification_reminders'] LOOP
    EXECUTE format('REVOKE ALL ON spps_identity_dev.%I FROM PUBLIC', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY identity_owner_read ON spps_identity_dev.%I FOR SELECT TO spps_identity_owner USING (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_insert ON spps_identity_dev.%I FOR INSERT TO spps_identity_owner WITH CHECK (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_update ON spps_identity_dev.%I FOR UPDATE TO spps_identity_owner USING (true) WITH CHECK (true)', t);
    EXECUTE format('CREATE POLICY identity_owner_delete ON spps_identity_dev.%I FOR DELETE TO spps_identity_owner USING (true)', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- category of a notification kind
CREATE FUNCTION spps_identity_dev.notification_category(p_kind text) RETURNS text
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
 SELECT CASE WHEN p_kind LIKE 'booking.%' THEN 'appointments'
             WHEN p_kind LIKE 'programme.%' OR p_kind = 'summary.released' THEN 'programme_tasks'
             ELSE 'account_security' END
$body$;

-- ---------------------------------------------------------------- shared internals (not granted to anyone)
CREATE FUNCTION spps_identity_dev.notif_register(p_account integer, p_practitioner integer, p_platform text,
                                                 p_endpoint_hash text, p_subscription text) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_id integer;
BEGIN
 IF (p_account IS NULL) = (p_practitioner IS NULL) OR p_platform NOT IN ('web','android')
    OR p_endpoint_hash !~ '^[0-9a-f]{64}$' OR char_length(coalesce(p_subscription,'')) NOT BETWEEN 10 AND 4000 THEN
  RAISE EXCEPTION 'invalid device' USING ERRCODE='22023';
 END IF;
 INSERT INTO notification_devices(account_id, practitioner_id, platform, endpoint_hash, subscription)
 VALUES (p_account, p_practitioner, p_platform, p_endpoint_hash, p_subscription)
 ON CONFLICT (platform, endpoint_hash) DO UPDATE
   SET account_id=EXCLUDED.account_id, practitioner_id=EXCLUDED.practitioner_id,
       subscription=EXCLUDED.subscription, revoked_at=NULL, last_seen_at=now()
 RETURNING id INTO v_id;
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.notif_devices(p_account integer, p_practitioner integer)
 RETURNS TABLE(device_id integer, platform text, created_at timestamptz, last_seen_at timestamptz)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT d.id, d.platform, d.created_at, d.last_seen_at FROM notification_devices d
  WHERE d.revoked_at IS NULL
    AND ((p_account IS NOT NULL AND d.account_id = p_account)
      OR (p_practitioner IS NOT NULL AND d.practitioner_id = p_practitioner))
  ORDER BY d.id
$body$;

-- Revokes one of the caller's own devices, by id (Settings) or by endpoint hash (sign-out). Returns how many.
CREATE FUNCTION spps_identity_dev.notif_revoke(p_account integer, p_practitioner integer, p_device_id integer,
                                               p_endpoint_hash text) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 UPDATE notification_devices d SET revoked_at = now()
  WHERE d.revoked_at IS NULL
    AND ((p_account IS NOT NULL AND d.account_id = p_account)
      OR (p_practitioner IS NOT NULL AND d.practitioner_id = p_practitioner))
    AND ((p_device_id IS NOT NULL AND d.id = p_device_id)
      OR (p_endpoint_hash IS NOT NULL AND d.endpoint_hash = p_endpoint_hash));
 GET DIAGNOSTICS n = ROW_COUNT;
 RETURN n;
END $body$;

CREATE FUNCTION spps_identity_dev.notif_prefs(p_account integer, p_practitioner integer)
 RETURNS TABLE(category text, enabled boolean)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT c.category, coalesce(p.enabled, true)
   FROM (VALUES ('appointments'), ('programme_tasks'), ('account_security')) AS c(category)
   LEFT JOIN notification_preferences p ON p.category = c.category
        AND ((p_account IS NOT NULL AND p.account_id = p_account)
          OR (p_practitioner IS NOT NULL AND p.practitioner_id = p_practitioner))
  ORDER BY c.category
$body$;

CREATE FUNCTION spps_identity_dev.notif_pref_set(p_account integer, p_practitioner integer, p_category text,
                                                 p_enabled boolean) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
BEGIN
 IF (p_account IS NULL) = (p_practitioner IS NULL) OR p_category NOT IN ('appointments','programme_tasks','account_security')
    OR p_enabled IS NULL THEN
  RAISE EXCEPTION 'invalid preference' USING ERRCODE='22023';
 END IF;
 IF p_category = 'account_security' AND NOT p_enabled THEN
  RAISE EXCEPTION 'account and security messages cannot be switched off' USING ERRCODE='22023';
 END IF;
 IF p_account IS NOT NULL THEN
  INSERT INTO notification_preferences(account_id, category, enabled) VALUES (p_account, p_category, p_enabled)
   ON CONFLICT (account_id, category) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now();
 ELSE
  INSERT INTO notification_preferences(practitioner_id, category, enabled) VALUES (p_practitioner, p_category, p_enabled)
   ON CONFLICT (practitioner_id, category) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now();
 END IF;
 RETURN p_enabled;
END $body$;

-- should_prompt: show the first-sign-in pop-up. It is shown when the person has no active device and has either never
-- answered or answered "Not now" at least seven days ago. Someone who turned notifications on is not asked again.
CREATE FUNCTION spps_identity_dev.notif_prompt_state(p_account integer, p_practitioner integer)
 RETURNS TABLE(should_prompt boolean, state text, next_prompt_at timestamptz, has_device boolean)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_state text; v_next timestamptz; v_dev boolean;
BEGIN
 SELECT p.state, p.next_prompt_at INTO v_state, v_next FROM notification_prompts p
  WHERE (p_account IS NOT NULL AND p.account_id = p_account)
     OR (p_practitioner IS NOT NULL AND p.practitioner_id = p_practitioner);
 SELECT EXISTS (SELECT 1 FROM notification_devices d WHERE d.revoked_at IS NULL
                 AND ((p_account IS NOT NULL AND d.account_id = p_account)
                   OR (p_practitioner IS NOT NULL AND d.practitioner_id = p_practitioner))) INTO v_dev;
 RETURN QUERY SELECT (NOT v_dev) AND (v_state IS NULL OR (v_state = 'NOT_NOW' AND v_next <= now())),
                     v_state, v_next, v_dev;
END $body$;

CREATE FUNCTION spps_identity_dev.notif_prompt_answer(p_account integer, p_practitioner integer, p_answer text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_state text; v_next timestamptz;
BEGIN
 IF (p_account IS NULL) = (p_practitioner IS NULL) OR p_answer NOT IN ('ON','NOT_NOW') THEN
  RAISE EXCEPTION 'invalid answer' USING ERRCODE='22023';
 END IF;
 v_state := p_answer;
 v_next := CASE WHEN p_answer = 'NOT_NOW' THEN now() + interval '7 days' ELSE NULL END;
 IF p_account IS NOT NULL THEN
  INSERT INTO notification_prompts(account_id, state, next_prompt_at) VALUES (p_account, v_state, v_next)
   ON CONFLICT (account_id) DO UPDATE SET state = EXCLUDED.state, next_prompt_at = EXCLUDED.next_prompt_at, answered_at = now();
 ELSE
  INSERT INTO notification_prompts(practitioner_id, state, next_prompt_at) VALUES (p_practitioner, v_state, v_next)
   ON CONFLICT (practitioner_id) DO UPDATE SET state = EXCLUDED.state, next_prompt_at = EXCLUDED.next_prompt_at, answered_at = now();
 END IF;
 RETURN v_state;
END $body$;

-- ---------------------------------------------------------------- callable wrappers: athletes and guardians
CREATE FUNCTION spps_identity_dev.portal_push_register(p_token_hash text, p_platform text, p_endpoint_hash text,
                                                       p_subscription text) RETURNS integer
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_register(require_actor(p_token_hash, NULL), NULL, p_platform, p_endpoint_hash, p_subscription)
$body$;
CREATE FUNCTION spps_identity_dev.portal_push_devices(p_token_hash text)
 RETURNS TABLE(device_id integer, platform text, created_at timestamptz, last_seen_at timestamptz)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_devices(require_actor(p_token_hash, NULL), NULL)
$body$;
CREATE FUNCTION spps_identity_dev.portal_push_revoke(p_token_hash text, p_device_id integer, p_endpoint_hash text)
 RETURNS integer LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_revoke(require_actor(p_token_hash, NULL), NULL, p_device_id, p_endpoint_hash)
$body$;
CREATE FUNCTION spps_identity_dev.portal_notification_prefs(p_token_hash text)
 RETURNS TABLE(category text, enabled boolean)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_prefs(require_actor(p_token_hash, NULL), NULL)
$body$;
CREATE FUNCTION spps_identity_dev.portal_notification_set(p_token_hash text, p_category text, p_enabled boolean)
 RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_pref_set(require_actor(p_token_hash, NULL), NULL, p_category, p_enabled)
$body$;
CREATE FUNCTION spps_identity_dev.portal_notification_prompt(p_token_hash text)
 RETURNS TABLE(should_prompt boolean, state text, next_prompt_at timestamptz, has_device boolean)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_prompt_state(require_actor(p_token_hash, NULL), NULL)
$body$;
CREATE FUNCTION spps_identity_dev.portal_notification_answer(p_token_hash text, p_answer text)
 RETURNS text LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_prompt_answer(require_actor(p_token_hash, NULL), NULL, p_answer)
$body$;

-- ---------------------------------------------------------------- callable wrappers: practitioners, owner, admins
CREATE FUNCTION spps_identity_dev.practitioner_push_register(p_practitioner_id integer, p_platform text,
                                                             p_endpoint_hash text, p_subscription text) RETURNS integer
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_register(NULL, p_practitioner_id, p_platform, p_endpoint_hash, p_subscription)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_push_devices(p_practitioner_id integer)
 RETURNS TABLE(device_id integer, platform text, created_at timestamptz, last_seen_at timestamptz)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_devices(NULL, p_practitioner_id)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_push_revoke(p_practitioner_id integer, p_device_id integer,
                                                           p_endpoint_hash text) RETURNS integer
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_revoke(NULL, p_practitioner_id, p_device_id, p_endpoint_hash)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_notification_prefs(p_practitioner_id integer)
 RETURNS TABLE(category text, enabled boolean)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_prefs(NULL, p_practitioner_id)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_notification_set(p_practitioner_id integer, p_category text,
                                                                p_enabled boolean) RETURNS boolean
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_pref_set(NULL, p_practitioner_id, p_category, p_enabled)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_notification_prompt(p_practitioner_id integer)
 RETURNS TABLE(should_prompt boolean, state text, next_prompt_at timestamptz, has_device boolean)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT * FROM notif_prompt_state(NULL, p_practitioner_id)
$body$;
CREATE FUNCTION spps_identity_dev.practitioner_notification_answer(p_practitioner_id integer, p_answer text)
 RETURNS text LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT notif_prompt_answer(NULL, p_practitioner_id, p_answer)
$body$;

-- ---------------------------------------------------------------- delivery worker
-- Active devices for one queued notification, after the recipient's preference for its category. Only the worker role
-- may call this; it is the only function that returns a subscription.
CREATE FUNCTION spps_identity_dev.notification_push_targets(p_notification_id integer)
 RETURNS TABLE(device_id integer, platform text, subscription text)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT d.id, d.platform, d.subscription
   FROM platform_notifications n
   JOIN notification_devices d ON d.revoked_at IS NULL
        AND ((n.account_id IS NOT NULL AND d.account_id = n.account_id)
          OR (n.practitioner_id IS NOT NULL AND d.practitioner_id = n.practitioner_id))
  WHERE n.id = p_notification_id
    AND NOT EXISTS (SELECT 1 FROM notification_preferences p
                     WHERE p.category = notification_category(n.kind) AND NOT p.enabled
                       AND ((n.account_id IS NOT NULL AND p.account_id = n.account_id)
                         OR (n.practitioner_id IS NOT NULL AND p.practitioner_id = n.practitioner_id)))
  ORDER BY d.id
$body$;

-- The provider said the token is gone (404/410, UNREGISTERED): never try it again.
CREATE FUNCTION spps_identity_dev.notification_device_dead(p_device_id integer) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 UPDATE notification_devices SET revoked_at = now() WHERE id = p_device_id AND revoked_at IS NULL
$body$;

-- Queues one reminder per confirmed appointment that starts within the next p_ahead_minutes. The ledger row is the
-- idempotency key, so a worker restart or a second worker never sends a second reminder.
CREATE FUNCTION spps_identity_dev.notification_enqueue_due_reminders(p_ahead_minutes integer) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE r record; n integer := 0;
BEGIN
 IF p_ahead_minutes NOT BETWEEN 5 AND 1440 THEN RAISE EXCEPTION 'invalid reminder window' USING ERRCODE='22023'; END IF;
 FOR r IN SELECT b.id, b.practitioner_id, b.requested_by_account_id FROM platform_bookings b
           WHERE b.status = 'CONFIRMED' AND b.starts_at > now()
             AND b.starts_at <= now() + make_interval(mins => p_ahead_minutes)
             AND NOT EXISTS (SELECT 1 FROM notification_reminders x WHERE x.booking_id = b.id)
           FOR UPDATE OF b SKIP LOCKED LOOP
  INSERT INTO notification_reminders(booking_id) VALUES (r.id) ON CONFLICT DO NOTHING;
  IF FOUND THEN
   PERFORM notify_practitioner(r.practitioner_id, 'booking.reminder', 'booking', r.id);
   PERFORM notify_account(r.requested_by_account_id, 'booking.reminder', 'booking', r.id);
   n := n + 1;
  END IF;
 END LOOP;
 RETURN n;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_identity_dev'
    AND p.proname IN ('notification_category','notif_register','notif_devices','notif_revoke','notif_prefs',
                      'notif_pref_set','notif_prompt_state','notif_prompt_answer',
                      'portal_push_register','portal_push_devices','portal_push_revoke','portal_notification_prefs',
                      'portal_notification_set','portal_notification_prompt','portal_notification_answer',
                      'practitioner_push_register','practitioner_push_devices','practitioner_push_revoke',
                      'practitioner_notification_prefs','practitioner_notification_set',
                      'practitioner_notification_prompt','practitioner_notification_answer',
                      'notification_push_targets','notification_device_dead','notification_enqueue_due_reminders') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  -- The shared internals (notif_*) stay callable only by the definer functions above.
  IF f.sig::text NOT LIKE 'spps_identity_dev.notif\_%' THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0046_v2_notifications_push.sql','e956f0d885c6fc3b6644c59e696b4eef8f9c55084dce9367ec7a52014d661202') ON CONFLICT (name) DO NOTHING;
COMMIT;
