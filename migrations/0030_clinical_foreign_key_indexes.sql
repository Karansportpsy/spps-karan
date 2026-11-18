BEGIN;
-- 0030: covering indexes for clinical foreign keys that had none.
--
-- Problem fixed: the Supabase performance advisor reported foreign keys without a covering index.
-- Without one, deleting or updating a parent row scans the whole child table, and joins from
-- the child side cannot use an index. Indexes only: no table, grant, policy, function or data
-- is changed, so row-level security and every role's access are exactly as before.
--
-- Run only by the privileged migration role (the Supabase SQL editor "postgres" role).

CREATE INDEX IF NOT EXISTS ix_assignments_case_id ON spps_dev.assignments (case_id);
CREATE INDEX IF NOT EXISTS ix_assignments_intervention_id ON spps_dev.assignments (intervention_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_linked_athlete_id ON spps_dev.inquiries (linked_athlete_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_service_id ON spps_dev.inquiries (service_id);
CREATE INDEX IF NOT EXISTS ix_notes_author_id ON spps_dev.notes (author_id);
CREATE INDEX IF NOT EXISTS ix_observations_case_id ON spps_dev.observations (case_id);
CREATE INDEX IF NOT EXISTS ix_observations_factor_id ON spps_dev.observations (factor_id);
CREATE INDEX IF NOT EXISTS ix_programme_enquiries_programme_id ON spps_dev.programme_enquiries (programme_id);
CREATE INDEX IF NOT EXISTS ix_reports_athlete_id ON spps_dev.reports (athlete_id);
CREATE INDEX IF NOT EXISTS ix_reports_case_id ON spps_dev.reports (case_id);
CREATE INDEX IF NOT EXISTS ix_session_reviews_author_id ON spps_dev.session_reviews (author_id);
COMMIT;
