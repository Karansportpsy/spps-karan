BEGIN;
-- 0036 (v2 phase 4): instruments as data, a custom instrument builder, score entry and sparse-data trends.
--
-- * instruments: platform rows (practitioner_id NULL, readable by every practitioner of the tenant, licence managed
--   by the owner) and practitioner-built rows (private to their author).
-- * Published tests (CSAI-2, SMS-28, POMS, ACSI-28) are seeded below as NAME AND SUBSCALE SCORE ENTRY ONLY, with
--   licence 'licensed_score_entry_only' and licence_confirmed = false until the owner confirms. No item text is
--   shipped, and a trigger refuses any item text for an instrument whose licence is licensed-score-entry-only.
-- * Administrations, responses and scores belong to the practitioner who holds the athlete record. A subscale
--   that was not scored is stored as NULL (missing) and never imputed.

CREATE TABLE spps_dev.instruments (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer REFERENCES spps_dev.practitioners(id),
    name varchar(160) NOT NULL,
    short_name varchar(40) NOT NULL DEFAULT '',
    version varchar(40) NOT NULL DEFAULT '1',
    licence varchar(32) NOT NULL DEFAULT 'own' CHECK (licence IN ('own','open','licensed_score_entry_only')),
    licence_confirmed boolean NOT NULL DEFAULT false,
    validation_status varchar(24) NOT NULL DEFAULT 'practitioner_defined'
        CHECK (validation_status IN ('practitioner_defined','adapted','published','unverified')),
    response_min integer NOT NULL DEFAULT 1,
    response_max integer NOT NULL DEFAULT 5,
    response_labels text NOT NULL DEFAULT '[]',
    assignable_to_athlete boolean NOT NULL DEFAULT false,
    source_note varchar(400) NOT NULL DEFAULT '',
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT (assignable_to_athlete AND licence = 'licensed_score_entry_only')),
    CHECK (practitioner_id IS NULL OR licence IN ('own','open'))
);
CREATE INDEX ix_instruments_tenant_id ON spps_dev.instruments(tenant_id);
CREATE INDEX ix_instruments_practitioner_id ON spps_dev.instruments(practitioner_id);

CREATE TABLE spps_dev.instrument_subscales (
    id serial PRIMARY KEY,
    instrument_id integer NOT NULL REFERENCES spps_dev.instruments(id),
    key varchar(40) NOT NULL,
    label varchar(120) NOT NULL,
    scoring varchar(8) NOT NULL DEFAULT 'sum' CHECK (scoring IN ('sum','mean')),
    min_score double precision NOT NULL,
    max_score double precision NOT NULL,
    bands text NOT NULL DEFAULT '[]',
    position integer NOT NULL DEFAULT 0,
    CONSTRAINT uq_instrument_subscale_key UNIQUE (instrument_id, key)
);
CREATE INDEX ix_instrument_subscales_instrument_id ON spps_dev.instrument_subscales(instrument_id);

CREATE TABLE spps_dev.instrument_items (
    id serial PRIMARY KEY,
    instrument_id integer NOT NULL REFERENCES spps_dev.instruments(id),
    position integer NOT NULL,
    text text,
    subscale_key varchar(40) NOT NULL,
    reverse_scored boolean NOT NULL DEFAULT false
);
CREATE INDEX ix_instrument_items_instrument_id ON spps_dev.instrument_items(instrument_id);

CREATE TABLE spps_dev.instrument_administrations (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    athlete_id integer NOT NULL REFERENCES spps_dev.athletes(id),
    instrument_id integer NOT NULL REFERENCES spps_dev.instruments(id),
    administered_on varchar(10) NOT NULL,
    mode varchar(16) NOT NULL CHECK (mode IN ('items','subscale_entry')),
    source varchar(16) NOT NULL DEFAULT 'practitioner' CHECK (source IN ('practitioner','athlete')),
    note varchar(500) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_instrument_administrations_tenant_id ON spps_dev.instrument_administrations(tenant_id);
CREATE INDEX ix_instrument_administrations_practitioner_id ON spps_dev.instrument_administrations(practitioner_id);
CREATE INDEX ix_instrument_administrations_athlete_id ON spps_dev.instrument_administrations(athlete_id);
CREATE INDEX ix_instrument_administrations_instrument_id ON spps_dev.instrument_administrations(instrument_id);

CREATE TABLE spps_dev.instrument_responses (
    id serial PRIMARY KEY,
    administration_id integer NOT NULL REFERENCES spps_dev.instrument_administrations(id),
    item_id integer NOT NULL REFERENCES spps_dev.instrument_items(id),
    value double precision NOT NULL,
    CONSTRAINT uq_instrument_response_item UNIQUE (administration_id, item_id)
);
CREATE INDEX ix_instrument_responses_administration_id ON spps_dev.instrument_responses(administration_id);
CREATE INDEX ix_instrument_responses_item_id ON spps_dev.instrument_responses(item_id);

CREATE TABLE spps_dev.instrument_scores (
    id serial PRIMARY KEY,
    administration_id integer NOT NULL REFERENCES spps_dev.instrument_administrations(id),
    subscale_key varchar(40) NOT NULL,
    score double precision,
    band varchar(80) NOT NULL DEFAULT '',
    CONSTRAINT uq_instrument_score_subscale UNIQUE (administration_id, subscale_key)
);
CREATE INDEX ix_instrument_scores_administration_id ON spps_dev.instrument_scores(administration_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['instruments','instrument_subscales','instrument_items','instrument_administrations',
                           'instrument_responses','instrument_scores'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- A licensed (score-entry-only) instrument never holds item text, whoever writes the row.
CREATE FUNCTION spps_dev.v2_no_item_text_for_licensed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF NEW.text IS NOT NULL AND EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = NEW.instrument_id
                                         AND i.licence = 'licensed_score_entry_only') THEN
        RAISE EXCEPTION 'Item text is not stored for a licensed instrument' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_no_item_text_for_licensed() FROM PUBLIC;
CREATE TRIGGER v2_no_item_text_for_licensed BEFORE INSERT OR UPDATE ON spps_dev.instrument_items
  FOR EACH ROW EXECUTE FUNCTION spps_dev.v2_no_item_text_for_licensed();

-- Instruments: platform rows readable by the tenant; own rows private. Only the owner changes a platform licence.
GRANT SELECT, INSERT ON spps_dev.instruments TO spps_dev_app;
GRANT UPDATE (active, assignable_to_athlete, licence, licence_confirmed) ON spps_dev.instruments TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.instruments_id_seq TO spps_dev_app;
CREATE POLICY v2_instruments_read ON spps_dev.instruments FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND spps_dev.practitioner_id() IS NOT NULL
         AND (practitioner_id IS NULL OR practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY v2_instruments_build_own ON spps_dev.instruments FOR INSERT TO spps_dev_app
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
              AND licence IN ('own','open'));
CREATE POLICY v2_instruments_update_own ON spps_dev.instruments FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
              AND licence IN ('own','open'));
CREATE POLICY v2_instruments_owner_licence ON spps_dev.instruments FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id IS NULL AND spps_dev.current_is_admin())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id IS NULL AND spps_dev.current_is_admin());

GRANT SELECT, INSERT ON spps_dev.instrument_subscales, spps_dev.instrument_items TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.instrument_subscales_id_seq, spps_dev.instrument_items_id_seq TO spps_dev_app;
CREATE POLICY v2_subscales_read ON spps_dev.instrument_subscales FOR SELECT TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = instrument_id));
CREATE POLICY v2_subscales_build ON spps_dev.instrument_subscales FOR INSERT TO spps_dev_app
  WITH CHECK (EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = instrument_id
                      AND i.practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY v2_items_read ON spps_dev.instrument_items FOR SELECT TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = instrument_id));
CREATE POLICY v2_items_build ON spps_dev.instrument_items FOR INSERT TO spps_dev_app
  WITH CHECK (EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = instrument_id
                      AND i.practitioner_id = spps_dev.practitioner_id()));

GRANT SELECT, INSERT ON spps_dev.instrument_administrations, spps_dev.instrument_responses,
                        spps_dev.instrument_scores TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.instrument_administrations_id_seq, spps_dev.instrument_responses_id_seq,
                        spps_dev.instrument_scores_id_seq TO spps_dev_app;
CREATE POLICY v2_administrations_own ON spps_dev.instrument_administrations TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
    AND EXISTS (SELECT 1 FROM spps_dev.athletes a WHERE a.id = athlete_id
                AND a.owner_practitioner_id = spps_dev.practitioner_id())
    AND EXISTS (SELECT 1 FROM spps_dev.instruments i WHERE i.id = instrument_id));
CREATE POLICY v2_responses_own ON spps_dev.instrument_responses TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM spps_dev.instrument_administrations a WHERE a.id = administration_id))
  WITH CHECK (EXISTS (SELECT 1 FROM spps_dev.instrument_administrations a WHERE a.id = administration_id
                      AND a.practitioner_id = spps_dev.practitioner_id()));
CREATE POLICY v2_scores_own ON spps_dev.instrument_scores TO spps_dev_app
  USING (EXISTS (SELECT 1 FROM spps_dev.instrument_administrations a WHERE a.id = administration_id))
  WITH CHECK (EXISTS (SELECT 1 FROM spps_dev.instrument_administrations a WHERE a.id = administration_id
                      AND a.practitioner_id = spps_dev.practitioner_id()));

-- Published instruments, score entry only (names and subscale ranges; no item text).
WITH t AS (SELECT id FROM spps_dev.tenants WHERE slug = 'winmindperform'),
     i AS (INSERT INTO spps_dev.instruments(tenant_id,practitioner_id,name,short_name,version,licence,licence_confirmed,
                                            validation_status,response_min,response_max,assignable_to_athlete,source_note)
           SELECT t.id,NULL,'Competitive State Anxiety Inventory-2','CSAI-2','published','licensed_score_entry_only',false,
                  'published',0,0,false,'Score entry only. Item text is not provided. Licensing to be confirmed by the platform owner.' FROM t RETURNING id)
INSERT INTO spps_dev.instrument_subscales(instrument_id,key,label,scoring,min_score,max_score,bands,position)
SELECT i.id, s.key, s.label, 'sum', s.lo, s.hi, '[]', s.pos FROM i,
    (VALUES ('cognitive_anxiety','Cognitive anxiety',9,36,0),
           ('somatic_anxiety','Somatic anxiety',9,36,1),
           ('self_confidence','Self-confidence',9,36,2)) AS s(key,label,lo,hi,pos);

WITH t AS (SELECT id FROM spps_dev.tenants WHERE slug = 'winmindperform'),
     i AS (INSERT INTO spps_dev.instruments(tenant_id,practitioner_id,name,short_name,version,licence,licence_confirmed,
                                            validation_status,response_min,response_max,assignable_to_athlete,source_note)
           SELECT t.id,NULL,'Sport Motivation Scale','SMS-28','published','licensed_score_entry_only',false,
                  'published',0,0,false,'Score entry only. Item text is not provided. Licensing to be confirmed by the platform owner.' FROM t RETURNING id)
INSERT INTO spps_dev.instrument_subscales(instrument_id,key,label,scoring,min_score,max_score,bands,position)
SELECT i.id, s.key, s.label, 'sum', s.lo, s.hi, '[]', s.pos FROM i,
    (VALUES ('im_to_know','Intrinsic motivation – to know',4,28,0),
           ('im_accomplishment','Intrinsic motivation – toward accomplishment',4,28,1),
           ('im_stimulation','Intrinsic motivation – to experience stimulation',4,28,2),
           ('em_identified','Extrinsic motivation – identified',4,28,3),
           ('em_introjected','Extrinsic motivation – introjected',4,28,4),
           ('em_external','Extrinsic motivation – external regulation',4,28,5),
           ('amotivation','Amotivation',4,28,6)) AS s(key,label,lo,hi,pos);

WITH t AS (SELECT id FROM spps_dev.tenants WHERE slug = 'winmindperform'),
     i AS (INSERT INTO spps_dev.instruments(tenant_id,practitioner_id,name,short_name,version,licence,licence_confirmed,
                                            validation_status,response_min,response_max,assignable_to_athlete,source_note)
           SELECT t.id,NULL,'Profile of Mood States','POMS','published','licensed_score_entry_only',false,
                  'published',0,0,false,'Score entry only. Item text is not provided. Licensing to be confirmed by the platform owner.' FROM t RETURNING id)
INSERT INTO spps_dev.instrument_subscales(instrument_id,key,label,scoring,min_score,max_score,bands,position)
SELECT i.id, s.key, s.label, 'sum', s.lo, s.hi, '[]', s.pos FROM i,
    (VALUES ('tension','Tension–anxiety',0,36,0),
           ('depression','Depression–dejection',0,60,1),
           ('anger','Anger–hostility',0,48,2),
           ('vigour','Vigour–activity',0,32,3),
           ('fatigue','Fatigue–inertia',0,28,4),
           ('confusion','Confusion–bewilderment',0,28,5)) AS s(key,label,lo,hi,pos);

WITH t AS (SELECT id FROM spps_dev.tenants WHERE slug = 'winmindperform'),
     i AS (INSERT INTO spps_dev.instruments(tenant_id,practitioner_id,name,short_name,version,licence,licence_confirmed,
                                            validation_status,response_min,response_max,assignable_to_athlete,source_note)
           SELECT t.id,NULL,'Athletic Coping Skills Inventory-28','ACSI-28','published','licensed_score_entry_only',false,
                  'published',0,0,false,'Score entry only. Item text is not provided. Licensing to be confirmed by the platform owner.' FROM t RETURNING id)
INSERT INTO spps_dev.instrument_subscales(instrument_id,key,label,scoring,min_score,max_score,bands,position)
SELECT i.id, s.key, s.label, 'sum', s.lo, s.hi, '[]', s.pos FROM i,
    (VALUES ('coping_adversity','Coping with adversity',0,12,0),
           ('coachability','Coachability',0,12,1),
           ('concentration','Concentration',0,12,2),
           ('confidence_motivation','Confidence and achievement motivation',0,12,3),
           ('goal_setting','Goal setting and mental preparation',0,12,4),
           ('peaking','Peaking under pressure',0,12,5),
           ('freedom_worry','Freedom from worry',0,12,6),
           ('total','Personal coping resources (total)',0,84,7)) AS s(key,label,lo,hi,pos);

CREATE OR REPLACE FUNCTION spps_dev.v2_erase_athlete_records(p_ids integer[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN RETURN; END IF;
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
COMMIT;
