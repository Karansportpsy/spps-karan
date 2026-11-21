BEGIN;
-- 0048 (fix): the owner's "Publish" in Admin -> Directory failed with HTTP 500 "permission denied for table practitioners".
--
-- Cause: the INSERT policy v2_directory_snapshot_owner_publish (0039) ran a subquery on spps_dev.practitioners, and a
-- policy expression runs with the privileges of the application role, which (correctly) has no SELECT on practitioners.
-- The same subquery also compared the practitioner against itself (p.id = pr.practitioner_id) instead of the row being
-- published, so it only proved that SOME approved practitioner existed.
--
-- Fix: a minimal SECURITY DEFINER helper, in the style of current_is_admin() (0014), answers one boolean question about
-- one same-tenant practitioner id; the policy calls it with the id of the row being inserted. No table privilege is
-- added to any role. EXPAND-ONLY in effect: one new function, one policy replaced with a stricter, correct one.
-- Run this file BEFORE the owner publishes again; no code deploy is needed for it.

CREATE FUNCTION spps_dev.directory_publishable(pid integer) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_dev AS $body$
 SELECT EXISTS (SELECT 1 FROM spps_dev.practitioners p
                JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id
                WHERE p.id = pid AND p.tenant_id = spps_dev.tenant_id() AND p.active AND pr.status = 'approved')
$body$;
REVOKE ALL ON FUNCTION spps_dev.directory_publishable(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.directory_publishable(integer) TO spps_dev_app;

DROP POLICY IF EXISTS v2_directory_snapshot_owner_publish ON spps_dev.directory_snapshot;
CREATE POLICY v2_directory_snapshot_owner_publish ON spps_dev.directory_snapshot FOR INSERT TO spps_dev_app
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id()
              AND spps_dev.directory_publishable(practitioner_id));
COMMIT;
