-- SPPS production database, step 20 of the serial set: 0019_practitioner_approval
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0019_practitioner_approval.sql
BEGIN;
-- SPPS hosted clinical schema (spps_dev) companion migration to Supabase spps_pilot approval gate.
-- Run with the designated migration role after the staged hosted database backup.
SET LOCAL search_path=spps_dev;
ALTER TABLE spps_dev.practitioner_profiles
  ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS supabase_auth_uid varchar(36),
  ADD COLUMN IF NOT EXISTS email_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS review_note text,
  ADD COLUMN IF NOT EXISTS more_info_requested_at timestamptz,
  ADD COLUMN IF NOT EXISTS approved_at timestamptz,
  ADD COLUMN IF NOT EXISTS approval_email_sent_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS uq_spps_dev_practitioner_auth_uid
  ON spps_dev.practitioner_profiles(supabase_auth_uid) WHERE supabase_auth_uid IS NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_spps_dev_practitioner_review_status'
    AND conrelid='spps_dev.practitioner_profiles'::regclass) THEN
    ALTER TABLE spps_dev.practitioner_profiles ADD CONSTRAINT ck_spps_dev_practitioner_review_status
    CHECK (status IN ('pending','approved','rejected'));
  END IF;
END $$;
-- Existing non-owner practitioners remain pending until explicit owner review.
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0019_practitioner_approval.sql','68190971950fe55d927c6e5509c79a874dbc27bd4b545a17b4bae22b6ea94e66') ON CONFLICT (name) DO NOTHING;
COMMIT;
