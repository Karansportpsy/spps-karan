-- SPPS production database, step 18 of the serial set: 0017_free_clinical_slots
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0017_free_clinical_slots.sql
BEGIN;
-- Beta v1.0: permit free appointments without deleting or rewriting historical payments.
-- Deploy only after validating backup and identity RLS roles. No data is erased.
SET LOCAL search_path=spps_dev;
ALTER TABLE appointment_slots DROP CONSTRAINT IF EXISTS appointment_slots_amount_paise_check;
ALTER TABLE appointment_slots ADD CONSTRAINT ck_spps_slot_nonnegative_amount CHECK (amount_paise >= 0);
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_amount_paise_check;
ALTER TABLE bookings ADD CONSTRAINT ck_spps_booking_nonnegative_amount CHECK (amount_paise >= 0);
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0017_free_clinical_slots.sql','0dcfe1c3af50acebc415df8c30c15714662038a73256146e5eed9ca75f5529ed') ON CONFLICT (name) DO NOTHING;
COMMIT;
