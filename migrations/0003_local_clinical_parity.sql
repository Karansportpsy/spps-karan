-- SPPS fictional local PostgreSQL clinical-parity extension.
-- Runs only inside tools.development_db fresh spps_dev* localhost provisioning.
-- NEVER apply to hosted Supabase spps_pilot / production.
SET LOCAL search_path=spps_dev;

-- Reuse the existing restricted application role. Neither role may own tables,
-- inherit privileges, bypass RLS, grant access, or see authentication secrets.
GRANT INSERT ON notes, assignments, reports, injury_episodes, injury_checkins TO spps_dev_app;
GRANT UPDATE (state, revision) ON notes TO spps_dev_app;
GRANT USAGE ON SEQUENCE notes_id_seq, assignments_id_seq, reports_id_seq,
  injury_episodes_id_seq, injury_checkins_id_seq TO spps_dev_app;

-- Consultation notes: author and the selected clinical case must be the same owner.
CREATE POLICY own_note_insert ON notes FOR INSERT TO spps_dev_app
 WITH CHECK (tenant_id=spps_dev.tenant_id() AND author_id=spps_dev.practitioner_id()
  AND EXISTS (SELECT 1 FROM cases c WHERE c.id=notes.case_id AND c.tenant_id=notes.tenant_id));
-- Immutable after finalization. Only state/revision columns are mutable by this role.
CREATE POLICY own_note_finalize ON notes FOR UPDATE TO spps_dev_app
 USING (tenant_id=spps_dev.tenant_id() AND author_id=spps_dev.practitioner_id()
  AND state='draft' AND EXISTS (SELECT 1 FROM cases c WHERE c.id=notes.case_id))
 WITH CHECK (tenant_id=spps_dev.tenant_id() AND author_id=spps_dev.practitioner_id()
  AND state='final' AND revision>=2 AND EXISTS (SELECT 1 FROM cases c WHERE c.id=notes.case_id));

CREATE POLICY own_assignment_insert ON assignments FOR INSERT TO spps_dev_app
 WITH CHECK (tenant_id=spps_dev.tenant_id()
  AND EXISTS (SELECT 1 FROM cases c WHERE c.id=assignments.case_id
    AND c.tenant_id=assignments.tenant_id AND c.athlete_id=assignments.athlete_id)
  AND EXISTS (SELECT 1 FROM interventions i WHERE i.id=assignments.intervention_id
    AND i.tenant_id=assignments.tenant_id AND i.approved));

-- Only private report DRAFT creation, not publication/disclosure or export.
CREATE POLICY own_report_insert ON reports FOR INSERT TO spps_dev_app
 WITH CHECK (tenant_id=spps_dev.tenant_id() AND status='draft'
  AND EXISTS (SELECT 1 FROM cases c WHERE c.id=reports.case_id
    AND c.tenant_id=reports.tenant_id AND c.athlete_id=reports.athlete_id));

CREATE POLICY own_injury_insert ON injury_episodes FOR INSERT TO spps_dev_app
 WITH CHECK (tenant_id=spps_dev.tenant_id()
  AND EXISTS (SELECT 1 FROM cases c WHERE c.id=injury_episodes.case_id
    AND c.tenant_id=injury_episodes.tenant_id AND c.athlete_id=injury_episodes.athlete_id));
CREATE POLICY own_checkin_insert ON injury_checkins FOR INSERT TO spps_dev_app
 WITH CHECK (EXISTS (SELECT 1 FROM injury_episodes e WHERE e.id=injury_checkins.episode_id
   AND e.tenant_id=spps_dev.tenant_id()));

-- No permission to manage payments, memberships, bookings, session/media tokens
-- or public directory records. All 33 tables keep ENABLE/FORCE RLS.
