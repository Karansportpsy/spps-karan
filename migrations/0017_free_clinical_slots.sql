-- Beta v1.0: permit free appointments without deleting or rewriting historical payments.
-- Deploy only after validating backup and identity RLS roles. No data is erased.
BEGIN;
SET LOCAL search_path=spps_dev;
ALTER TABLE appointment_slots DROP CONSTRAINT IF EXISTS appointment_slots_amount_paise_check;
ALTER TABLE appointment_slots ADD CONSTRAINT ck_spps_slot_nonnegative_amount CHECK (amount_paise >= 0);
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_amount_paise_check;
ALTER TABLE bookings ADD CONSTRAINT ck_spps_booking_nonnegative_amount CHECK (amount_paise >= 0);
COMMIT;
