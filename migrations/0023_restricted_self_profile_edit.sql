-- Stage free SPPS: practitioner-owned profile edits WITHOUT writable review flags.
-- Existing FORCE RLS policies remain unchanged. No UPDATE grants are widened.
-- Function accepts no target practitioner ID. It always revokes both credential
-- verification and public listing approval when editable credentials change.
BEGIN;
SET LOCAL search_path=spps_dev;

CREATE OR REPLACE FUNCTION spps_dev.update_own_practitioner_profile(
    p_qualifications text, p_experience_years integer,
    p_achievements text, p_specialisms text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $body$
DECLARE
    v_practitioner_id integer := spps_dev.practitioner_id();
    v_tenant_id integer := spps_dev.tenant_id();
    v_updated_id integer;
BEGIN
    IF v_practitioner_id IS NULL OR v_tenant_id IS NULL
       OR p_qualifications IS NULL OR length(p_qualifications) > 1000
       OR p_experience_years IS NULL OR p_experience_years NOT BETWEEN 0 AND 70
       OR p_achievements IS NULL OR length(p_achievements) > 1500
       OR p_specialisms IS NULL OR length(p_specialisms) > 250
    THEN
       RAISE EXCEPTION 'Invalid or unauthenticated practitioner profile edit'
           USING ERRCODE='42501';
    END IF;
    UPDATE spps_dev.practitioner_profiles AS profile
       SET qualifications=p_qualifications,
           experience_years=p_experience_years,
           achievements=p_achievements,
           specialisms=p_specialisms,
           public_approved=FALSE,
           credentials_verified=FALSE
     WHERE profile.practitioner_id=v_practitioner_id
       AND EXISTS (
           SELECT 1 FROM spps_dev.practitioners AS owner
            WHERE owner.id=v_practitioner_id AND owner.tenant_id=v_tenant_id
              AND owner.active=TRUE
       )
    RETURNING profile.practitioner_id INTO v_updated_id;
    IF v_updated_id IS NULL THEN
        RAISE EXCEPTION 'Practitioner profile is not available to this identity'
            USING ERRCODE='42501';
    END IF;
    RETURN v_updated_id;
END
$body$;
REVOKE ALL ON FUNCTION spps_dev.update_own_practitioner_profile(
    text, integer, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.update_own_practitioner_profile(
    text, integer, text, text) TO spps_dev_app;
COMMIT;
