-- SPPS production database, step 23 of the serial set: 0022_verified_owner_claim
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0022_verified_owner_claim.sql
BEGIN;
-- Migration 0022: One-time owner claim after *verified Supabase Auth + TOTP AAL2*.
-- This private function is invoked only by the SPPS backend after validating
-- the Supabase access token. It NEVER creates or confirms Supabase Auth users.
-- The owner email is fixed; callers cannot nominate an arbitrary admin.
CREATE OR REPLACE FUNCTION spps_dev.claim_verified_platform_owner(
    p_auth_uid uuid, p_email text, p_tenant_slug text, p_password_hash text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $body$
DECLARE v_tenant integer; v_id integer;
BEGIN
    IF p_auth_uid IS NULL
       OR p_email <> 'karansportpsy@gmail.com'
       OR p_password_hash IS NULL OR length(p_password_hash) NOT BETWEEN 20 AND 256
    THEN
        RAISE EXCEPTION 'Owner claim refused' USING ERRCODE='42501';
    END IF;
    SELECT id INTO v_tenant FROM spps_dev.tenants WHERE slug=p_tenant_slug;
    IF v_tenant IS NULL THEN
        RAISE EXCEPTION 'Owner tenant is not provisioned' USING ERRCODE='22023';
    END IF;
    SELECT id INTO v_id FROM spps_dev.practitioners
      WHERE lower(email)=p_email FOR UPDATE;
    IF v_id IS NULL THEN
        INSERT INTO spps_dev.practitioners
           (tenant_id,email,name,password_hash,active,tier,is_admin)
        VALUES
           (v_tenant,p_email,'SPPS Platform Owner',p_password_hash,TRUE,'free',TRUE)
        RETURNING id INTO v_id;
    ELSE
        IF EXISTS(SELECT 1 FROM spps_dev.practitioner_profiles
                  WHERE practitioner_id=v_id AND supabase_auth_uid IS NOT NULL
                    AND supabase_auth_uid<>p_auth_uid::text) THEN
            RAISE EXCEPTION 'Owner identity already bound to another verified subject'
                USING ERRCODE='42501';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM spps_dev.practitioners
                       WHERE id=v_id AND tenant_id=v_tenant) THEN
            RAISE EXCEPTION 'Owner tenant mismatch' USING ERRCODE='42501';
        END IF;
        UPDATE spps_dev.practitioners SET is_admin=TRUE,active=TRUE,tier='free'
          WHERE id=v_id;
    END IF;
    INSERT INTO spps_dev.practitioner_profiles
       (practitioner_id,qualifications,experience_years,achievements,specialisms,
        public_approved,avatar_key,consultation_fee_paise,credentials_verified,
        status,supabase_auth_uid,email_verified_at,approved_at)
    VALUES
       (v_id,'Platform administration',0,'','',FALSE,'portrait_01',0,FALSE,
        'approved',p_auth_uid::text,now(),now())
    ON CONFLICT (practitioner_id) DO UPDATE
      SET status='approved',supabase_auth_uid=EXCLUDED.supabase_auth_uid,
          email_verified_at=EXCLUDED.email_verified_at,
          public_approved=FALSE,approved_at=EXCLUDED.approved_at;
    INSERT INTO spps_dev.audit_events
       (tenant_id,actor_id,action,object_type,object_id,happened_at)
    VALUES (v_tenant,v_id,'owner.verified_auth_claim','practitioner',v_id,now());
    RETURN v_id;
END $body$;
REVOKE ALL ON FUNCTION spps_dev.claim_verified_platform_owner(uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.claim_verified_platform_owner(uuid,text,text,text)
  TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0022_verified_owner_claim.sql','d7697d8fb357c6f0fbcf2fb1eb63c6adec9f727c75408df258577166d0d2b8ed') ON CONFLICT (name) DO NOTHING;
COMMIT;
