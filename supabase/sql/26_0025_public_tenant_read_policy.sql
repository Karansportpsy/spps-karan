-- SPPS production database, step 26 of the serial set: 0025_public_tenant_read_policy
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0025_public_tenant_read_policy.sql
BEGIN;
-- 0025: Permit the restricted app to read only the seeded public tenant.
-- Public tenant discovery cannot use the privileged migration-owner connection.
-- No INSERT/UPDATE/DELETE grant: owner creation is via the audited SECURITY DEFINER function.
GRANT SELECT (id, slug, display_name) ON TABLE spps_dev.tenants TO spps_dev_app;
DROP POLICY IF EXISTS spps_public_tenant_lookup ON spps_dev.tenants;
CREATE POLICY spps_public_tenant_lookup ON spps_dev.tenants
    FOR SELECT TO spps_dev_app
    USING (slug = 'model-c-demo' OR id = spps_dev.tenant_id());
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0025_public_tenant_read_policy.sql','9e5315217cf5cf72bd41d973d2d5ae75403e5bccd2b17b0043e4f8fdff650ff5') ON CONFLICT (name) DO NOTHING;
COMMIT;
