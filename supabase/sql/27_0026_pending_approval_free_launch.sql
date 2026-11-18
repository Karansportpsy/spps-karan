-- SPPS production database, step 27 of the serial set: 0026_pending_approval_free_launch
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0026_pending_approval_free_launch.sql
BEGIN;
-- SPPS v1.0 release: fail-closed registration state for every non-owner signup.
-- Runs once under the restricted migration-owner role, NOT a web/public role.
ALTER TABLE spps_dev.practitioner_profiles
    DROP CONSTRAINT IF EXISTS ck_spps_dev_practitioner_review_status;
UPDATE spps_dev.practitioner_profiles
    SET status='pending_approval' WHERE status='pending';
ALTER TABLE spps_dev.practitioner_profiles
    ALTER COLUMN status SET DEFAULT 'pending_approval';
ALTER TABLE spps_dev.practitioner_profiles
    ADD CONSTRAINT ck_spps_dev_practitioner_review_status
    CHECK (status IN ('pending_approval','approved','rejected'));

-- Reissue the existing SECURITY DEFINER function with identical privileges,
-- input validation and fixed inactive/free/non-admin account creation.
-- Migration 0021: Hosted SPPS verified-email, pending-only registration gateway.
-- Apply AFTER 0019/0020 as the dedicated migration owner. No public DB grants.
-- The API MUST validate the Supabase user and confirmed email before calling.
-- This SECURITY DEFINER function has fixed columns/values and does not accept
-- admin, active, approval, tier, fee, or credential-verified inputs.
CREATE OR REPLACE FUNCTION spps_dev.register_verified_free_practitioner(
    p_auth_uid uuid,
    p_email text,
    p_tenant_slug text,
    p_name text,
    p_password_hash text,
    p_qualifications text,
    p_experience_years integer,
    p_achievements text,
    p_specialisms text,
    p_avatar_bytes bytea
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $body$
DECLARE
    v_tenant_id integer;
    v_practitioner_id integer;
BEGIN
    IF p_auth_uid IS NULL
       OR p_email IS NULL OR length(p_email) NOT BETWEEN 5 AND 250
       OR p_email <> lower(btrim(p_email))
       OR p_email !~ '^[a-z0-9._%+-]+@[a-z0-9.-]+[.][a-z]{2,}$'
       OR p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 3 AND 120
       OR p_password_hash IS NULL OR length(p_password_hash) NOT BETWEEN 20 AND 256
       OR p_qualifications IS NULL OR length(btrim(p_qualifications)) NOT BETWEEN 4 AND 4000
       OR p_experience_years IS NULL OR p_experience_years NOT BETWEEN 0 AND 70
       OR length(coalesce(p_achievements,'')) > 1500
       OR length(coalesce(p_specialisms,'')) > 250
       OR p_avatar_bytes IS NULL
       OR octet_length(p_avatar_bytes) NOT BETWEEN 32 AND 250000
    THEN
        RAISE EXCEPTION 'Invalid verified practitioner registration'
          USING ERRCODE='22023';
    END IF;

    SELECT id INTO v_tenant_id
      FROM spps_dev.tenants WHERE slug=p_tenant_slug;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION 'Registration tenant unavailable' USING ERRCODE='22023';
    END IF;

    INSERT INTO spps_dev.practitioners
       (tenant_id,email,name,password_hash,active,tier,is_admin)
    VALUES
       (v_tenant_id,p_email,btrim(p_name),p_password_hash,FALSE,'free',FALSE)
    RETURNING id INTO v_practitioner_id;

    INSERT INTO spps_dev.practitioner_profiles
       (practitioner_id,qualifications,experience_years,
        achievements,specialisms,public_approved,avatar_key,
        consultation_fee_paise,avatar_bytes,credentials_verified,
        status,supabase_auth_uid,email_verified_at)
    VALUES
       (v_practitioner_id,btrim(p_qualifications),p_experience_years,
        coalesce(p_achievements,''),coalesce(p_specialisms,''),
        FALSE,'portrait_01',0,p_avatar_bytes,FALSE,
        'pending_approval',p_auth_uid::text,now());

    INSERT INTO spps_dev.terms_acceptances
       (practitioner_id,version,accepted_at)
    VALUES
       (v_practitioner_id,'beta-v1.0-free',now());

    INSERT INTO spps_dev.audit_events
       (tenant_id,actor_id,action,object_type,object_id,happened_at)
    VALUES
       (v_tenant_id,NULL,'registration.free_pending_credential_review',
        'practitioner',v_practitioner_id,now());

    RETURN v_practitioner_id;
END
$body$;

REVOKE ALL ON FUNCTION
    spps_dev.register_verified_free_practitioner(
      uuid,text,text,text,text,text,integer,text,text,bytea)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    spps_dev.register_verified_free_practitioner(
      uuid,text,text,text,text,text,integer,text,text,bytea)
  TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0026_pending_approval_free_launch.sql','3bd29a4ea75116eb348f1837836c5203bc3571f7b1bf37f1d031f183340ad25a') ON CONFLICT (name) DO NOTHING;
COMMIT;
