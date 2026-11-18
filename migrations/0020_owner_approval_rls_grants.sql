-- SPPS staged clinical database, AFTER migrations/0014 and 0019.
-- Narrow rights for approved-owner review actions; no anonymous grants.
BEGIN;
GRANT UPDATE (status, review_note, more_info_requested_at, approved_at,
              approval_email_sent_at, credentials_verified, public_approved)
  ON TABLE spps_dev.practitioner_profiles TO spps_dev_app;

-- Server's separate auth role can activate/deactivate only a same-tenant,
-- non-self practitioner when called as an already authenticated admin.
GRANT EXECUTE ON FUNCTION spps_dev.current_is_admin(),
  spps_dev.same_tenant_practitioner(integer) TO spps_dev_auth;
GRANT UPDATE (active) ON TABLE spps_dev.practitioners TO spps_dev_auth;
DROP POLICY IF EXISTS admin_approval_practitioner_active ON spps_dev.practitioners;
CREATE POLICY admin_approval_practitioner_active
  ON spps_dev.practitioners FOR UPDATE TO spps_dev_auth
  USING (
    spps_dev.current_is_admin()
    AND spps_dev.same_tenant_practitioner(id)
    AND id <> spps_dev.practitioner_id()
  )
  WITH CHECK (
    spps_dev.current_is_admin()
    AND spps_dev.same_tenant_practitioner(id)
    AND id <> spps_dev.practitioner_id()
  );
COMMIT;
