-- SPPS production database: v2 verification (read-only). Run after steps 00-42, as the last step (98_verify.sql is the v1 check;
-- its expected numbers describe a v1-only database). Every row should read as the comment beside it says.
-- Expected: tables 61 / 36 after 0042 (63 / 37 before), policies 95 / 98, force_rls_missing = 0, api_roles_with_access = 0,
-- anon_execute_on_definer = 0,
-- v2 migrations = 9, missing v2 tables = (none).
SELECT 'tables spps_dev' AS check, count(*)::text AS value FROM pg_tables WHERE schemaname='spps_dev'
UNION ALL SELECT 'tables spps_identity_dev', count(*)::text FROM pg_tables WHERE schemaname='spps_identity_dev'
UNION ALL SELECT 'policies spps_dev', count(*)::text FROM pg_policies WHERE schemaname='spps_dev'
UNION ALL SELECT 'policies spps_identity_dev', count(*)::text FROM pg_policies WHERE schemaname='spps_identity_dev'
UNION ALL SELECT 'force_rls_missing', count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND c.relkind='r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
UNION ALL SELECT 'api_roles_with_access', count(*)::text FROM pg_roles r
  WHERE r.rolname IN ('anon','authenticated') AND (has_schema_privilege(r.rolname,'spps_dev','USAGE') OR has_schema_privilege(r.rolname,'spps_identity_dev','USAGE'))
UNION ALL SELECT 'anon_execute_on_definer', count(*)::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND p.prosecdef
    AND (p.proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))
UNION ALL SELECT 'v2 migrations', count(*)::text FROM spps_ops.migration_history WHERE name ~ '^00(3[3-9]|4[0-9])_v2_'
UNION ALL SELECT 'missing v2 tables', coalesce(string_agg(t, ','), '(none)') FROM unnest(ARRAY[
  'spps_dev.registration_applications','spps_dev.record_access_log','spps_dev.athlete_profiles','spps_dev.instruments',
  'spps_dev.shared_reports','spps_dev.report_links','spps_dev.directory_profiles','spps_dev.directory_snapshot',
  'spps_identity_dev.loop_accounts','spps_identity_dev.loop_links',
  'spps_identity_dev.loop_share_requests']) t WHERE to_regclass(t) IS NULL
UNION ALL SELECT 'login roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles
  WHERE rolname LIKE 'spps%' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls;
