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
