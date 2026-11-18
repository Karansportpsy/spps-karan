-- SPPS production database, step 04 of the serial set: 0010_clinical_slot_management
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0010_clinical_slot_management.sql
BEGIN;
-- Stage 13B clinical extension: practitioners manage ONLY their own appointment slots.
-- Runs only inside tools.development_db fresh spps_dev* localhost provisioning, after 0003.
-- NEVER apply to hosted Supabase spps_pilot / production without review. No table added:
-- the clinical schema keeps exactly 33 FORCE RLS tables. No DELETE (history is retained);
-- slots are withdrawn by setting available=false.
SET LOCAL search_path=spps_dev;

GRANT INSERT ON appointment_slots TO spps_dev_app;
GRANT UPDATE (available) ON appointment_slots TO spps_dev_app;
GRANT USAGE ON SEQUENCE appointment_slots_id_seq TO spps_dev_app;

CREATE POLICY own_slot_insert ON appointment_slots FOR INSERT TO spps_dev_app
 WITH CHECK (practitioner_id=spps_dev.practitioner_id() AND starts_at>now() AND available);
CREATE POLICY own_slot_withdraw ON appointment_slots FOR UPDATE TO spps_dev_app
 USING (practitioner_id=spps_dev.practitioner_id())
 WITH CHECK (practitioner_id=spps_dev.practitioner_id());
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0010_clinical_slot_management.sql','1a5f6cb3e279c6e07de43bf23c538cdafa2d4e1594ce908b0100e96d3d53ce37') ON CONFLICT (name) DO NOTHING;
COMMIT;
