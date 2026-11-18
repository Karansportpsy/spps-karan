BEGIN;
-- 0032: account lifecycle, phase A (practitioner self-service deactivation).
--
-- A practitioner can take their own public listing offline and bring it back. Nothing is deleted and no
-- approval flag changes, so reactivation needs no new review. The account keeps working: the practitioner can
-- still sign in, see their athletes and export their data. Only the public surface stops:
--   * the directory and the profile photo stop listing the practitioner,
--   * public enquiries naming the practitioner are refused (submit_public_enquiry already calls the directory).
--
-- Nothing is widened. spps_dev_app still has no UPDATE right on the new column; the only write path is the
-- SECURITY DEFINER function below, which takes no practitioner id and acts on the caller's own profile only.
-- The application switches the feature on with SPPS_ACCOUNT_LIFECYCLE_ENABLED=true and refuses to start in
-- production with the flag on until this migration has been run (server/release_preflight.py).
--
-- Run only by the privileged migration role (the Supabase SQL editor "postgres" role).

ALTER TABLE spps_dev.practitioner_profiles ADD COLUMN IF NOT EXISTS deactivated_at timestamptz;

-- 1. Public directory: identical to 0029 plus "not deactivated".
CREATE OR REPLACE FUNCTION spps_dev.public_practitioner_directory(p_tenant_slug text)
RETURNS TABLE (
    id integer, name text, qualifications text, experience_years integer,
    achievements text, specialisms text, avatar_key text, has_photo boolean,
    credentials_verified boolean, featured_owner boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $dir$
    SELECT p.id, p.name::text, pr.qualifications::text, pr.experience_years,
           pr.achievements::text, pr.specialisms::text, pr.avatar_key::text,
           (pr.avatar_bytes IS NOT NULL), pr.credentials_verified,
           (p.is_admin AND pr.supabase_auth_uid IS NOT NULL AND pr.email_verified_at IS NOT NULL)
      FROM spps_dev.practitioners p
      JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id
      JOIN spps_dev.tenants t ON t.id = p.tenant_id
     WHERE t.slug = p_tenant_slug
       AND p.active
       AND pr.status = 'approved'
       AND pr.credentials_verified
       AND pr.public_approved
       AND pr.deactivated_at IS NULL
     ORDER BY (p.is_admin AND pr.supabase_auth_uid IS NOT NULL AND pr.email_verified_at IS NOT NULL) DESC,
              p.id ASC
$dir$;

-- 2. Public photo: identical to 0029 plus "not deactivated".
CREATE OR REPLACE FUNCTION spps_dev.public_practitioner_photo(p_tenant_slug text, p_practitioner_id integer)
RETURNS bytea
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $photo$
    SELECT pr.avatar_bytes
      FROM spps_dev.practitioners p
      JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id
      JOIN spps_dev.tenants t ON t.id = p.tenant_id
     WHERE t.slug = p_tenant_slug AND p.id = p_practitioner_id
       AND p.active AND pr.status = 'approved' AND pr.credentials_verified AND pr.public_approved
       AND pr.deactivated_at IS NULL
$photo$;

-- 3. Own deactivation switch. p_deactivate TRUE takes the listing offline (keeping the first timestamp if it is
--    already offline); FALSE brings it back. Returns the deactivation time, or NULL when the listing is on.
CREATE OR REPLACE FUNCTION spps_dev.set_own_account_deactivation(p_deactivate boolean)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $deact$
DECLARE
    v_practitioner_id integer := spps_dev.practitioner_id();
    v_tenant_id integer := spps_dev.tenant_id();
    v_at timestamptz;
BEGIN
    IF v_practitioner_id IS NULL OR v_tenant_id IS NULL OR p_deactivate IS NULL THEN
        RAISE EXCEPTION 'Unauthenticated or invalid account state change' USING ERRCODE='42501';
    END IF;
    UPDATE spps_dev.practitioner_profiles AS profile
       SET deactivated_at = CASE WHEN p_deactivate THEN COALESCE(profile.deactivated_at, now()) ELSE NULL END
     WHERE profile.practitioner_id = v_practitioner_id
       AND EXISTS (
           SELECT 1 FROM spps_dev.practitioners AS owner
            WHERE owner.id = v_practitioner_id AND owner.tenant_id = v_tenant_id AND owner.active = TRUE
       )
    RETURNING profile.deactivated_at INTO v_at;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practitioner profile is not available to this identity' USING ERRCODE='42501';
    END IF;
    RETURN v_at;
END
$deact$;

REVOKE ALL ON FUNCTION spps_dev.set_own_account_deactivation(boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.set_own_account_deactivation(boolean) TO spps_dev_app;
COMMIT;
