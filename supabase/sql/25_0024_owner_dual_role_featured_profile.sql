-- SPPS production database, step 25 of the serial set: 0024_owner_dual_role_featured_profile
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0024_owner_dual_role_featured_profile.sql
BEGIN;
-- 0024: verified founder owner can be master admin AND ordinary practitioner.
-- Additive successor to 0022. This is run only by a privileged migration role;
-- the function is only callable by the private SPPS app DB role.
-- FastAPI MUST independently verify the Supabase access token, email confirmation
-- and authenticator AAL2 before passing p_auth_uid. Never trust public metadata.
CREATE OR REPLACE FUNCTION spps_dev.claim_verified_platform_owner(
    p_auth_uid uuid, p_email text, p_tenant_slug text, p_password_hash text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $owner$
DECLARE
    v_tenant_id integer;
    v_owner_id integer;
    v_name constant text := 'Dr. Karanbir Singh';
    v_qualifications constant text := 'PhD (Sports Science) · MA (Sport Psychology)';
    v_specialisms constant text := 'Mental conditioning · Cognitive performance · Sport injury psychology';
    v_bio constant text :=
      'Senior Lead in Sports and Performance Psychology. '
      || '12+ years of professional experience delivering mental conditioning '
      || 'and cognitive performance frameworks for athletes.';
BEGIN
    IF p_auth_uid IS NULL
       OR p_email IS DISTINCT FROM 'karansportpsy@gmail.com'
       OR p_password_hash IS NULL OR length(p_password_hash) NOT BETWEEN 20 AND 256
    THEN
        RAISE EXCEPTION 'Verified owner identity required' USING ERRCODE='42501';
    END IF;
    SELECT id INTO v_tenant_id FROM spps_dev.tenants WHERE slug=p_tenant_slug;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION 'Owner tenant is not provisioned' USING ERRCODE='22023';
    END IF;

    SELECT id INTO v_owner_id FROM spps_dev.practitioners
     WHERE lower(email)='karansportpsy@gmail.com' FOR UPDATE;
    -- The first verified claim creates/publishes the founder. Later sign-ins
    -- must NOT reverse a deliberate suspension, rejection or unlisting.
    IF v_owner_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM spps_dev.audit_events
        WHERE actor_id=v_owner_id
          AND action='owner.verified_aal2_dual_role_profile_claim'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM spps_dev.practitioners p
            JOIN spps_dev.practitioner_profiles pr
              ON pr.practitioner_id=p.id
            WHERE p.id=v_owner_id AND p.tenant_id=v_tenant_id
              AND p.active AND p.is_admin AND p.tier='free'
              AND pr.status='approved' AND pr.credentials_verified
              AND pr.public_approved AND pr.supabase_auth_uid=p_auth_uid::text
        ) THEN
            RAISE EXCEPTION 'Existing owner claim has a security or publication hold'
                USING ERRCODE='42501';
        END IF;
        RETURN v_owner_id;
    END IF;
    IF v_owner_id IS NULL THEN
        INSERT INTO spps_dev.practitioners
            (tenant_id,email,name,password_hash,active,tier,is_admin)
        VALUES
            (v_tenant_id,'karansportpsy@gmail.com',v_name,p_password_hash,TRUE,'free',TRUE)
        RETURNING id INTO v_owner_id;
    ELSE
        -- Refuse an explicitly rejected owner profile even if the correct
        -- person later authenticates; restoration requires separate review.
        IF EXISTS (
            SELECT 1 FROM spps_dev.practitioner_profiles
            WHERE practitioner_id=v_owner_id AND status='rejected'
        ) THEN
            RAISE EXCEPTION 'Rejected owner profile requires manual review'
              USING ERRCODE='42501';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM spps_dev.practitioners
            WHERE id=v_owner_id AND tenant_id=v_tenant_id
        ) THEN
            RAISE EXCEPTION 'Owner tenant mismatch' USING ERRCODE='42501';
        END IF;
        IF EXISTS (
            SELECT 1 FROM spps_dev.practitioner_profiles
            WHERE practitioner_id=v_owner_id AND supabase_auth_uid IS NOT NULL
                AND supabase_auth_uid<>p_auth_uid::text
        ) THEN
            RAISE EXCEPTION 'Owner identity already bound to a different verified account'
                USING ERRCODE='42501';
        END IF;
        UPDATE spps_dev.practitioners
           SET name=v_name,active=TRUE,is_admin=TRUE,tier='free'
         WHERE id=v_owner_id;
    END IF;

    INSERT INTO spps_dev.practitioner_profiles (
        practitioner_id,qualifications,experience_years,achievements,
        specialisms,public_approved,credentials_verified,avatar_key,
        consultation_fee_paise,status,supabase_auth_uid,email_verified_at,approved_at
    ) VALUES (
        v_owner_id,v_qualifications,12,v_bio,v_specialisms,
        TRUE,TRUE,'portrait_01',0,'approved',p_auth_uid::text,now(),now()
    )
    ON CONFLICT (practitioner_id) DO UPDATE
       SET qualifications=EXCLUDED.qualifications,
           experience_years=EXCLUDED.experience_years,
           achievements=EXCLUDED.achievements,
           specialisms=EXCLUDED.specialisms,
           public_approved=TRUE,
           credentials_verified=TRUE,
           consultation_fee_paise=0,
           status='approved',
           supabase_auth_uid=EXCLUDED.supabase_auth_uid,
           email_verified_at=COALESCE(
                spps_dev.practitioner_profiles.email_verified_at,EXCLUDED.email_verified_at),
           approved_at=COALESCE(
                spps_dev.practitioner_profiles.approved_at,EXCLUDED.approved_at);

    INSERT INTO spps_dev.audit_events
        (tenant_id,actor_id,action,object_type,object_id,happened_at)
    VALUES
        (v_tenant_id,v_owner_id,'owner.verified_aal2_dual_role_profile_claim',
         'practitioner',v_owner_id,now());
    RETURN v_owner_id;
END
$owner$;
REVOKE ALL ON FUNCTION spps_dev.claim_verified_platform_owner(uuid,text,text,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.claim_verified_platform_owner(uuid,text,text,text)
    TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0024_owner_dual_role_featured_profile.sql','3c21358aea1e401bf59b8f0d9cb8e131582aad61ac9468b0ec777b7a67c40b8e') ON CONFLICT (name) DO NOTHING;
COMMIT;
