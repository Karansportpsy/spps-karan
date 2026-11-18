-- SPPS production database, step 43 of the serial set: 0042_v2_appointment_only_directory
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0042_v2_appointment_only_directory.sql
BEGIN;
-- 0042 (appointment-only directory): retires every enquiry object. First contact from the directory is an
-- appointment request (booking), so these are no longer used by the application.
--
-- EXPAND/CONTRACT: deploy the release that no longer reads or writes these objects FIRST, then run this file. The
-- startup checks of that release are name-based (every table the code knows must be FORCE RLS and no table may lack
-- it), so they pass both before and after this file is run.
--
-- SAFETY: if any enquiry row exists, this file stops and drops nothing. Review the rows (as postgres you see all of
-- them), export what you need, delete them on purpose, then run this file again.
--
-- Retires: spps_dev.inquiries (v1 public enquiries), spps_dev.directory_enquiries (v2 directory enquiries),
-- spps_identity_dev.loop_requests (an account's copy of an enquiry), spps_dev.submit_public_enquiry(),
-- spps_dev.v2_owner_enquiry_stats(). Replaces v2_erase_athlete_records() (0037) without its inquiries line, and
-- deactivates the default 'sport-psychology-enquiry' service row (kept, inactive, so history stays valid).

DO $guard$
DECLARE n bigint := 0; c bigint;
BEGIN
    IF to_regclass('spps_dev.inquiries') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM spps_dev.inquiries' INTO c; n := n + c;
    END IF;
    IF to_regclass('spps_dev.directory_enquiries') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM spps_dev.directory_enquiries' INTO c; n := n + c;
    END IF;
    IF to_regclass('spps_identity_dev.loop_requests') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM spps_identity_dev.loop_requests' INTO c; n := n + c;
    END IF;
    IF n > 0 THEN
        RAISE EXCEPTION 'Enquiry rows exist (%). Nothing was dropped. Review, export and delete them deliberately, then run this file again.', n
            USING ERRCODE = 'P0001';
    END IF;
END $guard$;

CREATE OR REPLACE FUNCTION spps_dev.v2_erase_athlete_records(p_ids integer[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN RETURN; END IF;
    DELETE FROM spps_dev.report_link_views v USING spps_dev.report_links l, spps_dev.shared_reports r
     WHERE v.link_id = l.id AND l.report_id = r.id AND r.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.report_links l USING spps_dev.shared_reports r WHERE l.report_id = r.id AND r.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.shared_reports WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.report_consents WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.instrument_responses r USING spps_dev.instrument_administrations a
     WHERE r.administration_id = a.id AND a.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.instrument_scores s USING spps_dev.instrument_administrations a
     WHERE s.administration_id = a.id AND a.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.instrument_administrations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.note_amendments x USING spps_dev.notes n, spps_dev.cases c
     WHERE x.note_id = n.id AND n.case_id = c.id AND c.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.note_signatures x USING spps_dev.notes n, spps_dev.cases c
     WHERE x.note_id = n.id AND n.case_id = c.id AND c.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.note_drafts x USING spps_dev.cases c WHERE x.case_id = c.id AND c.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.case_formulations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.athlete_profiles WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.assessment_subscores s USING spps_dev.assessment_administrations a
     WHERE s.administration_id = a.id AND a.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.assessment_administrations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.injury_checkins c USING spps_dev.injury_episodes e
     WHERE c.episode_id = e.id AND e.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.injury_episodes WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.observations WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.notes n USING spps_dev.cases c WHERE n.case_id = c.id AND c.athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.assignments WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.reports WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.cases WHERE athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.retention_holds WHERE athlete_id = ANY(p_ids) AND NOT active;
    DELETE FROM spps_dev.athletes WHERE id = ANY(p_ids);
END $fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_erase_athlete_records(integer[]) FROM PUBLIC;

DROP FUNCTION IF EXISTS spps_dev.v2_owner_enquiry_stats();
DROP FUNCTION IF EXISTS spps_dev.submit_public_enquiry(text, integer, integer, text, text, text, text, text, text, text, text);
DROP TABLE IF EXISTS spps_dev.directory_enquiries;
DROP TABLE IF EXISTS spps_dev.inquiries;
-- Identity objects belong to spps_identity_owner (postgres may SET to it but is not the owner), as in 0009.
SET LOCAL ROLE spps_identity_owner;
DROP TABLE IF EXISTS spps_identity_dev.loop_requests;
RESET ROLE;

UPDATE spps_dev.services SET active = FALSE WHERE slug = 'sport-psychology-enquiry';
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0042_v2_appointment_only_directory.sql','86145370628676647d457f7b05cea22b6adc21c1480184d69719149d2e886d2d') ON CONFLICT (name) DO NOTHING;
COMMIT;
