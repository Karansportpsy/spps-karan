-- 0025: Permit the restricted app to read only the seeded public tenant.
-- Public tenant discovery cannot use the privileged migration-owner connection.
-- No INSERT/UPDATE/DELETE grant: owner creation is via the audited SECURITY DEFINER function.
BEGIN;
GRANT SELECT (id, slug, display_name) ON TABLE spps_dev.tenants TO spps_dev_app;
DROP POLICY IF EXISTS spps_public_tenant_lookup ON spps_dev.tenants;
CREATE POLICY spps_public_tenant_lookup ON spps_dev.tenants
    FOR SELECT TO spps_dev_app
    USING (slug = 'model-c-demo' OR id = spps_dev.tenant_id());
COMMIT;
