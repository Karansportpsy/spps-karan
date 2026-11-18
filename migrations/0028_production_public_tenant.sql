-- 0028: Production public tenant identity for the phase 1 launch.
-- The hosted database was provisioned with the placeholder staging tenant
-- ("model-c-demo" / "SPPS staging directory"). Rename only that placeholder row;
-- local development databases keep their seeded demo tenant.
-- Forward-only. No clinical rows are touched. Render must set
-- MODEL_C_PUBLIC_TENANT=winmindperform together with this migration.
BEGIN;
UPDATE spps_dev.tenants
   SET slug = 'winmindperform', display_name = 'WinMindPerform SPPS'
 WHERE slug = 'model-c-demo' AND display_name = 'SPPS staging directory'
   AND NOT EXISTS (SELECT 1 FROM spps_dev.tenants t WHERE t.slug = 'winmindperform');
DROP POLICY IF EXISTS spps_public_tenant_lookup ON spps_dev.tenants;
CREATE POLICY spps_public_tenant_lookup ON spps_dev.tenants
    FOR SELECT TO spps_dev_app
    USING (slug IN ('winmindperform', 'model-c-demo') OR id = spps_dev.tenant_id());
COMMIT;
