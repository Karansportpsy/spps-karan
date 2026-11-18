-- SPPS production database, step 35 of the serial set: 0034_v2_trust_foundation
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0034_v2_trust_foundation.sql
BEGIN;
-- 0034 (v2 phase 2): record-access audit log, data-export ledger, practitioner settings (AI opt-in), orderly closure and the owner archive.
--
-- * record_access_log: who read or wrote which athlete record and when (never content). Insert and read own rows
--   only; no UPDATE or DELETE grant. The owner reads counts through v2_owner_access_counts() (numbers only).
-- * data_exports: hash and size of each export the practitioner downloaded (never the file).
-- * practitioner_settings: per-practitioner AI opt-in; has effect only while SPPS_AI_FEATURES_ENABLED=true.
-- * practitioner_closures / closure_dispositions: the agreed orderly closure (docs/ACCOUNT_LIFECYCLE_DESIGN.md A3).
-- * owner_practitioner_archive: professional data and a minimal closure record, written once by the closure
--   function, readable only by the owner. It never contains athlete, case, note or assessment content.

CREATE TABLE spps_dev.record_access_log (
    id bigserial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    actor_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer,  -- no FK: the log outlives a purged record
    action varchar(8) NOT NULL CHECK (action IN ('read','write','export')),
    resource varchar(120) NOT NULL,
    request_id varchar(64) NOT NULL DEFAULT '',
    happened_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_record_access_log_tenant_id ON spps_dev.record_access_log(tenant_id);
CREATE INDEX ix_record_access_log_practitioner_id ON spps_dev.record_access_log(practitioner_id);
CREATE INDEX ix_record_access_log_actor_id ON spps_dev.record_access_log(actor_id);
CREATE INDEX ix_record_access_log_athlete_id ON spps_dev.record_access_log(athlete_id);

CREATE TABLE spps_dev.data_exports (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    sha256 varchar(64) NOT NULL,
    size_bytes integer NOT NULL,
    files integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_data_exports_tenant_id ON spps_dev.data_exports(tenant_id);
CREATE INDEX ix_data_exports_practitioner_id ON spps_dev.data_exports(practitioner_id);

CREATE TABLE spps_dev.practitioner_settings (
    practitioner_id integer PRIMARY KEY REFERENCES spps_dev.practitioners(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    ai_opt_in boolean NOT NULL DEFAULT false,
    ai_opt_in_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_practitioner_settings_tenant_id ON spps_dev.practitioner_settings(tenant_id);

CREATE TABLE spps_dev.practitioner_closures (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    state varchar(12) NOT NULL DEFAULT 'closing'
        CHECK (state IN ('closing','cancelled','closed','restored','purged')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    notice_ends_at timestamptz NOT NULL,
    completed_at timestamptz,
    cooling_off_ends_at timestamptz
);
CREATE INDEX ix_practitioner_closures_tenant_id ON spps_dev.practitioner_closures(tenant_id);
CREATE INDEX ix_practitioner_closures_practitioner_id ON spps_dev.practitioner_closures(practitioner_id);
CREATE UNIQUE INDEX uq_one_open_closure ON spps_dev.practitioner_closures(practitioner_id) WHERE state = 'closing';

CREATE TABLE spps_dev.closure_dispositions (
    id serial PRIMARY KEY,
    closure_id integer NOT NULL REFERENCES spps_dev.practitioner_closures(id) ON DELETE CASCADE,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer NOT NULL,
    disposition varchar(24) NOT NULL CHECK (disposition IN ('export_and_erase','keep_deactivated')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_closure_disposition_athlete UNIQUE (closure_id, athlete_id)
);
CREATE INDEX ix_closure_dispositions_closure_id ON spps_dev.closure_dispositions(closure_id);
CREATE INDEX ix_closure_dispositions_tenant_id ON spps_dev.closure_dispositions(tenant_id);
CREATE INDEX ix_closure_dispositions_practitioner_id ON spps_dev.closure_dispositions(practitioner_id);
CREATE INDEX ix_closure_dispositions_athlete_id ON spps_dev.closure_dispositions(athlete_id);

CREATE TABLE spps_dev.owner_practitioner_archive (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    closure_id integer NOT NULL REFERENCES spps_dev.practitioner_closures(id),
    snapshot text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_owner_practitioner_archive_tenant_id ON spps_dev.owner_practitioner_archive(tenant_id);
CREATE INDEX ix_owner_practitioner_archive_practitioner_id ON spps_dev.owner_practitioner_archive(practitioner_id);
CREATE INDEX ix_owner_practitioner_archive_closure_id ON spps_dev.owner_practitioner_archive(closure_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['record_access_log','data_exports','practitioner_settings','practitioner_closures',
                           'closure_dispositions','owner_practitioner_archive'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- Access log: append and read own. No UPDATE or DELETE for any application role.
GRANT SELECT, INSERT ON spps_dev.record_access_log TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.record_access_log_id_seq TO spps_dev_app;
CREATE POLICY v2_access_log_append_own ON spps_dev.record_access_log FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
              AND actor_id = spps_dev.practitioner_id());
CREATE POLICY v2_access_log_read_own ON spps_dev.record_access_log FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT ON spps_dev.data_exports TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.data_exports_id_seq TO spps_dev_app;
CREATE POLICY v2_exports_own ON spps_dev.data_exports TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT ON spps_dev.practitioner_settings TO spps_dev_app;
GRANT UPDATE (ai_opt_in, ai_opt_in_at, updated_at) ON spps_dev.practitioner_settings TO spps_dev_app;
CREATE POLICY v2_settings_own ON spps_dev.practitioner_settings TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT ON spps_dev.practitioner_closures TO spps_dev_app;
GRANT UPDATE (state) ON spps_dev.practitioner_closures TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.practitioner_closures_id_seq TO spps_dev_app;
CREATE POLICY v2_closure_read ON spps_dev.practitioner_closures FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id()
         AND (practitioner_id = spps_dev.practitioner_id() OR spps_dev.current_is_admin()));
CREATE POLICY v2_closure_start_own ON spps_dev.practitioner_closures FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
              AND state = 'closing' AND completed_at IS NULL AND notice_ends_at >= requested_at);
-- The only state a practitioner may set directly is "cancelled", and only while closing.
CREATE POLICY v2_closure_cancel_own ON spps_dev.practitioner_closures FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND state = 'closing')
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND state = 'cancelled');

GRANT SELECT, INSERT ON spps_dev.closure_dispositions TO spps_dev_app;
GRANT UPDATE (disposition, recorded_at) ON spps_dev.closure_dispositions TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.closure_dispositions_id_seq TO spps_dev_app;
CREATE POLICY v2_dispositions_own ON spps_dev.closure_dispositions TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.practitioner_closures c WHERE c.id = closure_id AND c.state = 'closing'
                AND c.practitioner_id = spps_dev.practitioner_id())
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id()));

GRANT SELECT ON spps_dev.owner_practitioner_archive TO spps_dev_app;
CREATE POLICY v2_archive_owner_only ON spps_dev.owner_practitioner_archive FOR SELECT TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());

-- Owner: counts per practitioner, numbers only.
CREATE FUNCTION spps_dev.v2_owner_access_counts()
RETURNS TABLE (practitioner_id integer, reads bigint, writes bigint, exports bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
    SELECT l.practitioner_id,
           count(*) FILTER (WHERE l.action = 'read'),
           count(*) FILTER (WHERE l.action = 'write'),
           count(*) FILTER (WHERE l.action = 'export')
      FROM spps_dev.record_access_log l
     WHERE spps_dev.current_is_admin() AND l.tenant_id = spps_dev.tenant_id()
     GROUP BY l.practitioner_id
$fn$;

-- Closure: complete the caller's own closure (all checks repeated here; one transaction).
CREATE FUNCTION spps_dev.v2_complete_own_closure(p_closure_id integer, p_counts jsonb, p_cooling_off_days integer)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_me integer := spps_dev.practitioner_id(); v_c spps_dev.practitioner_closures%ROWTYPE;
        v_missing integer; v_holds integer; v_snapshot jsonb;
BEGIN
    SELECT * INTO v_c FROM spps_dev.practitioner_closures WHERE id = p_closure_id FOR UPDATE;
    IF v_me IS NULL OR v_c.id IS NULL OR v_c.practitioner_id <> v_me OR v_c.state <> 'closing'
       OR v_c.notice_ends_at > now() OR p_cooling_off_days NOT BETWEEN 0 AND 365 THEN
        RAISE EXCEPTION 'Closure cannot be completed' USING ERRCODE='22023';
    END IF;
    SELECT count(*) INTO v_missing FROM spps_dev.athletes a
     WHERE a.owner_practitioner_id = v_me
       AND NOT EXISTS (SELECT 1 FROM spps_dev.closure_dispositions d WHERE d.closure_id = v_c.id AND d.athlete_id = a.id);
    SELECT count(*) INTO v_holds FROM spps_dev.retention_holds h JOIN spps_dev.athletes a ON a.id = h.athlete_id
     WHERE a.owner_practitioner_id = v_me AND h.active;
    IF v_missing > 0 OR v_holds > 0 THEN
        RAISE EXCEPTION 'Closure blocked' USING ERRCODE='22023';
    END IF;
    SELECT jsonb_build_object(
        'format','spps-owner-archive-v1',
        'practitioner', jsonb_build_object('id',p.id,'name',p.name,'email',p.email),
        'professional', jsonb_build_object('qualifications',pr.qualifications,'experience_years',pr.experience_years,
                                           'specialisms',pr.specialisms,'achievements',pr.achievements,'status',pr.status),
        'review_history', coalesce((SELECT jsonb_agg(jsonb_build_object('from',t.from_state,'to',t.to_state,
                                       'actor',t.actor_kind,'at',t.happened_at) ORDER BY t.id)
                                    FROM spps_dev.registration_transitions t
                                    JOIN spps_dev.registration_applications r ON r.id = t.application_id
                                   WHERE r.practitioner_id = p.id), '[]'::jsonb),
        'terms_acceptances', coalesce((SELECT jsonb_agg(jsonb_build_object('version',ta.version,'accepted_at',ta.accepted_at))
                                       FROM spps_dev.terms_acceptances ta WHERE ta.practitioner_id = p.id), '[]'::jsonb),
        'closure', jsonb_build_object('id',v_c.id,'requested_at',v_c.requested_at,'notice_ends_at',v_c.notice_ends_at,
                                      'completed_at',now(),'dispositions',coalesce(p_counts,'{}'::jsonb)))
      INTO v_snapshot
      FROM spps_dev.practitioners p LEFT JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id
     WHERE p.id = v_me;
    INSERT INTO spps_dev.owner_practitioner_archive(tenant_id,practitioner_id,closure_id,snapshot)
    VALUES (v_c.tenant_id, v_me, v_c.id, v_snapshot::text);
    UPDATE spps_dev.practitioner_closures
       SET state='closed', completed_at=now(), cooling_off_ends_at=now() + make_interval(days => p_cooling_off_days)
     WHERE id = v_c.id;
    UPDATE spps_dev.practitioners SET active = false WHERE id = v_me;
    UPDATE spps_dev.practitioner_profiles SET public_approved = false WHERE practitioner_id = v_me;
    UPDATE spps_dev.login_sessions SET revoked = true WHERE practitioner_id = v_me;
    INSERT INTO spps_dev.audit_events(tenant_id,actor_id,action,object_type,object_id,happened_at)
    VALUES (v_c.tenant_id, v_me, 'account.closed', 'practitioner', v_me, now());
END $fn$;

CREATE FUNCTION spps_dev.v2_owner_restore_closure(p_closure_id integer)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_c spps_dev.practitioner_closures%ROWTYPE;
BEGIN
    SELECT * INTO v_c FROM spps_dev.practitioner_closures WHERE id = p_closure_id FOR UPDATE;
    IF NOT spps_dev.current_is_admin() OR v_c.id IS NULL OR v_c.tenant_id <> spps_dev.tenant_id()
       OR v_c.state <> 'closed' OR v_c.cooling_off_ends_at < now() THEN
        RAISE EXCEPTION 'Closure cannot be restored' USING ERRCODE='22023';
    END IF;
    UPDATE spps_dev.practitioners SET active = true WHERE id = v_c.practitioner_id;
    UPDATE spps_dev.practitioner_closures SET state = 'restored' WHERE id = v_c.id;
END $fn$;

-- After cooling-off: erase the athlete records the practitioner chose to export and erase. Returns how many.
CREATE FUNCTION spps_dev.v2_owner_purge_closure(p_closure_id integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_c spps_dev.practitioner_closures%ROWTYPE; v_ids integer[];
BEGIN
    SELECT * INTO v_c FROM spps_dev.practitioner_closures WHERE id = p_closure_id FOR UPDATE;
    IF NOT spps_dev.current_is_admin() OR v_c.id IS NULL OR v_c.tenant_id <> spps_dev.tenant_id()
       OR v_c.state <> 'closed' OR v_c.cooling_off_ends_at > now() THEN
        RAISE EXCEPTION 'Closure cannot be purged' USING ERRCODE='22023';
    END IF;
    SELECT coalesce(array_agg(d.athlete_id), '{}') INTO v_ids FROM spps_dev.closure_dispositions d
     JOIN spps_dev.athletes a ON a.id = d.athlete_id AND a.owner_practitioner_id = v_c.practitioner_id
     WHERE d.closure_id = v_c.id AND d.disposition = 'export_and_erase';
    PERFORM spps_dev.v2_erase_athlete_records(v_ids);
    UPDATE spps_dev.practitioner_closures SET state = 'purged' WHERE id = v_c.id;
    RETURN coalesce(array_length(v_ids, 1), 0);
END $fn$;

-- Ordered erasure of athlete-linked clinical rows. Later migrations that add athlete-linked tables replace this
-- function (CREATE OR REPLACE in their own new file). Not executable by any application role.
CREATE FUNCTION spps_dev.v2_erase_athlete_records(p_ids integer[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN RETURN; END IF;
    DELETE FROM spps_dev.assessment_subscores s USING spps_dev.assessment_administrations a
     WHERE s.administration_id = a.id AND a.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.assessment_administrations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.injury_checkins c USING spps_dev.injury_episodes e
     WHERE c.episode_id = e.id AND e.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.injury_episodes WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.observations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.notes n USING spps_dev.cases c WHERE n.case_id = c.id AND c.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.assignments WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.reports WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.cases WHERE athlete_id = ANY(p_ids);
    UPDATE spps_dev.inquiries SET linked_athlete_id = NULL WHERE linked_athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.retention_holds WHERE athlete_id = ANY(p_ids) AND NOT active;
    DELETE FROM spps_dev.athletes WHERE id = ANY(p_ids);
END $fn$;

REVOKE ALL ON FUNCTION spps_dev.v2_owner_access_counts(), spps_dev.v2_complete_own_closure(integer,jsonb,integer),
  spps_dev.v2_owner_restore_closure(integer), spps_dev.v2_owner_purge_closure(integer),
  spps_dev.v2_erase_athlete_records(integer[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.v2_owner_access_counts(), spps_dev.v2_complete_own_closure(integer,jsonb,integer),
  spps_dev.v2_owner_restore_closure(integer), spps_dev.v2_owner_purge_closure(integer) TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0034_v2_trust_foundation.sql','acd4c46f35e668b47dbe5b5fb66bbe092349b5adb9ba534a0ee4e173864e30ce') ON CONFLICT (name) DO NOTHING;
COMMIT;
