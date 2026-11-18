-- SPPS production database, step 38 of the serial set: 0037_v2_reports
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0037_v2_reports.sql
BEGIN;
-- 0037 (v2 phase 5): report templates with audience-tagged fields, signed reports, per-audience sharing consent and expiring, revocable report links.
--
-- * All tables are private to the practitioner who holds the athlete record (FORCE RLS).
-- * A report becomes shareable only when signed: its rendered snapshot (already redacted for ONE audience by the
--   application, and encrypted) is frozen with its hash. Signed reports cannot be edited (only status/values/
--   snapshot/hash columns are updatable, and only while the row is a draft).
-- * Links store only a SHA-256 of the token. Anonymous visitors reach a report only through
--   v2_open_report_link(), which returns the snapshot only while the link is unexpired, unrevoked and its consent
--   is not withdrawn, and records every attempt in report_link_views. No table is readable anonymously.

CREATE TABLE spps_dev.report_templates (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    name varchar(120) NOT NULL,
    fields text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_report_templates_tenant_id ON spps_dev.report_templates(tenant_id);
CREATE INDEX ix_report_templates_practitioner_id ON spps_dev.report_templates(practitioner_id);

CREATE TABLE spps_dev.shared_reports (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer NOT NULL REFERENCES spps_dev.athletes(id),
    template_id integer NOT NULL REFERENCES spps_dev.report_templates(id),
    title varchar(140) NOT NULL,
    audience varchar(12) NOT NULL CHECK (audience IN ('athlete','guardian','coach','medical')),
    status varchar(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','signed')),
    "values" text NOT NULL DEFAULT '{}',
    snapshot text,
    content_sha256 varchar(64) NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1,
    signed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status = 'draft' OR (snapshot IS NOT NULL AND signed_at IS NOT NULL AND content_sha256 ~ '^[0-9a-f]{64}$'))
);
CREATE INDEX ix_shared_reports_tenant_id ON spps_dev.shared_reports(tenant_id);
CREATE INDEX ix_shared_reports_practitioner_id ON spps_dev.shared_reports(practitioner_id);
CREATE INDEX ix_shared_reports_athlete_id ON spps_dev.shared_reports(athlete_id);
CREATE INDEX ix_shared_reports_template_id ON spps_dev.shared_reports(template_id);

CREATE TABLE spps_dev.report_consents (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer NOT NULL REFERENCES spps_dev.athletes(id),
    audience varchar(12) NOT NULL CHECK (audience IN ('athlete','guardian','coach','medical')),
    given_by varchar(10) NOT NULL CHECK (given_by IN ('athlete','guardian')),
    method varchar(12) NOT NULL CHECK (method IN ('written','verbal','portal')),
    note varchar(240) NOT NULL DEFAULT '',
    recorded_at timestamptz NOT NULL DEFAULT now(),
    withdrawn_at timestamptz
);
CREATE INDEX ix_report_consents_tenant_id ON spps_dev.report_consents(tenant_id);
CREATE INDEX ix_report_consents_practitioner_id ON spps_dev.report_consents(practitioner_id);
CREATE INDEX ix_report_consents_athlete_id ON spps_dev.report_consents(athlete_id);

CREATE TABLE spps_dev.report_links (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    report_id integer NOT NULL REFERENCES spps_dev.shared_reports(id),
    consent_id integer NOT NULL REFERENCES spps_dev.report_consents(id),
    token_hash varchar(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    recipient_label varchar(120) NOT NULL DEFAULT '',
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at <= created_at + interval '31 days')
);
CREATE INDEX ix_report_links_tenant_id ON spps_dev.report_links(tenant_id);
CREATE INDEX ix_report_links_practitioner_id ON spps_dev.report_links(practitioner_id);
CREATE INDEX ix_report_links_report_id ON spps_dev.report_links(report_id);
CREATE INDEX ix_report_links_consent_id ON spps_dev.report_links(consent_id);

CREATE TABLE spps_dev.report_link_views (
    id bigserial PRIMARY KEY,
    link_id integer NOT NULL REFERENCES spps_dev.report_links(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    outcome varchar(12) NOT NULL CHECK (outcome IN ('shown','expired','revoked','withdrawn')),
    viewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_report_link_views_link_id ON spps_dev.report_link_views(link_id);
CREATE INDEX ix_report_link_views_tenant_id ON spps_dev.report_link_views(tenant_id);
CREATE INDEX ix_report_link_views_practitioner_id ON spps_dev.report_link_views(practitioner_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['report_templates','shared_reports','report_consents','report_links','report_link_views'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT ON spps_dev.report_templates TO spps_dev_app;
GRANT UPDATE (active) ON spps_dev.report_templates TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.report_templates_id_seq TO spps_dev_app;
CREATE POLICY v2_report_templates_own ON spps_dev.report_templates TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT ON spps_dev.shared_reports TO spps_dev_app;
GRANT UPDATE (status, "values", snapshot, content_sha256, revision, signed_at) ON spps_dev.shared_reports TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.shared_reports_id_seq TO spps_dev_app;
CREATE POLICY v2_shared_reports_read ON spps_dev.shared_reports FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());
CREATE POLICY v2_shared_reports_create ON spps_dev.shared_reports FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND status = 'draft'
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id())
    AND EXISTS (SELECT 1 FROM spps_dev.report_templates t WHERE t.id = template_id
                AND t.practitioner_id = spps_dev.practitioner_id()));
-- Only drafts change; a signed report is frozen.
CREATE POLICY v2_shared_reports_edit_draft ON spps_dev.shared_reports FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND status = 'draft')
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

GRANT SELECT, INSERT ON spps_dev.report_consents TO spps_dev_app;
GRANT UPDATE (withdrawn_at) ON spps_dev.report_consents TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.report_consents_id_seq TO spps_dev_app;
CREATE POLICY v2_report_consents_own ON spps_dev.report_consents TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id()));

GRANT SELECT, INSERT ON spps_dev.report_links TO spps_dev_app;
GRANT UPDATE (revoked_at) ON spps_dev.report_links TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.report_links_id_seq TO spps_dev_app;
CREATE POLICY v2_report_links_read ON spps_dev.report_links FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());
CREATE POLICY v2_report_links_create ON spps_dev.report_links FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND revoked_at IS NULL
    AND EXISTS (SELECT 1 FROM spps_dev.shared_reports r WHERE r.id = report_id AND r.status = 'signed'
                AND r.practitioner_id = spps_dev.practitioner_id())
    AND EXISTS (SELECT 1 FROM spps_dev.report_consents c JOIN spps_dev.shared_reports r ON r.id = report_id
                WHERE c.id = consent_id AND c.withdrawn_at IS NULL AND c.audience = r.audience
                  AND c.athlete_id = r.athlete_id AND c.practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY v2_report_links_revoke ON spps_dev.report_links FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id() AND revoked_at IS NOT NULL);

GRANT SELECT ON spps_dev.report_link_views TO spps_dev_app;
CREATE POLICY v2_report_link_views_own ON spps_dev.report_link_views FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

-- Anonymous link opening: snapshot (ciphertext) or NULL; every attempt on a known link is recorded.
CREATE FUNCTION spps_dev.v2_open_report_link(p_token_hash text)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_l spps_dev.report_links%ROWTYPE; v_outcome text; v_snap text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
    SELECT * INTO v_l FROM spps_dev.report_links WHERE token_hash = p_token_hash;
    IF v_l.id IS NULL THEN RETURN NULL; END IF;
    SELECT CASE WHEN v_l.revoked_at IS NOT NULL THEN 'revoked'
                WHEN v_l.expires_at < now() THEN 'expired'
                WHEN EXISTS (SELECT 1 FROM spps_dev.report_consents c WHERE c.id = v_l.consent_id
                             AND c.withdrawn_at IS NULL) THEN 'shown'
                ELSE 'withdrawn' END INTO v_outcome;
    INSERT INTO spps_dev.report_link_views(link_id,tenant_id,practitioner_id,outcome)
    VALUES (v_l.id, v_l.tenant_id, v_l.practitioner_id, v_outcome);
    IF v_outcome <> 'shown' THEN RETURN NULL; END IF;
    SELECT r.snapshot INTO v_snap FROM spps_dev.shared_reports r WHERE r.id = v_l.report_id AND r.status = 'signed';
    RETURN v_snap;
END $fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_open_report_link(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.v2_open_report_link(text) TO spps_dev_app;

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
    UPDATE spps_dev.inquiries SET linked_athlete_id = NULL WHERE linked_athlete_id = ANY(p_ids);
    DELETE FROM spps_dev.retention_holds WHERE athlete_id = ANY(p_ids) AND NOT active;
    DELETE FROM spps_dev.athletes WHERE id = ANY(p_ids);
END $fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_erase_athlete_records(integer[]) FROM PUBLIC;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0037_v2_reports.sql','58af887d33ce907e6af3aaead7e79d675c88d968d2b66072190e542163c549d6') ON CONFLICT (name) DO NOTHING;
COMMIT;
