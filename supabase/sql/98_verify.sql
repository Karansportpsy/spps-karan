-- SPPS production database: verification (read-only). Run after steps 00-33.
-- Expected: 33 / 22 tables, 41 / 67 policies, 13 / 102 functions, force_rls_missing = 0,
-- api_roles_with_access = 0, history = 31, tenant = winmindperform.
SELECT 'tables spps_dev' AS check, count(*)::text AS value FROM pg_tables WHERE schemaname='spps_dev'
UNION ALL SELECT 'tables spps_identity_dev', count(*)::text FROM pg_tables WHERE schemaname='spps_identity_dev'
UNION ALL SELECT 'policies spps_dev', count(*)::text FROM pg_policies WHERE schemaname='spps_dev'
UNION ALL SELECT 'policies spps_identity_dev', count(*)::text FROM pg_policies WHERE schemaname='spps_identity_dev'
UNION ALL SELECT 'functions spps_dev', count(*)::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_dev'
UNION ALL SELECT 'functions spps_identity_dev', count(*)::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='spps_identity_dev'
UNION ALL SELECT 'force_rls_missing', count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND c.relkind='r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
UNION ALL SELECT 'api_roles_with_access', count(*)::text FROM pg_roles r
  WHERE r.rolname IN ('anon','authenticated') AND (has_schema_privilege(r.rolname,'spps_dev','USAGE') OR has_schema_privilege(r.rolname,'spps_identity_dev','USAGE'))
UNION ALL SELECT 'history', count(*)::text FROM spps_ops.migration_history
UNION ALL SELECT 'tenant', string_agg(slug, ',') FROM spps_dev.tenants
UNION ALL SELECT 'status default', column_default FROM information_schema.columns
  WHERE table_schema='spps_dev' AND table_name='practitioner_profiles' AND column_name='status'
UNION ALL SELECT 'login roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles
  WHERE rolname LIKE 'spps%' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls;
