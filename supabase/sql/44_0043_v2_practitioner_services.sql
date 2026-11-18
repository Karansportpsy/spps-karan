-- SPPS production database, step 44 of the serial set: 0043_v2_practitioner_services
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0043_v2_practitioner_services.sql
BEGIN;
-- 0043 (v2 booking-only directory, phase 2): practitioner service catalogue and appointment requests from the directory.
--
-- Expand-only and forward-only: adds two clinical tables, three nullable/defaulted identity columns, makes one identity
-- column nullable, adds two CHECKs that every existing row already satisfies, and five identity functions. Nothing is
-- dropped, and no existing function is replaced.
--
-- RUN ORDER: run this file BEFORE deploying the code that uses it (the startup check requires both new tables). The
-- release that does not know these objects keeps working after this file has run.
--
-- Clinical schema (spps_dev):
-- * practitioner_services: a practitioner's own services (name, short description, duration, mode, active, order).
--   FORCE RLS; a practitioner reads and writes only their own rows. No price column (payments are off). This is not the
--   tenant-level spps_dev.services table, which is left unchanged.
-- * directory_service_snapshot: the public read model for ACTIVE services. A practitioner writes only their own rows;
--   anyone may read a row only while that practitioner is listed in directory_snapshot (owner-published and within
--   the verification date). Public and portal handlers read this table, never practitioner_services.
--
-- Identity schema (spps_identity_dev), created and altered as spps_identity_owner (postgres is a member but not the
-- owner on Supabase):
-- * practitioner_athlete_links.athlete_record_id becomes nullable: a link requested from the directory has no clinical
--   record until the practitioner approves the first appointment. A CHECK keeps every ACTIVE link tied to a record.
-- * practitioner_athlete_links.origin: 'invitation' (all existing rows) or 'directory'.
-- * platform_bookings.service_id / service_name: the chosen service, snapshotted at request time.
-- * portal_directory_booking_context / portal_request_directory_booking: an e-mail-verified account requests an
--   appointment for itself (adult, attested at registration) or for a dependant (VERIFIED guardian authority with
--   booking_management). The minimal link is PRACTITIONER_REVIEW; consents CASE_LINK and APPOINTMENT_BOOKING (and
--   VIDEO_CONSULTATION for an online service) must be current first.
-- * practitioner_booking_details: service name and link state per booking, so the practitioner sees the minimum.
-- * practitioner_approve_directory_booking: attaches the record the server created under the practitioner's own RLS,
--   activates the link (same checks as practitioner_decide_link) and then confirms through the unchanged
--   free_practitioner_decide_booking.
-- * practitioner_decline_directory_link: closes a pending directory link once its request is declined.

-- ------------------------------------------------------------------ clinical
CREATE TABLE spps_dev.practitioner_services (
    id serial PRIMARY KEY,
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    name varchar(80) NOT NULL CHECK (length(btrim(name)) BETWEEN 3 AND 80),
    short_description varchar(280) NOT NULL DEFAULT '',
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 20 AND 120),
    mode varchar(10) NOT NULL CHECK (mode IN ('online','in_person')),
    active boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0 CHECK (sort_order BETWEEN 0 AND 999),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_practitioner_services_practitioner_id ON spps_dev.practitioner_services(practitioner_id);
CREATE INDEX ix_practitioner_services_tenant_id ON spps_dev.practitioner_services(tenant_id);

CREATE TABLE spps_dev.directory_service_snapshot (
    service_id integer PRIMARY KEY REFERENCES spps_dev.practitioner_services(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    name varchar(80) NOT NULL,
    short_description varchar(280) NOT NULL DEFAULT '',
    duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 20 AND 120),
    mode varchar(10) NOT NULL CHECK (mode IN ('online','in_person')),
    sort_order integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_directory_service_snapshot_practitioner_id ON spps_dev.directory_service_snapshot(practitioner_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['practitioner_services','directory_service_snapshot'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- No DELETE: a service is deactivated, so bookings that name it stay explainable.
GRANT SELECT, INSERT ON spps_dev.practitioner_services TO spps_dev_app;
GRANT UPDATE (name, short_description, duration_minutes, mode, active, sort_order, updated_at)
  ON spps_dev.practitioner_services TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.practitioner_services_id_seq TO spps_dev_app;
CREATE POLICY v2_practitioner_service_own ON spps_dev.practitioner_services TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON spps_dev.directory_service_snapshot TO spps_dev_app;
CREATE POLICY v2_directory_service_public_read ON spps_dev.directory_service_snapshot FOR SELECT TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM spps_dev.directory_snapshot s
                 WHERE s.practitioner_id = directory_service_snapshot.practitioner_id
                   AND s.tenant_id = directory_service_snapshot.tenant_id
                   AND s.verified_until >= to_char(now(), 'YYYY-MM-DD'))
         OR (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY v2_directory_service_own_insert ON spps_dev.directory_service_snapshot FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.practitioner_services v WHERE v.id = service_id
                AND v.practitioner_id = spps_dev.practitioner_id() AND v.active));
CREATE POLICY v2_directory_service_own_update ON spps_dev.directory_service_snapshot FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.practitioner_services v WHERE v.id = service_id
                AND v.practitioner_id = spps_dev.practitioner_id() AND v.active));
CREATE POLICY v2_directory_service_own_delete ON spps_dev.directory_service_snapshot FOR DELETE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

-- ------------------------------------------------------------------ identity
-- Identity objects belong to spps_identity_owner (postgres may SET to it but is not the owner), as in 0042.
SET LOCAL ROLE spps_identity_owner;

ALTER TABLE spps_identity_dev.practitioner_athlete_links ALTER COLUMN athlete_record_id DROP NOT NULL;
ALTER TABLE spps_identity_dev.practitioner_athlete_links
  ADD COLUMN origin varchar(12) NOT NULL DEFAULT 'invitation'
  CONSTRAINT ck_practitioner_athlete_link_origin CHECK (origin IN ('invitation','directory'));
ALTER TABLE spps_identity_dev.practitioner_athlete_links
  ADD CONSTRAINT ck_active_link_has_record CHECK (status <> 'ACTIVE' OR athlete_record_id IS NOT NULL);
ALTER TABLE spps_identity_dev.platform_bookings ADD COLUMN service_id integer;
ALTER TABLE spps_identity_dev.platform_bookings ADD COLUMN service_name varchar(80);

-- Who is booking, for whom, and is there already a link? Raises 28000 without a verified session, P0002 when the
-- account may not book for that person, 42501 when a self-booking account has no adult attestation.
CREATE FUNCTION spps_identity_dev.portal_directory_booking_context(
 p_token_hash text, p_practitioner_id integer, p_subject_person_id integer)
 RETURNS TABLE(actor integer, person_id integer, authority_id integer, link_id integer, link_status text)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_allowed boolean; v_authority integer;
        v_link practitioner_athlete_links%ROWTYPE;
BEGIN
 IF p_practitioner_id IS NULL OR p_practitioner_id<=0 OR p_subject_person_id IS NULL OR p_subject_person_id<=0 THEN
  RAISE EXCEPTION 'invalid booking' USING ERRCODE='22023';
 END IF;
 SELECT c.allowed, c.authority_id INTO v_allowed, v_authority
  FROM acting_authority(v_actor, p_subject_person_id, 'booking_management') c;
 IF v_allowed IS NOT TRUE THEN RAISE EXCEPTION 'not authorised for this person' USING ERRCODE='P0002'; END IF;
 -- Booking for oneself needs the adult attestation given at registration; a minor books only through a guardian.
 IF v_authority IS NULL AND NOT EXISTS (SELECT 1 FROM platform_accounts a WHERE a.id=v_actor
                                         AND a.person_id=p_subject_person_id AND a.adult_attested_at IS NOT NULL) THEN
  RAISE EXCEPTION 'guardian required' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v_link FROM practitioner_athlete_links l
  WHERE l.person_id=p_subject_person_id AND l.practitioner_id=p_practitioner_id
    AND l.status IN ('REQUESTED','CONSENT_PENDING','PRACTITIONER_REVIEW','ACTIVE')
  ORDER BY l.id DESC LIMIT 1;
 RETURN QUERY SELECT v_actor, p_subject_person_id, v_authority, v_link.id, v_link.status::text;
END $body$;

CREATE FUNCTION spps_identity_dev.portal_request_directory_booking(
 p_token_hash text, p_practitioner_id integer, p_tenant_id integer, p_subject_person_id integer,
 p_slot_id integer, p_starts_at timestamptz, p_duration_minutes integer, p_video boolean, p_note text,
 p_service_id integer, p_service_name text)
 RETURNS TABLE(booking_id integer, link_id integer, link_status text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
#variable_conflict use_column
DECLARE c record; v_link integer; v_status text; v_id integer;
BEGIN
 PERFORM booking_expire_due();
 SELECT * INTO c FROM portal_directory_booking_context(p_token_hash, p_practitioner_id, p_subject_person_id);
 IF p_tenant_id IS NULL OR p_tenant_id<=0 OR p_slot_id IS NULL OR p_slot_id<=0
    OR p_duration_minutes NOT BETWEEN 20 AND 120 OR length(coalesce(p_note,''))>300
    OR p_service_id IS NULL OR p_service_id<=0 OR length(btrim(coalesce(p_service_name,''))) NOT BETWEEN 3 AND 80 THEN
  RAISE EXCEPTION 'invalid booking' USING ERRCODE='22023';
 END IF;
 IF p_starts_at <= now()+interval '2 hours' THEN
  RAISE EXCEPTION 'slot too soon or past' USING ERRCODE='22023';
 END IF;
 IF NOT consent_is_current(c.person_id, p_practitioner_id, 'CASE_LINK')
    OR NOT consent_is_current(c.person_id, p_practitioner_id, 'APPOINTMENT_BOOKING')
    OR (p_video AND NOT consent_is_current(c.person_id, p_practitioner_id, 'VIDEO_CONSULTATION')) THEN
  RAISE EXCEPTION 'booking consent required' USING ERRCODE='P0003';
 END IF;
 v_link := c.link_id; v_status := c.link_status;
 IF v_link IS NULL THEN
  BEGIN
   INSERT INTO practitioner_athlete_links(person_id,practitioner_id,athlete_record_id,tenant_id,status,origin,
     requested_by_account_id,guardian_authority_id,requested_at)
   VALUES (c.person_id,p_practitioner_id,NULL,p_tenant_id,'PRACTITIONER_REVIEW','directory',c.actor,c.authority_id,now())
   RETURNING id INTO v_link;
  EXCEPTION WHEN unique_violation THEN
   RAISE EXCEPTION 'already linked or pending' USING ERRCODE='23505';
  END;
  v_status := 'PRACTITIONER_REVIEW';
  PERFORM audit(c.actor,'link.requested_from_directory','link',v_link,'');
 ELSIF v_status <> 'ACTIVE' THEN
  -- A pending link carries one open request at a time; an expired, declined or cancelled one frees it.
  IF EXISTS (SELECT 1 FROM platform_bookings b WHERE b.link_id=v_link AND b.status='PENDING_APPROVAL') THEN
   RAISE EXCEPTION 'a request is already waiting for this practitioner' USING ERRCODE='23505';
  END IF;
  IF (SELECT l.origin FROM practitioner_athlete_links l WHERE l.id=v_link) <> 'directory' THEN
   RAISE EXCEPTION 'finish the invitation link first' USING ERRCODE='P0004';
  END IF;
 ELSIF NOT link_is_active(v_link) THEN
  RAISE EXCEPTION 'link is no longer active' USING ERRCODE='P0004';
 END IF;
 INSERT INTO platform_bookings(practitioner_id,tenant_id,slot_id,starts_at,ends_at,amount_paise,person_id,link_id,
   requested_by_account_id,guardian_authority_id,status,video_requested,client_note,action_deadline,created_at,
   updated_at,service_id,service_name)
 VALUES (p_practitioner_id,p_tenant_id,p_slot_id,p_starts_at,p_starts_at+make_interval(mins=>p_duration_minutes),0,
   c.person_id,v_link,c.actor,c.authority_id,'PENDING_APPROVAL',coalesce(p_video,false),btrim(coalesce(p_note,'')),
   least(now()+interval '48 hours', p_starts_at-interval '2 hours'),now(),now(),p_service_id,btrim(p_service_name))
 RETURNING id INTO v_id;
 PERFORM notify_practitioner(p_practitioner_id,'booking.requested','booking',v_id);
 PERFORM audit(c.actor,'booking.requested','booking',v_id,'directory');
 RETURN QUERY SELECT v_id, v_link, v_status;
END $body$;

-- Per booking: the chosen service and whether its link still waits for the practitioner (no other data).
CREATE FUNCTION spps_identity_dev.practitioner_booking_details(p_practitioner_id integer)
 RETURNS TABLE(booking_id integer, service_id integer, service_name text, link_id integer, link_status text,
               link_origin text, subject_display_name text)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT b.id, b.service_id, b.service_name::text, l.id, l.status::text, l.origin::text,
        COALESCE(NULLIF(p.display_name,''), a.display_name)::text
 FROM platform_bookings b JOIN practitioner_athlete_links l ON l.id=b.link_id
 JOIN platform_people p ON p.id=b.person_id
 LEFT JOIN platform_accounts a ON a.person_id=b.person_id
 WHERE b.practitioner_id=p_practitioner_id
$body$;

-- The server has just created p_athlete_record_id under the practitioner's own clinical RLS (as for invitations,
-- ownership of the record is proven in the clinical database before this call).
CREATE FUNCTION spps_identity_dev.practitioner_approve_directory_booking(
 p_practitioner_id integer, p_booking_id integer, p_athlete_record_id integer, p_reason text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE b platform_bookings%ROWTYPE; v_link practitioner_athlete_links%ROWTYPE;
BEGIN
 PERFORM booking_expire_due();
 IF p_athlete_record_id IS NULL OR p_athlete_record_id<=0 THEN
  RAISE EXCEPTION 'invalid record' USING ERRCODE='22023';
 END IF;
 SELECT * INTO b FROM platform_bookings WHERE id=p_booking_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF b.status<>'PENDING_APPROVAL' THEN RAISE EXCEPTION 'booking is %', b.status USING ERRCODE='P0004'; END IF;
 SELECT * INTO v_link FROM practitioner_athlete_links WHERE id=b.link_id AND practitioner_id=p_practitioner_id FOR UPDATE;
 IF NOT FOUND OR v_link.origin<>'directory' OR v_link.status<>'PRACTITIONER_REVIEW'
    OR v_link.athlete_record_id IS NOT NULL THEN
  RAISE EXCEPTION 'not a pending directory request' USING ERRCODE='P0004';
 END IF;
 IF NOT consent_is_current(v_link.person_id, p_practitioner_id, 'CASE_LINK')
    OR NOT consent_is_current(v_link.person_id, p_practitioner_id, 'APPOINTMENT_BOOKING')
    OR (b.video_requested AND NOT consent_is_current(v_link.person_id, p_practitioner_id, 'VIDEO_CONSULTATION')) THEN
  RAISE EXCEPTION 'consent is no longer current' USING ERRCODE='P0003';
 END IF;
 IF v_link.guardian_authority_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM guardian_authorities g WHERE g.id=v_link.guardian_authority_id AND g.status='VERIFIED'
     AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>now())) THEN
  RAISE EXCEPTION 'guardian authority no longer valid' USING ERRCODE='42501';
 END IF;
 BEGIN
  UPDATE practitioner_athlete_links SET athlete_record_id=p_athlete_record_id, status='ACTIVE', approved_at=now(),
    activated_at=now(),
    consent_event_id=(SELECT max(e.id) FROM platform_consent_events e WHERE e.person_id=v_link.person_id
                      AND e.practitioner_id=p_practitioner_id AND e.purpose='CASE_LINK' AND e.decision='GRANTED')
   WHERE id=v_link.id;
 EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'athlete record already linked' USING ERRCODE='23505';
 END;
 PERFORM audit(NULL,'link.approved','link',v_link.id,'practitioner '||p_practitioner_id||' via directory booking');
 RETURN free_practitioner_decide_booking(p_practitioner_id, p_booking_id, 'APPROVE', p_reason, 24);
END $body$;

-- After a declined first request, the pending directory link is closed too (it never became active).
CREATE FUNCTION spps_identity_dev.practitioner_decline_directory_link(p_practitioner_id integer, p_booking_id integer)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_link practitioner_athlete_links%ROWTYPE;
BEGIN
 SELECT l.* INTO v_link FROM practitioner_athlete_links l JOIN platform_bookings b ON b.link_id=l.id
  WHERE b.id=p_booking_id AND b.practitioner_id=p_practitioner_id AND l.practitioner_id=p_practitioner_id FOR UPDATE OF l;
 IF NOT FOUND THEN RAISE EXCEPTION 'booking not found' USING ERRCODE='P0002'; END IF;
 IF v_link.origin<>'directory' OR v_link.status<>'PRACTITIONER_REVIEW'
    OR EXISTS (SELECT 1 FROM platform_bookings x WHERE x.link_id=v_link.id AND x.status='PENDING_APPROVAL') THEN
  RETURN v_link.status;
 END IF;
 UPDATE practitioner_athlete_links SET status='DECLINED', decided_reason='appointment request declined'
  WHERE id=v_link.id;
 PERFORM audit(NULL,'link.declined','link',v_link.id,'practitioner '||p_practitioner_id||' via directory booking');
 RETURN 'DECLINED';
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_identity_dev'
    AND p.proname IN ('portal_directory_booking_context','portal_request_directory_booking',
                      'practitioner_booking_details','practitioner_approve_directory_booking',
                      'practitioner_decline_directory_link') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0043_v2_practitioner_services.sql','c260f5486760c18677caa037f9cd89ab0842c1a05415d803364db690538a92a0') ON CONFLICT (name) DO NOTHING;
COMMIT;
