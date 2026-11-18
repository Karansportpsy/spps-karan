-- SPPS production database, step 36 of the serial set: 0035_v2_practitioner_record
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0035_v2_practitioner_record.sql
BEGIN;
-- 0035 (v2 phase 3): athlete profile, note templates, signatures, amendments, server drafts, 5Ps formulation and spreadsheet imports.
--
-- Every table is owned by one practitioner and is visible to that practitioner only (FORCE RLS, tenant and
-- practitioner from the server-set identity). Signatures and amendments are append-only: no UPDATE or DELETE grant.
-- An amendment is accepted only for the caller's own signed (final) note; a signature only once per note.
-- Text columns holding clinical narrative (referral notes, amendments, drafts, formulations, staged imports) are
-- encrypted by the application before they are written (server/v2/fieldcrypt.py); the columns are plain text.
-- v2_erase_athlete_records (0034) is replaced so a purge also removes the new athlete-linked rows.

CREATE TABLE spps_dev.athlete_profiles (
    athlete_id integer PRIMARY KEY REFERENCES spps_dev.athletes(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    level varchar(20) NOT NULL DEFAULT 'not specified',
    age_group varchar(16) NOT NULL DEFAULT 'not specified',
    guardian_name varchar(120) NOT NULL DEFAULT '',
    guardian_email varchar(250) NOT NULL DEFAULT '',
    guardian_phone varchar(24) NOT NULL DEFAULT '',
    consent_status varchar(16) NOT NULL DEFAULT 'not_recorded'
        CHECK (consent_status IN ('not_recorded','recorded','withdrawn')),
    consent_changed_at timestamptz,
    referral_source varchar(120) NOT NULL DEFAULT '',
    referral_notes text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (age_group NOT IN ('under-12','12-14','15-17')
           OR (guardian_name <> '' AND (guardian_email <> '' OR guardian_phone <> '')))
);
CREATE INDEX ix_athlete_profiles_tenant_id ON spps_dev.athlete_profiles(tenant_id);
CREATE INDEX ix_athlete_profiles_practitioner_id ON spps_dev.athlete_profiles(practitioner_id);

CREATE TABLE spps_dev.note_templates (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    name varchar(120) NOT NULL,
    body text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_note_templates_tenant_id ON spps_dev.note_templates(tenant_id);
CREATE INDEX ix_note_templates_practitioner_id ON spps_dev.note_templates(practitioner_id);

CREATE TABLE spps_dev.note_signatures (
    note_id integer PRIMARY KEY REFERENCES spps_dev.notes(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    content_sha256 varchar(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    signed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_note_signatures_tenant_id ON spps_dev.note_signatures(tenant_id);
CREATE INDEX ix_note_signatures_practitioner_id ON spps_dev.note_signatures(practitioner_id);

CREATE TABLE spps_dev.note_amendments (
    id serial PRIMARY KEY,
    note_id integer NOT NULL REFERENCES spps_dev.notes(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    content text NOT NULL,
    reason varchar(240) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_note_amendments_note_id ON spps_dev.note_amendments(note_id);
CREATE INDEX ix_note_amendments_tenant_id ON spps_dev.note_amendments(tenant_id);
CREATE INDEX ix_note_amendments_practitioner_id ON spps_dev.note_amendments(practitioner_id);

CREATE TABLE spps_dev.note_drafts (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    case_id integer NOT NULL REFERENCES spps_dev.cases(id),
    client_draft_id varchar(40) NOT NULL,
    note_date varchar(10) NOT NULL,
    content text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_note_draft_client UNIQUE (practitioner_id, client_draft_id)
);
CREATE INDEX ix_note_drafts_tenant_id ON spps_dev.note_drafts(tenant_id);
CREATE INDEX ix_note_drafts_practitioner_id ON spps_dev.note_drafts(practitioner_id);
CREATE INDEX ix_note_drafts_case_id ON spps_dev.note_drafts(case_id);

CREATE TABLE spps_dev.case_formulations (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer NOT NULL REFERENCES spps_dev.athletes(id),
    version integer NOT NULL CHECK (version >= 1),
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_case_formulation_version UNIQUE (athlete_id, version)
);
CREATE INDEX ix_case_formulations_tenant_id ON spps_dev.case_formulations(tenant_id);
CREATE INDEX ix_case_formulations_practitioner_id ON spps_dev.case_formulations(practitioner_id);
CREATE INDEX ix_case_formulations_athlete_id ON spps_dev.case_formulations(athlete_id);

CREATE TABLE spps_dev.athlete_imports (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    filename varchar(160) NOT NULL,
    status varchar(12) NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','committed','discarded')),
    rows_total integer NOT NULL DEFAULT 0,
    rows_imported integer NOT NULL DEFAULT 0,
    payload text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_athlete_imports_tenant_id ON spps_dev.athlete_imports(tenant_id);
CREATE INDEX ix_athlete_imports_practitioner_id ON spps_dev.athlete_imports(practitioner_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['athlete_profiles','note_templates','note_signatures','note_amendments','note_drafts',
                           'case_formulations','athlete_imports'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- Athlete profile: own athletes only.
GRANT SELECT, INSERT ON spps_dev.athlete_profiles TO spps_dev_app;
GRANT UPDATE (level, age_group, guardian_name, guardian_email, guardian_phone, consent_status, consent_changed_at,
              referral_source, referral_notes, updated_at) ON spps_dev.athlete_profiles TO spps_dev_app;
CREATE POLICY v2_athlete_profile_own ON spps_dev.athlete_profiles TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id()));

GRANT SELECT, INSERT ON spps_dev.note_templates TO spps_dev_app;
GRANT UPDATE (active) ON spps_dev.note_templates TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.note_templates_id_seq TO spps_dev_app;
CREATE POLICY v2_note_templates_own ON spps_dev.note_templates TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

-- Signatures: once, for the caller's own final note. Append-only.
GRANT SELECT, INSERT ON spps_dev.note_signatures TO spps_dev_app;
CREATE POLICY v2_note_signature_read ON spps_dev.note_signatures FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());
CREATE POLICY v2_note_signature_sign_own_final ON spps_dev.note_signatures FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.notes n WHERE n.id = note_id AND n.author_id = spps_dev.practitioner_id()
                AND n.state = 'final'));

-- Amendments: append-only, only for the caller's own signed note.
GRANT SELECT, INSERT ON spps_dev.note_amendments TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.note_amendments_id_seq TO spps_dev_app;
CREATE POLICY v2_note_amendment_read ON spps_dev.note_amendments FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());
CREATE POLICY v2_note_amendment_own_final ON spps_dev.note_amendments FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.notes n WHERE n.id = note_id AND n.author_id = spps_dev.practitioner_id()
                AND n.state = 'final'));

-- Server drafts: the practitioner's own, on their own cases; deleted when committed or discarded.
GRANT SELECT, INSERT, DELETE ON spps_dev.note_drafts TO spps_dev_app;
GRANT UPDATE (case_id, note_date, content, revision, updated_at) ON spps_dev.note_drafts TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.note_drafts_id_seq TO spps_dev_app;
CREATE POLICY v2_note_drafts_own ON spps_dev.note_drafts TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.cases c WHERE c.id = case_id
                AND c.owner_practitioner_id = spps_dev.practitioner_id()));

-- Formulation: versioned, append-only (a new version is a new row).
GRANT SELECT, INSERT ON spps_dev.case_formulations TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.case_formulations_id_seq TO spps_dev_app;
CREATE POLICY v2_case_formulations_own ON spps_dev.case_formulations TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id()));

GRANT SELECT, INSERT ON spps_dev.athlete_imports TO spps_dev_app;
GRANT UPDATE (status, rows_imported, payload) ON spps_dev.athlete_imports TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.athlete_imports_id_seq TO spps_dev_app;
CREATE POLICY v2_athlete_imports_own ON spps_dev.athlete_imports TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

CREATE OR REPLACE FUNCTION spps_dev.v2_erase_athlete_records(p_ids integer[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN RETURN; END IF;
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
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0035_v2_practitioner_record.sql','fc9a30b53a5105aeb33a14fcb0215741fdbe96bf1dfcfb0aa09cd680f4c44fac') ON CONFLICT (name) DO NOTHING;
COMMIT;
