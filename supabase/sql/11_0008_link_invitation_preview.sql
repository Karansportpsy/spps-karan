-- SPPS production database, step 11 of the serial set: 0008_link_invitation_preview
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0008_link_invitation_preview.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
-- Stage 13A.8: let a signed-in athlete/guardian see WHICH practitioner issued a code
-- before recording CASE_LINK consent. Does not consume the code. Throttled by the API.
CREATE FUNCTION spps_identity_dev.portal_preview_invitation(p_token_hash text, p_code_hash text)
 RETURNS integer LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); v_practitioner integer;
BEGIN
 IF NOT is_sha256_hex(p_code_hash) THEN RAISE EXCEPTION 'invalid code' USING ERRCODE='P0001'; END IF;
 SELECT practitioner_id INTO v_practitioner FROM platform_link_invitations
  WHERE code_hash=p_code_hash AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>now();
 IF v_practitioner IS NULL THEN RAISE EXCEPTION 'invalid code' USING ERRCODE='P0001'; END IF;
 RETURN v_practitioner;
END $body$;
ALTER FUNCTION spps_identity_dev.portal_preview_invitation(text,text) OWNER TO spps_identity_owner;
REVOKE ALL ON FUNCTION spps_identity_dev.portal_preview_invitation(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spps_identity_dev.portal_preview_invitation(text,text) TO spps_identity_auth;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0008_link_invitation_preview.sql','16de0f838ff3a7d6f55f8a988989eb044f6c8e7209423f4f1d7b42be8897effc') ON CONFLICT (name) DO NOTHING;
COMMIT;
