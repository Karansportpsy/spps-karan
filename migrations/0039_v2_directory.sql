BEGIN;
-- 0039 (v2 phase 7): opt-in public directory profiles, the public read model (directory_snapshot) and directory enquiries.
--
-- * directory_profiles: the practitioner's own draft public profile; the owner reads it to decide publication.
-- * directory_snapshot: written only by the owner when publishing; the ONLY table public endpoints read. Readable
--   by anyone through the application role because it holds public fields only.
-- * A practitioner whose account is deactivated or no longer approved is removed from the snapshot by trigger, so
--   an unapproved, suspended, rejected or closed practitioner never appears, whatever path made the change.
-- * directory_enquiries: anonymous inserts only for a listed practitioner; the practitioner reads their own; the
--   owner sees response-time counts only (v2_owner_enquiry_stats), never names, contacts or messages.

CREATE TABLE spps_dev.directory_profiles (
    practitioner_id integer PRIMARY KEY REFERENCES spps_dev.practitioners(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    display_name varchar(120) NOT NULL DEFAULT '',
    qualifications varchar(600) NOT NULL DEFAULT '',
    sports varchar(300) NOT NULL DEFAULT '',
    athlete_levels varchar(200) NOT NULL DEFAULT '',
    age_groups varchar(200) NOT NULL DEFAULT '',
    languages varchar(200) NOT NULL DEFAULT '',
    delivery varchar(10) NOT NULL DEFAULT 'online' CHECK (delivery IN ('online','in_person','both')),
    city varchar(80) NOT NULL DEFAULT '',
    taking_new_clients boolean NOT NULL DEFAULT true,
    about varchar(1200) NOT NULL DEFAULT '',
    status varchar(12) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','published','delisted')),
    submitted_at timestamptz,
    owner_note varchar(240) NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_directory_profiles_tenant_id ON spps_dev.directory_profiles(tenant_id);

CREATE TABLE spps_dev.directory_snapshot (
    practitioner_id integer PRIMARY KEY REFERENCES spps_dev.practitioners(id),
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    display_name varchar(120) NOT NULL,
    qualifications varchar(600) NOT NULL,
    sports varchar(300) NOT NULL,
    athlete_levels varchar(200) NOT NULL,
    age_groups varchar(200) NOT NULL,
    languages varchar(200) NOT NULL,
    delivery varchar(10) NOT NULL,
    city varchar(80) NOT NULL,
    taking_new_clients boolean NOT NULL,
    about varchar(1200) NOT NULL,
    photo bytea,
    badges text NOT NULL,
    verified_until varchar(10) NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_directory_snapshot_tenant_id ON spps_dev.directory_snapshot(tenant_id);

CREATE TABLE spps_dev.directory_enquiries (
    id serial PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES spps_dev.tenants(id),
    practitioner_id integer NOT NULL REFERENCES spps_dev.practitioners(id),
    name varchar(120) NOT NULL,
    contact varchar(250) NOT NULL,
    role varchar(10) NOT NULL CHECK (role IN ('athlete','parent','coach')),
    sport varchar(80) NOT NULL,
    message varchar(600) NOT NULL,
    status varchar(12) NOT NULL DEFAULT 'new' CHECK (status IN ('new','responded','closed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    responded_at timestamptz,
    account_id integer
);
CREATE INDEX ix_directory_enquiries_tenant_id ON spps_dev.directory_enquiries(tenant_id);
CREATE INDEX ix_directory_enquiries_practitioner_id ON spps_dev.directory_enquiries(practitioner_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['directory_profiles','directory_snapshot','directory_enquiries'] LOOP
    EXECUTE format('ALTER TABLE spps_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT ON spps_dev.directory_profiles TO spps_dev_app;
GRANT UPDATE (display_name, qualifications, sports, athlete_levels, age_groups, languages, delivery, city,
              taking_new_clients, about, status, submitted_at, owner_note, updated_at)
  ON spps_dev.directory_profiles TO spps_dev_app;
CREATE POLICY v2_directory_profile_own ON spps_dev.directory_profiles TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id()
              AND status IN ('draft','submitted','delisted'));
CREATE POLICY v2_directory_profile_owner_read ON spps_dev.directory_profiles FOR SELECT TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());
CREATE POLICY v2_directory_profile_owner_decide ON spps_dev.directory_profiles FOR UPDATE TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id())
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON spps_dev.directory_snapshot TO spps_dev_app;
CREATE POLICY v2_directory_snapshot_public_read ON spps_dev.directory_snapshot FOR SELECT TO spps_dev_app USING (true);
CREATE POLICY v2_directory_snapshot_owner_publish ON spps_dev.directory_snapshot FOR INSERT TO spps_dev_app
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id()
    AND EXISTS (SELECT 1 FROM spps_dev.practitioners p JOIN spps_dev.practitioner_profiles pr ON pr.practitioner_id = p.id
                WHERE p.id = practitioner_id AND p.active AND pr.status = 'approved'));
CREATE POLICY v2_directory_snapshot_owner_update ON spps_dev.directory_snapshot FOR UPDATE TO spps_dev_app
  USING (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id())
  WITH CHECK (spps_dev.current_is_admin() AND tenant_id = spps_dev.tenant_id());
CREATE POLICY v2_directory_snapshot_remove ON spps_dev.directory_snapshot FOR DELETE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id()
         AND (practitioner_id = spps_dev.practitioner_id() OR spps_dev.current_is_admin()));

GRANT SELECT, INSERT ON spps_dev.directory_enquiries TO spps_dev_app;
GRANT UPDATE (status, responded_at) ON spps_dev.directory_enquiries TO spps_dev_app;
GRANT USAGE ON SEQUENCE spps_dev.directory_enquiries_id_seq TO spps_dev_app;
CREATE POLICY v2_directory_enquiry_public_insert ON spps_dev.directory_enquiries FOR INSERT TO spps_dev_app
  WITH CHECK (status = 'new' AND responded_at IS NULL
    AND EXISTS (SELECT 1 FROM spps_dev.directory_snapshot s WHERE s.practitioner_id = directory_enquiries.practitioner_id
                AND s.tenant_id = directory_enquiries.tenant_id AND s.verified_until >= to_char(now(), 'YYYY-MM-DD')));
CREATE POLICY v2_directory_enquiry_own ON spps_dev.directory_enquiries FOR SELECT TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());
CREATE POLICY v2_directory_enquiry_respond ON spps_dev.directory_enquiries FOR UPDATE TO spps_dev_app
  USING (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id())
  WITH CHECK (tenant_id = spps_dev.tenant_id() AND practitioner_id = spps_dev.practitioner_id());

CREATE FUNCTION spps_dev.v2_owner_enquiry_stats()
RETURNS TABLE (practitioner_id integer, enquiries bigint, responded bigint, unanswered bigint, median_response_hours numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
    SELECT e.practitioner_id, count(*), count(e.responded_at), count(*) - count(e.responded_at),
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM e.responded_at - e.created_at) / 3600)
                  FILTER (WHERE e.responded_at IS NOT NULL))::numeric, 1)
      FROM spps_dev.directory_enquiries e
     WHERE spps_dev.current_is_admin() AND e.tenant_id = spps_dev.tenant_id()
     GROUP BY e.practitioner_id
$fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_owner_enquiry_stats() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_dev.v2_owner_enquiry_stats() TO spps_dev_app;

-- Whatever path deactivates or un-approves a practitioner, the public listing goes with it.
CREATE FUNCTION spps_dev.v2_directory_unlist() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF NOT NEW.active THEN
        DELETE FROM spps_dev.directory_snapshot WHERE practitioner_id = NEW.id;
    END IF;
    RETURN NEW;
END $fn$;
CREATE FUNCTION spps_dev.v2_directory_unlist_profile() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
BEGIN
    IF NEW.status <> 'approved' THEN
        DELETE FROM spps_dev.directory_snapshot WHERE practitioner_id = NEW.practitioner_id;
    END IF;
    RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION spps_dev.v2_directory_unlist(), spps_dev.v2_directory_unlist_profile() FROM PUBLIC;
CREATE TRIGGER v2_directory_unlist_inactive AFTER UPDATE OF active ON spps_dev.practitioners
  FOR EACH ROW EXECUTE FUNCTION spps_dev.v2_directory_unlist();
CREATE TRIGGER v2_directory_unlist_unapproved AFTER UPDATE OF status ON spps_dev.practitioner_profiles
  FOR EACH ROW EXECUTE FUNCTION spps_dev.v2_directory_unlist_profile();
COMMIT;
