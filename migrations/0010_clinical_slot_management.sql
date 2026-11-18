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
