BEGIN;
-- 0041 (v2 release 1.0 - Free): a practitioner's report links stop opening as soon as that practitioner is deactivated,
-- closes their account, or is rejected or sent back for review by the platform owner. Replaces
-- v2_open_report_link() from 0037; every other rule is unchanged (unexpired, unrevoked, consent not withdrawn, signed
-- snapshot only, every attempt recorded in report_link_views).

CREATE OR REPLACE FUNCTION spps_dev.v2_open_report_link(p_token_hash text)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,spps_dev AS $fn$
DECLARE v_l spps_dev.report_links%ROWTYPE; v_outcome text; v_snap text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
    SELECT * INTO v_l FROM spps_dev.report_links WHERE token_hash = p_token_hash;
    IF v_l.id IS NULL THEN RETURN NULL; END IF;
    SELECT CASE WHEN v_l.revoked_at IS NOT NULL THEN 'revoked'
                WHEN NOT EXISTS (SELECT 1 FROM spps_dev.practitioners p
                                 WHERE p.id = v_l.practitioner_id AND p.active) THEN 'revoked'
                WHEN EXISTS (SELECT 1 FROM spps_dev.practitioner_profiles pp
                             WHERE pp.practitioner_id = v_l.practitioner_id AND pp.status <> 'approved') THEN 'revoked'
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
COMMIT;
