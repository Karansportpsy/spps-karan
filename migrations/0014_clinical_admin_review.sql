-- Stage 13D on PostgreSQL: administrator credential/publication review under FORCE RLS.
-- Runs only inside tools.development_db fresh spps_dev* provisioning, after 0010.
-- No table added (33 tables kept). An administrator may READ same-tenant practitioner
-- profiles and UPDATE only credentials_verified/public_approved of OTHER practitioners.
-- Self-review is impossible at the database layer as well as in the API.
SET LOCAL search_path=spps_dev;

-- Minimal definer helpers: return booleans about the server-bound practitioner identity only.
CREATE FUNCTION spps_dev.current_is_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_dev AS $body$
 SELECT COALESCE((SELECT p.is_admin AND p.active FROM spps_dev.practitioners p
   WHERE p.id=spps_dev.practitioner_id() AND p.tenant_id=spps_dev.tenant_id()), false)
$body$;
CREATE FUNCTION spps_dev.same_tenant_practitioner(pid integer) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_dev AS $body$
 SELECT EXISTS (SELECT 1 FROM spps_dev.practitioners p WHERE p.id=pid AND p.tenant_id=spps_dev.tenant_id())
$body$;
REVOKE ALL ON FUNCTION spps_dev.current_is_admin(), spps_dev.same_tenant_practitioner(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.current_is_admin(), spps_dev.same_tenant_practitioner(integer) TO spps_dev_app;

GRANT UPDATE (credentials_verified, public_approved) ON practitioner_profiles TO spps_dev_app;
CREATE POLICY admin_review_profile_read ON practitioner_profiles FOR SELECT TO spps_dev_app
 USING (spps_dev.current_is_admin() AND spps_dev.same_tenant_practitioner(practitioner_id));
CREATE POLICY admin_review_profile_update ON practitioner_profiles FOR UPDATE TO spps_dev_app
 USING (spps_dev.current_is_admin() AND spps_dev.same_tenant_practitioner(practitioner_id)
        AND practitioner_id <> spps_dev.practitioner_id())
 WITH CHECK (spps_dev.current_is_admin() AND spps_dev.same_tenant_practitioner(practitioner_id)
        AND practitioner_id <> spps_dev.practitioner_id());

-- Reviewers can read back their OWN audit decisions (actor-scoped policy already exists).
GRANT SELECT (action, object_type, object_id) ON audit_events TO spps_dev_app;
