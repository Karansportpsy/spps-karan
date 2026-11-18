BEGIN;
-- 0031: covering indexes for platform identity foreign keys that had none.
--
-- Problem fixed: the Supabase performance advisor reported foreign keys without a covering index.
-- Without one, deleting or updating a parent row scans the whole child table, and joins from
-- the child side cannot use an index. Indexes only: no table, grant, policy, function or data
-- is changed, so row-level security and every role's access are exactly as before.
--
-- Run only by the privileged migration role (the Supabase SQL editor "postgres" role).

CREATE INDEX IF NOT EXISTS ix_platform_consent_events_actor_account_id ON spps_identity_dev.platform_consent_events (actor_account_id);
CREATE INDEX IF NOT EXISTS ix_platform_consent_events_guardian_authority_id ON spps_identity_dev.platform_consent_events (guardian_authority_id);
CREATE INDEX IF NOT EXISTS ix_platform_consent_events_supersedes_event_id ON spps_identity_dev.platform_consent_events (supersedes_event_id);
CREATE INDEX IF NOT EXISTS ix_practitioner_athlete_links_consent_event_id ON spps_identity_dev.practitioner_athlete_links (consent_event_id);
COMMIT;
