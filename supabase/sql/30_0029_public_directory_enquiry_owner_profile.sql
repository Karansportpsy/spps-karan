-- SPPS production database, step 30 of the serial set: 0029_public_directory_enquiry_owner_profile
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0029_public_directory_enquiry_owner_profile.sql
BEGIN;
-- 0029: public directory, public enquiry, applicant resubmission, owner-safe profile edit.
--
-- Problem fixed: the restricted application role (spps_dev_app) has no table access for
-- anonymous visitors, so the public "Find a Sport Psychologist" directory, the services list
-- and the public enquiry could not work against the real PostgreSQL roles. No table grant or
-- policy is widened. Instead, five narrow SECURITY DEFINER functions expose exactly the public
-- fields, are callable only by spps_dev_app, and never return e-mail, password hash, review
-- notes, Supabase identity or any clinical data.
--
-- Run only by the privileged migration role (the Supabase SQL editor "postgres" role).

-- 1. Public directory. Eligibility mirrors the application's listing rule: active account,
--    approved status, verified credentials and publication approval. The platform owner
--    (the only account that can hold is_admin, enforced by ck_spps_only_verified_owner_admin)
--    sorts first; every other practitioner follows in stable id order.
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
     ORDER BY (p.is_admin AND pr.supabase_auth_uid IS NOT NULL AND pr.email_verified_at IS NOT NULL) DESC,
              p.id ASC
$dir$;

-- 2. Public photo of a listed practitioner only.
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
$photo$;

-- 3. Public service list (names and descriptions only).
CREATE OR REPLACE FUNCTION spps_dev.public_services(p_tenant_slug text)
RETURNS TABLE (id integer, slug text, title text, description text, duration text, price_label text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $svc$
    SELECT s.id, s.slug::text, s.title::text, s.description, s.duration::text, s.price_label::text
      FROM spps_dev.services s JOIN spps_dev.tenants t ON t.id = s.tenant_id
     WHERE t.slug = p_tenant_slug AND s.active
     ORDER BY s.id
$svc$;

-- 4. Public enquiry. Accepts only listed practitioners and active services; fixed status,
--    consent version and timestamps. Returns the new enquiry id.
CREATE OR REPLACE FUNCTION spps_dev.submit_public_enquiry(
    p_tenant_slug text, p_service_id integer, p_practitioner_id integer,
    p_contact_name text, p_contact_email text, p_contact_phone text,
    p_enquiring_for text, p_sport text, p_support_topic text,
    p_contact_preference text, p_scheduling_note text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $enq$
DECLARE
    v_tenant_id integer;
    v_id integer;
BEGIN
    IF p_contact_name IS NULL OR length(btrim(p_contact_name)) NOT BETWEEN 2 AND 120
       OR p_contact_email IS NULL OR length(p_contact_email) NOT BETWEEN 5 AND 250
       OR p_contact_email !~ '^[^@\s]+@[^@\s]+[.][^@\s]+$'
       OR length(coalesce(p_contact_phone,'')) > 24
       OR p_enquiring_for NOT IN ('myself','my child / dependent','someone else')
       OR p_sport IS NULL OR length(btrim(p_sport)) NOT BETWEEN 2 AND 80
       OR p_support_topic NOT IN ('Confidence','Competition pressure','Attention and focus',
                                  'Injury recovery','Motivation','General enquiry')
       OR p_contact_preference NOT IN ('email','phone')
       OR length(coalesce(p_scheduling_note,'')) > 250
    THEN
        RAISE EXCEPTION 'Invalid public enquiry' USING ERRCODE='22023';
    END IF;
    SELECT id INTO v_tenant_id FROM spps_dev.tenants WHERE slug = p_tenant_slug;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION 'Enquiry tenant unavailable' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM spps_dev.services
                    WHERE id = p_service_id AND tenant_id = v_tenant_id AND active) THEN
        RAISE EXCEPTION 'Service unavailable' USING ERRCODE='22023';
    END IF;
    IF p_practitioner_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM spps_dev.public_practitioner_directory(p_tenant_slug) d
         WHERE d.id = p_practitioner_id) THEN
        RAISE EXCEPTION 'Practitioner unavailable' USING ERRCODE='22023';
    END IF;
    INSERT INTO spps_dev.inquiries
        (tenant_id, practitioner_id, service_id, contact_name, contact_email, contact_phone,
         enquiring_for, sport, support_topic, contact_preference, scheduling_note,
         consent_version, consent_at, status, created_at)
    VALUES
        (v_tenant_id, p_practitioner_id, p_service_id, btrim(p_contact_name), lower(btrim(p_contact_email)),
         nullif(btrim(coalesce(p_contact_phone,'')),''), p_enquiring_for, btrim(p_sport),
         p_support_topic, p_contact_preference, nullif(btrim(coalesce(p_scheduling_note,'')),''),
         'contact-v1', now(), 'new', now())
    RETURNING id INTO v_id;
    RETURN v_id;
END
$enq$;

-- 5. Applicant resubmission after "request more information". Identified only by the
--    Supabase subject the API has already verified. Changes qualification text only, only
--    while the application is still pending, never any approval flag or the account state.
CREATE OR REPLACE FUNCTION spps_dev.resubmit_own_application(
    p_auth_uid uuid, p_qualifications text, p_experience_years integer,
    p_achievements text, p_specialisms text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,spps_dev
AS $resub$
DECLARE
    v_id integer;
    v_tenant_id integer;
BEGIN
    IF p_auth_uid IS NULL
       OR p_qualifications IS NULL OR length(btrim(p_qualifications)) NOT BETWEEN 4 AND 1000
       OR p_experience_years IS NULL OR p_experience_years NOT BETWEEN 0 AND 70
       OR length(coalesce(p_achievements,'')) > 1500
       OR length(coalesce(p_specialisms,'')) > 250
    THEN
        RAISE EXCEPTION 'Invalid application update' USING ERRCODE='22023';
    END IF;
    UPDATE spps_dev.practitioner_profiles AS pr
       SET qualifications = btrim(p_qualifications),
           experience_years = p_experience_years,
           achievements = coalesce(p_achievements,''),
           specialisms = coalesce(p_specialisms,''),
           more_info_requested_at = NULL
      FROM spps_dev.practitioners AS p
     WHERE pr.practitioner_id = p.id
       AND pr.supabase_auth_uid = p_auth_uid::text
       AND pr.status = 'pending_approval'
       AND NOT p.active AND NOT p.is_admin
    RETURNING pr.practitioner_id, p.tenant_id INTO v_id, v_tenant_id;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'No pending application for this identity' USING ERRCODE='42501';
    END IF;
    INSERT INTO spps_dev.audit_events (tenant_id, actor_id, action, object_type, object_id, happened_at)
    VALUES (v_tenant_id, v_id, 'application.resubmitted', 'practitioner', v_id, now());
    RETURN v_id;
END
$resub$;

-- 6. Owner-safe own-profile edit (replaces the 0023 version, same signature). An ordinary
--    practitioner who edits professional credentials is unlisted and unverified until the
--    owner re-approves. The verified platform owner is the reviewer, so the owner's own
--    listing and access are kept; otherwise editing the profile would unlist the owner and
--    block the next sign-in.
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
    v_is_admin boolean;
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
    SELECT owner.is_admin INTO v_is_admin
      FROM spps_dev.practitioners AS owner
     WHERE owner.id = v_practitioner_id AND owner.tenant_id = v_tenant_id AND owner.active = TRUE;
    IF v_is_admin IS NULL THEN
        RAISE EXCEPTION 'Practitioner profile is not available to this identity'
            USING ERRCODE='42501';
    END IF;
    UPDATE spps_dev.practitioner_profiles AS profile
       SET qualifications=p_qualifications,
           experience_years=p_experience_years,
           achievements=p_achievements,
           specialisms=p_specialisms,
           public_approved=CASE WHEN v_is_admin THEN profile.public_approved ELSE FALSE END,
           credentials_verified=CASE WHEN v_is_admin THEN profile.credentials_verified ELSE FALSE END
     WHERE profile.practitioner_id=v_practitioner_id
    RETURNING profile.practitioner_id INTO v_updated_id;
    IF v_updated_id IS NULL THEN
        RAISE EXCEPTION 'Practitioner profile is not available to this identity'
            USING ERRCODE='42501';
    END IF;
    RETURN v_updated_id;
END
$body$;

-- 7. Default public service so the directory's enquiry form works on a new database.
INSERT INTO spps_dev.services (tenant_id, slug, title, description, duration, price_label, active)
SELECT t.id, 'sport-psychology-enquiry', 'Sport psychology consultation enquiry',
       'Ask a practitioner about support with confidence, focus, competition pressure, motivation or return from injury.',
       'To be agreed with the practitioner', 'Free enquiry', TRUE
  FROM spps_dev.tenants t WHERE t.slug = 'winmindperform'
ON CONFLICT (tenant_id, slug) DO NOTHING;

-- 8. A signed-in practitioner may read their own tenant's service titles. The practitioner
--    dashboard and enquiry inbox join each enquiry to its service title; without this the
--    first public enquiry would make the dashboard fail with "permission denied".
--    Anonymous visitors have no tenant context, so they still read services only through
--    public_services(). No write privilege is granted.
GRANT SELECT ON TABLE spps_dev.services TO spps_dev_app;
DROP POLICY IF EXISTS services_same_tenant_read ON spps_dev.services;
CREATE POLICY services_same_tenant_read ON spps_dev.services
    FOR SELECT TO spps_dev_app
    USING (tenant_id = spps_dev.tenant_id());

-- Privileges: only the private application role may call these functions.
REVOKE ALL ON FUNCTION spps_dev.public_practitioner_directory(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION spps_dev.public_practitioner_photo(text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION spps_dev.public_services(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION spps_dev.submit_public_enquiry(
    text, integer, integer, text, text, text, text, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION spps_dev.resubmit_own_application(uuid, text, integer, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION spps_dev.update_own_practitioner_profile(text, integer, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.public_practitioner_directory(text) TO spps_dev_app;
GRANT EXECUTE ON FUNCTION spps_dev.public_practitioner_photo(text, integer) TO spps_dev_app;
GRANT EXECUTE ON FUNCTION spps_dev.public_services(text) TO spps_dev_app;
GRANT EXECUTE ON FUNCTION spps_dev.submit_public_enquiry(
    text, integer, integer, text, text, text, text, text, text, text, text) TO spps_dev_app;
GRANT EXECUTE ON FUNCTION spps_dev.resubmit_own_application(uuid, text, integer, text, text) TO spps_dev_app;
GRANT EXECUTE ON FUNCTION spps_dev.update_own_practitioner_profile(text, integer, text, text) TO spps_dev_app;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0029_public_directory_enquiry_owner_profile.sql','462ebc549d84fb344f22176cd6414157dff5335460b632c618368cb0c849b67d') ON CONFLICT (name) DO NOTHING;
COMMIT;
