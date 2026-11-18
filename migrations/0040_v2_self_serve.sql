BEGIN;
-- 0040 (v2 phase 8): self-serve athlete and guardian accounts — account-first enquiries, accepting an enquiry into a record and an invitation, switching practitioner, account deletion, athlete-approved report links.
--
-- Identity schema (spps_identity_dev), same two roles as 0038:
--   * loop_requests: an athlete/guardian account's own copy of an enquiry it sent through the directory. The
--     account sees only its own rows. Nothing about the account reaches a practitioner except what the enquiry
--     itself says, and nothing else is shared until a link is accepted on both sides (0038 rules).
--   * loop_share_requests: a practitioner asks the athlete (the guardian, for under-18s) to approve sending one
--     signed, coach-level report snapshot to a coach or club; only an approved, unexpired, unrevoked request on a
--     link that has not ended opens. loop_share_views audits every attempt.
--   * spps_identity_auth may now delete an account's identity data (account, sessions, unused invitations, its
--     enquiry copies) for self-service deletion.
-- Clinical schema: directory_enquiries gains a reference shared with the account's copy, the athlete record it
-- became, and the 'accepted' / 'declined' outcomes.

CREATE TABLE spps_identity_dev.loop_requests (
    id serial PRIMARY KEY,
    account_id integer NOT NULL REFERENCES spps_identity_dev.loop_accounts(id),
    tenant_id integer NOT NULL,
    practitioner_id integer NOT NULL,
    practitioner_name varchar(120) NOT NULL,
    ref varchar(64) NOT NULL UNIQUE,
    sport varchar(80) NOT NULL,
    message varchar(600) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','accepted','declined','withdrawn')),
    link_id integer REFERENCES spps_identity_dev.loop_links(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz
);
CREATE INDEX ix_loop_requests_account_id ON spps_identity_dev.loop_requests(account_id);

CREATE TABLE spps_identity_dev.loop_share_requests (
    id serial PRIMARY KEY,
    link_id integer NOT NULL REFERENCES spps_identity_dev.loop_links(id),
    tenant_id integer NOT NULL,
    practitioner_id integer NOT NULL,
    report_id integer NOT NULL,
    audience varchar(12) NOT NULL,
    title varchar(140) NOT NULL,
    recipient_label varchar(120) NOT NULL DEFAULT '',
    snapshot text NOT NULL,
    approver_role varchar(10) NOT NULL CHECK (approver_role IN ('athlete','guardian')),
    expires_in_days integer NOT NULL CHECK (expires_in_days BETWEEN 1 AND 30),
    status varchar(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','declined','withdrawn','revoked')),
    token_hash varchar(64) UNIQUE,
    decided_by_account integer,
    decided_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'approved' OR (token_hash IS NOT NULL AND expires_at IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX ix_loop_share_requests_link_id ON spps_identity_dev.loop_share_requests(link_id);

CREATE TABLE spps_identity_dev.loop_share_views (
    id serial PRIMARY KEY,
    share_id integer NOT NULL REFERENCES spps_identity_dev.loop_share_requests(id),
    outcome varchar(12) NOT NULL CHECK (outcome IN ('shown','expired','revoked','withdrawn','ended')),
    viewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_loop_share_views_share_id ON spps_identity_dev.loop_share_views(share_id);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_requests','loop_share_requests','loop_share_views'] LOOP
    EXECUTE format('ALTER TABLE spps_identity_dev.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE spps_identity_dev.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON spps_identity_dev.%I FROM PUBLIC', t);
  END LOOP;
END $$;

-- spps_identity_auth (server only): practitioner outcomes on account enquiries, public opening of approved report
-- links by token, and self-service deletion.
GRANT SELECT, INSERT, UPDATE, DELETE ON spps_identity_dev.loop_requests TO spps_identity_auth;
GRANT SELECT ON spps_identity_dev.loop_share_requests TO spps_identity_auth;
GRANT UPDATE (status, decided_at) ON spps_identity_dev.loop_share_requests TO spps_identity_auth;
GRANT SELECT, INSERT ON spps_identity_dev.loop_share_views TO spps_identity_auth;
GRANT USAGE ON SEQUENCE spps_identity_dev.loop_share_views_id_seq TO spps_identity_auth;
GRANT DELETE ON spps_identity_dev.loop_accounts, spps_identity_dev.loop_sessions, spps_identity_dev.loop_invites
  TO spps_identity_auth;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_requests','loop_share_requests','loop_share_views'] LOOP
    EXECUTE format('CREATE POLICY v2_selfserve_auth_server ON spps_identity_dev.%I TO spps_identity_auth USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

-- spps_identity_portal: an account sees and writes only its own enquiry copies.
GRANT SELECT, INSERT, UPDATE ON spps_identity_dev.loop_requests TO spps_identity_portal;
GRANT USAGE ON SEQUENCE spps_identity_dev.loop_requests_id_seq TO spps_identity_portal;
CREATE POLICY v2_loop_requests_own ON spps_identity_dev.loop_requests TO spps_identity_portal
  USING (account_id = spps_identity_dev.v2_loop_account())
  WITH CHECK (account_id = spps_identity_dev.v2_loop_account());

-- Report sharing: the practitioner who holds the link creates and revokes; the approver (athlete, or guardian
-- for under-18s) reads and decides while the link is open.
GRANT SELECT, INSERT ON spps_identity_dev.loop_share_requests TO spps_identity_portal;
GRANT UPDATE (status, token_hash, decided_by_account, decided_at, expires_at)
  ON spps_identity_dev.loop_share_requests TO spps_identity_portal;
GRANT USAGE ON SEQUENCE spps_identity_dev.loop_share_requests_id_seq TO spps_identity_portal;
GRANT SELECT ON spps_identity_dev.loop_share_views TO spps_identity_portal;
CREATE POLICY v2_share_practitioner ON spps_identity_dev.loop_share_requests TO spps_identity_portal
  USING (spps_identity_dev.v2_loop_role(link_id) = 'practitioner')
  WITH CHECK (spps_identity_dev.v2_loop_role(link_id) = 'practitioner'
              AND practitioner_id = spps_identity_dev.v2_loop_practitioner());
CREATE POLICY v2_share_practitioner_new ON spps_identity_dev.loop_share_requests AS RESTRICTIVE FOR INSERT
  TO spps_identity_portal WITH CHECK (status = 'pending' AND token_hash IS NULL AND decided_at IS NULL);
CREATE FUNCTION spps_identity_dev.v2_share_is_approver(p_link integer, p_role text) RETURNS boolean
  LANGUAGE sql STABLE SET search_path=pg_catalog,spps_identity_dev AS $$
  SELECT (p_role = 'athlete' AND spps_identity_dev.v2_loop_role(p_link) = 'athlete')
      OR (p_role = 'guardian' AND spps_identity_dev.v2_loop_role(p_link) IN ('guardian','guardian_detail')) $$;
REVOKE ALL ON FUNCTION spps_identity_dev.v2_share_is_approver(integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.v2_share_is_approver(integer, text)
  TO spps_identity_portal, spps_identity_auth;
CREATE POLICY v2_share_approver_read ON spps_identity_dev.loop_share_requests FOR SELECT TO spps_identity_portal
  USING (spps_identity_dev.v2_share_is_approver(link_id, approver_role));
CREATE POLICY v2_share_approver_decide ON spps_identity_dev.loop_share_requests FOR UPDATE TO spps_identity_portal
  USING (status IN ('pending','approved') AND spps_identity_dev.v2_share_is_approver(link_id, approver_role))
  WITH CHECK (spps_identity_dev.v2_share_is_approver(link_id, approver_role));
-- Only the approver approves, declines or withdraws; the practitioner can only revoke; the server's auth role
-- withdraws only when the approver deletes their account. Anything else is refused whatever the policies allow.
CREATE FUNCTION spps_identity_dev.v2_share_transition() RETURNS trigger LANGUAGE plpgsql
  SET search_path=pg_catalog,spps_identity_dev AS $fn$
BEGIN
  IF NEW.status = OLD.status THEN
    IF NEW.token_hash IS DISTINCT FROM OLD.token_hash OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
      RAISE EXCEPTION 'report share: link details change only on approval' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'pending' AND NEW.status = 'approved' THEN
    IF NOT spps_identity_dev.v2_share_is_approver(NEW.link_id, NEW.approver_role)
       OR NEW.decided_by_account IS DISTINCT FROM spps_identity_dev.v2_loop_account() THEN
      RAISE EXCEPTION 'report share: only the athlete (guardian for under-18s) approves' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF OLD.status IN ('pending','approved') AND NEW.status = 'withdrawn' AND current_user = 'spps_identity_auth' THEN
    NULL;  -- account deletion
  ELSIF (OLD.status = 'pending' AND NEW.status = 'declined') OR (OLD.status = 'approved' AND NEW.status = 'withdrawn') THEN
    IF NOT spps_identity_dev.v2_share_is_approver(NEW.link_id, NEW.approver_role) THEN
      RAISE EXCEPTION 'report share: only the approver declines or withdraws' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF OLD.status IN ('pending','approved') AND NEW.status = 'revoked' THEN
    IF spps_identity_dev.v2_loop_role(NEW.link_id) IS DISTINCT FROM 'practitioner' THEN
      RAISE EXCEPTION 'report share: only the practitioner revokes' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSE
    RAISE EXCEPTION 'report share: % -> % is not allowed', OLD.status, NEW.status USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION spps_identity_dev.v2_share_transition() FROM PUBLIC;
CREATE TRIGGER v2_share_transition BEFORE UPDATE ON spps_identity_dev.loop_share_requests
  FOR EACH ROW EXECUTE FUNCTION spps_identity_dev.v2_share_transition();
CREATE POLICY v2_share_views_practitioner ON spps_identity_dev.loop_share_views FOR SELECT TO spps_identity_portal
  USING (EXISTS (SELECT 1 FROM spps_identity_dev.loop_share_requests s WHERE s.id = share_id));

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['loop_requests','loop_share_requests','loop_share_views'] LOOP
    EXECUTE format('ALTER TABLE spps_identity_dev.%I OWNER TO spps_identity_owner', t);
  END LOOP;
END $$;

-- Clinical schema: enquiry outcomes.
ALTER TABLE spps_dev.directory_enquiries ADD COLUMN ref varchar(64), ADD COLUMN athlete_id integer;
ALTER TABLE spps_dev.directory_enquiries DROP CONSTRAINT directory_enquiries_status_check,
  ADD CONSTRAINT directory_enquiries_status_check CHECK (status IN ('new','responded','closed','accepted','declined'));
CREATE UNIQUE INDEX ix_directory_enquiries_ref ON spps_dev.directory_enquiries(ref) WHERE ref IS NOT NULL;
GRANT UPDATE (athlete_id) ON spps_dev.directory_enquiries TO spps_dev_app;
COMMIT;
