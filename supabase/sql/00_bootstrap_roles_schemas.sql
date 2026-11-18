-- SPPS production database, step 00 of the serial set: bootstrap_roles_schemas
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
BEGIN;

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

-- Restricted roles. Login roles have NO password yet: nobody can connect as them
-- until 99_set_role_passwords.sql is run with your own generated passwords.
DO $$
DECLARE r text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='spps_identity_owner') THEN
    CREATE ROLE spps_identity_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  FOREACH r IN ARRAY ARRAY['spps_dev_app','spps_dev_auth','spps_identity_auth','spps_identity_portal'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS CONNECTION LIMIT 20', r);
    END IF;
  END LOOP;
END $$;
GRANT spps_identity_owner TO postgres WITH SET TRUE, INHERIT FALSE;

CREATE SCHEMA IF NOT EXISTS spps_dev;
CREATE SCHEMA IF NOT EXISTS spps_identity_dev;
CREATE SCHEMA IF NOT EXISTS spps_ops;
REVOKE ALL ON SCHEMA spps_dev, spps_identity_dev, spps_ops FROM PUBLIC;
-- Supabase API roles never see the application schemas.
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('REVOKE ALL ON SCHEMA spps_dev, spps_identity_dev, spps_ops FROM %I', r);
    END IF;
  END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS spps_ops.migration_history (
  name text PRIMARY KEY,
  sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON TABLE spps_ops.migration_history FROM PUBLIC;
COMMIT;
