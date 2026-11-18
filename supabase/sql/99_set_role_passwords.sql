-- SPPS production database: set the four application passwords. Run LAST.
-- 1. Generate four different random passwords (letters and digits only, 32+ characters),
--    for example in a password manager. Do not reuse the Supabase dashboard password.
-- 2. Replace each CHANGE_ME below, run this file once, then DO NOT save it with the real values.
-- 3. Put the same passwords into the four Render database URLs (see DEPLOYMENT.md).
ALTER ROLE spps_dev_app         WITH PASSWORD 'CHANGE_ME_1';
ALTER ROLE spps_dev_auth        WITH PASSWORD 'CHANGE_ME_2';
ALTER ROLE spps_identity_auth   WITH PASSWORD 'CHANGE_ME_3';
ALTER ROLE spps_identity_portal WITH PASSWORD 'CHANGE_ME_4';
