-- SPPS production database, step 45 of the serial set: 0044_v2_service_snapshot_fk_index
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0044_v2_service_snapshot_fk_index.sql
BEGIN;
-- 0044: covering index for the tenant_id foreign key of spps_dev.directory_service_snapshot (created in 0043).
-- Every foreign key in these schemas has a covering index (0030/0031); the end-to-end suite caught this one missing
-- after 0043 had already been applied, so the fix is a new forward-only migration instead of an edit to 0043.
-- Safe to run more than once. Changes no data and no privileges.
CREATE INDEX IF NOT EXISTS ix_directory_service_snapshot_tenant_id ON spps_dev.directory_service_snapshot(tenant_id);
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0044_v2_service_snapshot_fk_index.sql','e91868f0c5cd15e39846b8c52b738395834253f011f3c794c73531fc713922a9') ON CONFLICT (name) DO NOTHING;
COMMIT;
