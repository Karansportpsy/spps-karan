-- SPPS production database, step 34 of the serial set: 0033_v2_registration_state_machine
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0033_v2_registration_state_machine.sql
BEGIN;
-- 0033 (v2 phase 1): server-side pending practitioner application, registration state machine and the review
-- e-mail delivery log.
--
-- * registration_applications: the application text is stored at sign-up (state 'applied', unverified) and
--   promoted to 'under_review' by the first authenticated call from any browser once GoTrue reports the e-mail as
--   confirmed. A confirmed account with no text is kept as 'email_verified' / has_application=false so the owner
--   can see it.
-- * registration_transitions: append-only who/when audit for every state change (never application text).
-- * email_deliveries: one row per approval / rejection / more-information e-mail; failures are visible to the owner.
--
-- Nothing is granted to anonymous callers. The anonymous and applicant writes go through the SECURITY DEFINER
-- functions below, which fix every state value themselves. Only the platform owner (current_is_admin()) can read
-- applications, and only for their own tenant. A practitioner cannot read any application, including other
-- applicants' pending ones. Run by the privileged migration role.

CREATE TABLE spps_dev.registration_applications (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    email varchar(250) NOT NULL,
    auth_uid varchar(36),
    claim_hash varchar(64) UNIQUE,
    state varchar(24) NOT NULL DEFAULT 'applied'
        CHECK (state IN ('applied','email_verified','under_review','more_info_requested','approved','rejected')),
    has_application boolean NOT NULL DEFAULT true,
    name varchar(120) NOT NULL DEFAULT '',
    qualifications text NOT NULL DEFAULT '',
    experience_years integer NOT NULL DEFAULT 0 CHECK (experience_years BETWEEN 0 AND 70),
    achievements text NOT NULL DEFAULT '',
    specialisms varchar(250) NOT NULL DEFAULT '',
    practitioner_id integer UNIQUE REFERENCES spps_dev.practitioners(id),
    email_verified_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state <> 'applied' OR expires_at IS NOT NULL OR NOT has_application),
    CHECK (state IN ('applied','email_verified') OR practitioner_id IS NOT NULL)
);
CREATE INDEX ix_registration_applications_tenant_id ON spps_dev.registration_applications(tenant_id);
CREATE INDEX ix_registration_applications_email ON spps_dev.registration_applications(email);
CREATE INDEX ix_registration_applications_auth_uid ON spps_dev.registration_applications(auth_uid);
CREATE INDEX ix_registration_applications_state ON spps_dev.registration_applications(state);

CREATE TABLE spps_dev.registration_transitions (
    id serial PRIMARY KEY,
    application_id integer NOT NULL REFERENCES spps_dev.registration_applications(id) ON DELETE CASCADE,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    from_state varchar(24),
    to_state varchar(24) NOT NULL,
    actor_kind varchar(16) NOT NULL CHECK (actor_kind IN ('applicant','system','owner')),
    actor_practitioner_id integer REFERENCES spps_dev.practitioners(id),
    happened_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_registration_transitions_application_id ON spps_dev.registration_transitions(application_id);
CREATE INDEX ix_registration_transitions_tenant_id ON spps_dev.registration_transitions(tenant_id);
CREATE INDEX ix_registration_transitions_actor ON spps_dev.registration_transitions(actor_practitioner_id);

CREATE TABLE spps_dev.email_deliveries (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    kind varchar(24) NOT NULL CHECK (kind IN ('approved','rejected','request_more_info')),
    status varchar(12) NOT NULL DEFAULT 'failed' CHECK (status IN ('sent','failed','superseded')),
    attempts integer NOT NULL DEFAULT 0,
    failure_reason varchar(60) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz,
    created_by integer REFERENCES spps_dev.practitioners(id)
);
CREATE INDEX ix_email_deliveries_tenant_id ON spps_dev.email_deliveries(tenant_id);
CREATE INDEX ix_email_deliveries_practitioner_id ON spps_dev.email_deliveries(practitioner_id);
CREATE INDEX ix_email_deliveries_status ON spps_dev.email_deliveries(status);
CREATE INDEX ix_email_deliveries_created_by ON spps_dev.email_deliveries(created_by);

ALTER TABLE spps_dev.registration_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE spps_dev.registration_applications FORCE ROW LEVEL SECURITY;
ALTER TABLE spps_dev.registration_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE spps_dev.registration_transitions FORCE ROW LEVEL SECURITY;
ALTER TABLE spps_dev.email_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE spps_dev.email_deliveries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON spps_dev.registration_applications, spps_dev.registration_transitions,
              spps_dev.email_deliveries FROM PUBLIC;

-- Owner (same tenant) reads applications and moves review states; v1-path practitioners get a row on first decision.
GRANT SELECT, INSERT ON spps_dev.registration_applications TO spps_dev_app;
GRANT UPDATE (state, updated_at) ON spps_dev.registration_applications TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.registration_applications_id_seq TO spps_dev_app;
CREATE POLICY v2_owner_reads_applications ON spps_dev.registration_applications FOR SELECT TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());
CREATE POLICY v2_owner_moves_review_state ON spps_dev.registration_applications FOR UPDATE TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id()
         AND state IN ('under_review','more_info_requested','approved','rejected'))
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id()
         AND state IN ('under_review','more_info_requested','approved','rejected'));
CREATE POLICY v2_owner_backfills_v1_application ON spps_dev.registration_applications FOR INSERT TO spps_dev_app
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id() AND state = 'under_review'
         AND practitioner_id IS NOT NULL AND spps_dev.same_tenant_practitioner(practitioner_id)
         AND claim_hash IS NULL);

GRANT SELECT, INSERT ON spps_dev.registration_transitions TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.registration_transitions_id_seq TO spps_dev_app;
CREATE POLICY v2_owner_reads_transitions ON spps_dev.registration_transitions FOR SELECT TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());
CREATE POLICY v2_owner_records_transition ON spps_dev.registration_transitions FOR INSERT TO spps_dev_app
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id()
         AND ((actor_kind = 'owner' AND actor_practitioner_id = spps_dev.practitioner_id())
              OR (actor_kind = 'system' AND from_state IS NULL AND actor_practitioner_id IS NULL)));

GRANT SELECT, INSERT ON spps_dev.email_deliveries TO spps_dev_app;
GRANT UPDATE (status, attempts, failure_reason, last_attempt_at) ON spps_dev.email_deliveries TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.email_deliveries_id_seq TO spps_dev_app;
CREATE POLICY v2_owner_email_deliveries ON spps_dev.email_deliveries TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id())
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());

-- 1. Anonymous sign-up: store the text as 'applied'. Never overwrites an earlier row.
CREATE FUNCTION spps_dev.v2_submit_pending_application(
    p_tenant_slug text, p_email text, p_claim_hash text, p_name text, p_qualifications text,
    p_experience_years integer, p_achievements text, p_specialisms text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_tenant integer; v_id integer;
BEGIN
    IF p_email IS NULL OR p_email <> lower(btrim(p_email)) OR length(p_email) NOT BETWEEN 5 AND 250
       OR p_email !~ '^[a-z0-9._%+-]+@[a-z0-9.-]+[.][a-z]{2,}$'
       OR p_claim_hash !~ '^[0-9a-f]{64}$'
       OR length(btrim(coalesce(p_name,''))) NOT BETWEEN 3 AND 120
       OR length(btrim(coalesce(p_qualifications,''))) NOT BETWEEN 4 AND 1000
       OR p_experience_years IS NULL OR p_experience_years NOT BETWEEN 0 AND 70
       OR length(coalesce(p_achievements,'')) > 1500 OR length(coalesce(p_specialisms,'')) > 250 THEN
        RAISE EXCEPTION 'Invalid pending application' USING ERRCODE='22023';
    END IF;
    SELECT id INTO v_tenant FROM spps_dev.tenants WHERE slug = p_tenant_slug;
    IF v_tenant IS NULL THEN RAISE EXCEPTION 'Tenant unavailable' USING ERRCODE='22023'; END IF;
    -- Expired, never-confirmed rows are removed here so the table cannot grow without bound.
    DELETE FROM spps_dev.registration_applications WHERE state='applied' AND has_application AND expires_at < now();
    INSERT INTO spps_dev.registration_applications
        (tenant_id,email,claim_hash,state,has_application,name,qualifications,experience_years,
         achievements,specialisms,expires_at)
    VALUES (v_tenant,p_email,p_claim_hash,'applied',true,btrim(p_name),btrim(p_qualifications),p_experience_years,
            coalesce(p_achievements,''),coalesce(p_specialisms,''),now()+interval '7 days')
    RETURNING id INTO v_id;
    RETURN v_id;
END $fn$;

-- 2. First authenticated call after confirmation (the API has already verified the token with GoTrue).
CREATE FUNCTION spps_dev.v2_promote_application(
    p_auth_uid uuid, p_email text, p_tenant_slug text, p_claim_hash text,
    p_name text, p_qualifications text, p_experience_years integer, p_achievements text, p_specialisms text,
    p_password_hash text)
RETURNS TABLE (state text, practitioner_id integer, has_application boolean, promoted boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
#variable_conflict use_column
DECLARE v_tenant integer; v_row spps_dev.registration_applications%ROWTYPE; v_pid integer; v_existing integer;
BEGIN
    IF p_auth_uid IS NULL OR p_email IS NULL OR p_email <> lower(btrim(p_email))
       OR p_password_hash IS NULL OR length(p_password_hash) NOT BETWEEN 20 AND 256 THEN
        RAISE EXCEPTION 'Invalid promotion' USING ERRCODE='22023';
    END IF;
    SELECT id INTO v_tenant FROM spps_dev.tenants WHERE slug = p_tenant_slug;
    IF v_tenant IS NULL THEN RAISE EXCEPTION 'Tenant unavailable' USING ERRCODE='22023'; END IF;

    SELECT pr.practitioner_id INTO v_existing FROM spps_dev.practitioner_profiles pr
     WHERE pr.supabase_auth_uid = p_auth_uid::text;
    IF v_existing IS NOT NULL THEN
        RETURN QUERY SELECT coalesce((SELECT r.state::text FROM spps_dev.registration_applications r
                                       WHERE r.practitioner_id = v_existing),'under_review'),
                            v_existing, true, false;
        RETURN;
    END IF;

    IF p_claim_hash IS NOT NULL THEN
        SELECT * INTO v_row FROM spps_dev.registration_applications r
         WHERE r.claim_hash = p_claim_hash AND r.email = p_email AND r.state = 'applied'
           AND r.tenant_id = v_tenant AND r.expires_at >= now()
         FOR UPDATE;
    END IF;
    IF v_row.id IS NULL AND length(btrim(coalesce(p_name,''))) >= 3
       AND length(btrim(coalesce(p_qualifications,''))) >= 4 THEN
        -- v1 carried-details fallback (user_metadata.application)
        INSERT INTO spps_dev.registration_applications
            (tenant_id,email,state,has_application,name,qualifications,experience_years,achievements,specialisms,
             expires_at)
        VALUES (v_tenant,p_email,'applied',true,left(btrim(p_name),120),left(btrim(p_qualifications),1000),
                least(70,greatest(0,coalesce(p_experience_years,0))),left(coalesce(p_achievements,''),1500),
                left(coalesce(p_specialisms,''),250),now()+interval '1 hour')
        RETURNING * INTO v_row;
    END IF;

    IF v_row.id IS NULL THEN
        -- Confirmed account, no application yet: visible to the owner.
        IF NOT EXISTS (SELECT 1 FROM spps_dev.registration_applications r
                        WHERE r.auth_uid = p_auth_uid::text AND NOT r.has_application) THEN
            INSERT INTO spps_dev.registration_applications
                (tenant_id,email,auth_uid,state,has_application,email_verified_at)
            VALUES (v_tenant,p_email,p_auth_uid::text,'email_verified',false,now())
            RETURNING * INTO v_row;
            INSERT INTO spps_dev.registration_transitions(application_id,tenant_id,from_state,to_state,actor_kind)
            VALUES (v_row.id,v_tenant,'applied','email_verified','system');
        END IF;
        RETURN QUERY SELECT 'email_verified'::text, NULL::integer, false, false;
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM spps_dev.practitioners p WHERE p.email = p_email) THEN
        RAISE EXCEPTION 'Already registered' USING ERRCODE='23505';
    END IF;
    INSERT INTO spps_dev.practitioners(tenant_id,email,name,password_hash,active,tier,is_admin)
    VALUES (v_tenant,p_email,v_row.name,p_password_hash,false,'free',false)
    RETURNING id INTO v_pid;
    -- The photo may be added after confirmation (v2_set_application_photo); the profile starts without one.
    INSERT INTO spps_dev.practitioner_profiles
       (practitioner_id,qualifications,experience_years,achievements,specialisms,public_approved,avatar_key,
        consultation_fee_paise,avatar_bytes,credentials_verified,status,supabase_auth_uid,email_verified_at)
    VALUES (v_pid,v_row.qualifications,v_row.experience_years,v_row.achievements,v_row.specialisms,false,
            'portrait_01',0,NULL,false,'pending_approval',p_auth_uid::text,now());
    INSERT INTO spps_dev.terms_acceptances(practitioner_id,version,accepted_at) VALUES (v_pid,'beta-v1.0-free',now());
    INSERT INTO spps_dev.audit_events(tenant_id,actor_id,action,object_type,object_id,happened_at)
    VALUES (v_tenant,NULL,'registration.free_pending_credential_review','practitioner',v_pid,now());
    UPDATE spps_dev.registration_applications r
       SET auth_uid=p_auth_uid::text, email_verified_at=now(), claim_hash=NULL, practitioner_id=v_pid,
           state='under_review', updated_at=now()
     WHERE r.id = v_row.id;
    INSERT INTO spps_dev.registration_transitions(application_id,tenant_id,from_state,to_state,actor_kind)
    VALUES (v_row.id,v_tenant,'applied','email_verified','system'),
           (v_row.id,v_tenant,'email_verified','under_review','system');
    DELETE FROM spps_dev.registration_applications r WHERE r.auth_uid = p_auth_uid::text AND NOT r.has_application;
    RETURN QUERY SELECT 'under_review'::text, v_pid, true, true;
END $fn$;

-- 3. Photo after confirmation, only while the application is still pending.
CREATE FUNCTION spps_dev.v2_set_application_photo(p_auth_uid uuid, p_avatar bytea)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF p_avatar IS NULL OR octet_length(p_avatar) NOT BETWEEN 32 AND 250000 THEN
        RAISE EXCEPTION 'Invalid photo' USING ERRCODE='22023';
    END IF;
    UPDATE spps_dev.practitioner_profiles SET avatar_bytes = p_avatar
     WHERE supabase_auth_uid = p_auth_uid::text AND status = 'pending_approval';
    RETURN FOUND;
END $fn$;

CREATE FUNCTION spps_dev.v2_application_needs_photo(p_auth_uid uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
    SELECT EXISTS (SELECT 1 FROM spps_dev.practitioner_profiles
                    WHERE supabase_auth_uid = p_auth_uid::text AND status = 'pending_approval'
                      AND avatar_bytes IS NULL)
$fn$;

-- 4. Applicant answered a "more information" request.
CREATE FUNCTION spps_dev.v2_applicant_resubmitted(p_auth_uid uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_row spps_dev.registration_applications%ROWTYPE;
BEGIN
    SELECT r.* INTO v_row FROM spps_dev.registration_applications r
      JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = r.practitioner_id
     WHERE pr.supabase_auth_uid = p_auth_uid::text FOR UPDATE OF r;
    IF v_row.id IS NOT NULL AND v_row.state = 'more_info_requested' THEN
        UPDATE spps_dev.registration_applications SET state='under_review', updated_at=now() WHERE id=v_row.id;
        INSERT INTO spps_dev.registration_transitions(application_id,tenant_id,from_state,to_state,actor_kind)
        VALUES (v_row.id,v_row.tenant_id,'more_info_requested','under_review','applicant');
    END IF;
END $fn$;

REVOKE ALL ON FUNCTION spps_dev.v2_submit_pending_application(text,text,text,text,text,integer,text,text),
  spps_dev.v2_promote_application(uuid,text,text,text,text,text,integer,text,text,text),
  spps_dev.v2_set_application_photo(uuid,bytea), spps_dev.v2_application_needs_photo(uuid),
  spps_dev.v2_applicant_resubmitted(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.v2_submit_pending_application(text,text,text,text,text,integer,text,text),
  spps_dev.v2_promote_application(uuid,text,text,text,text,text,integer,text,text,text),
  spps_dev.v2_set_application_photo(uuid,bytea), spps_dev.v2_application_needs_photo(uuid),
  spps_dev.v2_applicant_resubmitted(uuid) TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0033_v2_registration_state_machine.sql','9855c5b697bde9f065b3f3d91a6b7d2ec49a7197aa283a81b68ebc167641405f') ON CONFLICT (name) DO NOTHING;
COMMIT;
