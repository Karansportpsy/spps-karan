# End-to-end tests (opt-in)

These run the **real production code path** — startup validation, release preflight, restricted database roles, forced
row-level security, the SECURITY DEFINER functions, cookies, CSRF and the approval workflow — against a throwaway
local PostgreSQL built from `supabase/sql/` (steps 00–33). Only Supabase Auth is replaced by an in-process fake.

They check, in one story:

- the owner is the default administrator, has both workspaces, and is listed first in the directory automatically;
- an unconfirmed address, or another identity using the owner address, cannot become the owner;
- nobody else can be an administrator — at the API, and in the database (CHECK constraint, restricted role);
- new registrations are pending, hidden, unable to sign in, and cannot receive public enquiries;
- request-more-info, applicant reply, approval, rejection, edit-returns-to-review, and owner-keeps-listing behave as specified;
- public enquiries reach only the chosen practitioner, and the honeypot stores nothing;
- the owner cannot see another practitioner's athletes (API and row-level security);
- the serial SQL set has the expected shape (33/22 tables, 41 policies, 13 functions, 31 history rows, forced RLS everywhere).

## Run

```
tests_e2e/pg_tls_cluster.sh            # starts a throwaway PostgreSQL with TLS on 127.0.0.1:55440, prints two export lines
export SPPS_E2E_ADMIN_URL=...           # as printed
export SPPS_E2E_DESTROY_SCHEMAS=yes
python -m pytest tests_e2e -q -p no:cacheprovider
```

Run it in its own pytest process (not together with `tests/`), because the app reads its environment at import time.
Without `SPPS_E2E_ADMIN_URL` every test is skipped.

**Safety:** a run drops the `spps_dev`, `spps_identity_dev` and `spps_ops` schemas in the target database first. The harness
refuses any host except `127.0.0.1`/`localhost`/`::1` and any URL containing `supabase`. Never point it at a real project.
Tests are order-dependent by design (they share one database and one story).
