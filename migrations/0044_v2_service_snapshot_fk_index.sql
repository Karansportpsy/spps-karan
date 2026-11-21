BEGIN;
-- 0044: covering index for the tenant_id foreign key of spps_dev.directory_service_snapshot (created in 0043).
-- Every foreign key in these schemas has a covering index (0030/0031); the end-to-end suite caught this one missing
-- after 0043 had already been applied, so the fix is a new forward-only migration instead of an edit to 0043.
-- Safe to run more than once. Changes no data and no privileges.
CREATE INDEX IF NOT EXISTS ix_directory_service_snapshot_tenant_id ON spps_dev.directory_service_snapshot(tenant_id);
COMMIT;
