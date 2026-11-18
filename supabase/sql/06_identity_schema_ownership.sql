-- SPPS production database, step 06 of the serial set: identity_schema_ownership
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
BEGIN;
GRANT CREATE, USAGE ON SCHEMA spps_identity_dev TO spps_identity_owner;
DO $$
DECLARE r record;
BEGIN
 FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='spps_identity_dev' LOOP
  EXECUTE format('ALTER TABLE spps_identity_dev.%I OWNER TO spps_identity_owner',r.tablename);
 END LOOP;
END $$;
ALTER SCHEMA spps_identity_dev OWNER TO spps_identity_owner;
COMMIT;
