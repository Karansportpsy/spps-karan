-- SPPS production database, step 29 of the serial set: 0028_production_public_tenant
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0028_production_public_tenant.sql
BEGIN;
-- 0028: Production public tenant identity for the phase 1 launch.
-- The hosted database was provisioned with the placeholder staging tenant
-- ("model-c-demo" / "SPPS staging directory"). Rename only that placeholder row;
-- local development databases keep their seeded demo tenant.
-- Forward-only. No clinical rows are touched. Render must set
-- MODEL_C_PUBLIC_TENANT=winmindperform together with this migration.
UPDATE spps_dev.tenants
   SET slug = 'winmindperform', display_name = 'WinMindPerform SPPS'
 WHERE slug = 'model-c-demo' AND display_name = 'SPPS staging directory'
   AND NOT EXISTS (SELECT 1 FROM spps_dev.tenants t WHERE t.slug = 'winmindperform');
DROP POLICY IF EXISTS spps_public_tenant_lookup ON spps_dev.tenants;
CREATE POLICY spps_public_tenant_lookup ON spps_dev.tenants
    FOR SELECT TO spps_dev_app
    USING (slug IN ('winmindperform', 'model-c-demo') OR id = spps_dev.tenant_id());
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0028_production_public_tenant.sql','b785565311c4cfe251854cbcbb8e9368999a28adc44b6415d395606cb92b2274') ON CONFLICT (name) DO NOTHING;
COMMIT;
