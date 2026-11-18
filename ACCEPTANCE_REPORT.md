# SPPS Version 1.0 - Free — Final acceptance report

Date: 2026-11-17. Scope: the TypeSafe final engineering assessment (risk register P0–P2). Branding, Android package
ids, architecture and feature set are unchanged. No database was started, no hosted SQL was run, nothing was
deployed and no repository was touched in this round, as the assessment required.

Status keys: **Fixed + tested** (code changed, SQLite/API tests run and passing) · **Verified** (inspected, existing
tests cover it, nothing to change) · **Written, PG pending** (PostgreSQL test written, to be run at the
database-testing stage).

## Results of this round (real runs)

| Suite | Result |
|---|---|
| `pytest tests tests_v1 tests_v2` (SQLite / API) | **544 passed, 1 skipped** (was 531; +14 hardening tests, the rest unchanged) |
| `node --test tests_android/*.test.mjs` | **62 passed** |
| `tools/v2_serial_sql.py --check` | serial set in step with `migrations/` (43 + verify + passwords files) |
| `0041` SQL parsed offline with pglast (PostgreSQL 17 parser) | 6 statements, PL/pgSQL body valid |
| `tests_v2_pg` (PostgreSQL row-level security) | **not run this round** (no database start authorised). Last full run before this round: 44 passed. New file adds 2 tests. |

## Risk register

| # | Item | What was found | What changed | Where | Tests | Status |
|---|---|---|---|---|---|---|
| P0-1 | Public report database permissions | Anonymous redemption already goes only through the definer function `v2_open_report_link`, which returns the signed, audience-redacted snapshot and nothing else; the app role sees no clinical rows. Gap: a link kept opening after its practitioner was deactivated, rejected or sent back for review. | Migration **0041** replaces the function with two extra refusals (practitioner inactive; profile not `approved`), outcome logged as `revoked`. The same rule in the API (`practitioner_in_good_standing`). 404 pages are `no-store`, `noindex`. | `migrations/0041_v2_release_hardening.sql`, `server/v2/reports.py` | `test_report_link_stops_when_the_practitioner_is_deactivated…`, `test_report_link_stops_while_the_practitioner_is_not_approved[rejected/pending_approval]`, `tests_v2_pg/test_release_hardening_rls.py` (both tests), existing `test_anonymous_reaches_a_report_only_through_the_function` | Fixed + tested (SQLite); Written, PG pending |
| P0-2 | Report-sharing consent at every access, including after withdrawal/revocation | Every open re-checks expiry, revocation, consent withdrawal and the athlete link, and logs the attempt (existing tests). Gaps: (a) closing a practitioner account left report links and athlete-approved shares open; (b) no test for an expired athlete-approved share. | Closure hooks: on completion every report link is revoked, pending/approved shares are revoked and every athlete-app link is ended. Restoring the account does not bring them back. | `server/v2/trust.py` (`add_closure_hook`, `run_closure_hooks`), `server/v2/reports.py`, `server/v2/athlete_loop.py` | `test_closing_the_account_revokes_every_report_link`, `test_closing_the_account_ends_loop_links_and_closes_every_share`, `test_an_expired_share_returns_nothing…`, existing withdrawn/revoked/ended tests | Fixed + tested |
| P0-3 | Guardian authority before minor activation | Guardian-first flow, account binding and typed signature already existed; the guardian never explicitly declared parental/lawful authority, and could name their own e-mail as the athlete's. | Guardian acceptance now requires a declaration tick box (`guardian_declaration`) and an athlete e-mail different from the guardian's; the stored evidence records the signature and the declaration and is shown to the practitioner (`guardian_signature`). Web form updated; policy B.2 now matches the code. | `server/v2/athlete_loop.py`, `web/athlete.js`, `web/guide.html` | `test_guardian_must_declare_authority_and_name_a_different_account`, `test_a_minor_cannot_use_the_guardian_invitation…`, updated phase 6/8 tests | Fixed + tested |
| P1-1 | Invitation tokens in query strings | Already: `Referrer-Policy: no-referrer`; request logs mask token path segments and drop query strings; invites single use with 14-day expiry and bound to the invited account; verification tokens single use, 2-day expiry. Gaps: tokens stayed in the address bar/history; token pages could be cached. | `web/athlete.js` moves `invite`/`verify` into tab-only storage and clears the address bar before any request; tokens are forgotten once used. All token pages (`/r`, `/s`, `/athlete`, `/portal`, `/room`, `/studio`, `/your-data`) and all `/api` responses are `Cache-Control: no-store` and `noindex, nofollow`. | `web/athlete.js`, `server/main.py` middleware, `server/v2/self_serve.py` | `test_token_pages_are_never_cached_or_indexed_and_send_no_referrer`, `test_athlete_page_removes_tokens_from_the_address_bar`, `test_verification_and_invitation_tokens_are_single_use`, existing `test_invite_tokens_are_single_use…` | Fixed + tested |
| P1-2 | Clinical / identity access boundaries | Role matrix run across anonymous, owning practitioner, other practitioner, athlete, other account, unrelated guardian and platform owner. One finding: another practitioner calling `GET /api/v2/v2-reports/{id}/share-requests` got `200 []` instead of `404` (no data leaked, but it differed from an unknown id). | That endpoint now checks report ownership first and answers `404`. | `server/v2/self_serve.py` | `test_role_matrix_every_role_is_refused_outside_its_own_data`; existing per-phase isolation tests and `tests_v2_pg` RLS tests | Fixed + tested (API); RLS PG pending re-run |
| P1-3 | Cross-browser / e-mail flows | Workflow state is server-side; tests already open every e-mailed link in a fresh client (another browser / Chrome from Android). Token-stripping keeps working after a reload in the same tab. | None beyond P1-1. | — | phase 6/8 tests (`portal_user`, `linked_adult`) | Verified (manual check on a phone remains on the owner's test list) |
| P2-1 | Encryption key rotation docs/test | `tools/v2_rotate_field_key.py` has the four-step procedure; DEPLOYMENT.md links it; `test_key_rotation_re_encrypts_every_row_and_old_key_can_then_be_removed` covers it. | None. | — | phase 2 test | Verified |
| P2-2 | Release documentation without beta labels; Android ids unchanged | User-facing text, package metadata and titles say SPPS Version 1.0 - Free. The remaining `beta15` is the Android package id, kept deliberately (DECISIONS R-2). | Docs updated for 43 SQL files (START-HERE, DEPLOYMENT, README, `v2_verify.sql`), preflight knows 0041. | docs, `server/v2/preflight.py` | `test_release_1_0_free.py`, `test_phase9_release.py` | Fixed + tested |

## Database change

One new migration, `0041_v2_release_hardening.sql` → `supabase/sql/42_0041_v2_release_hardening.sql` (step 43).
It only replaces one function; no table, column or policy changes, so the expected numbers in `v2_verify.sql` stay
the same except `v2 migrations = 9`. The report-links flag now requires it in preflight.

New Supabase project: run `00_…` to `42_…` in filename order, then `v2_verify.sql`, then your edited copy of
`99_set_role_passwords.sql` (see START-HERE.md step 3).

## To reproduce the PostgreSQL tier (database-testing stage, on your own computer)

```
python tools/v2_pg_harness.py up
python -m pytest tests_v2_pg -q
python tools/v2_schema_proof.py
python tools/v2_pg_harness.py down
```

Expected: every `tests_v2_pg` test passes (44 earlier + 2 new), and the schema proof reports the serial set builds a
brand-new empty database identical to the migration set.

## Known limits (not changed, by design)

- An athlete-approved share (`/s/…`) is a copy held in the identity schema; portal code never reads clinical tables,
  so it stops on withdrawal, revocation, expiry, link end and practitioner closure, but not merely because the
  owner switches a practitioner off. Ending that practitioner's links (or closure) stops it.
- A practitioner taking their directory listing offline (v1 "deactivate listing") does not stop their report links;
  it is a listing pause, not an account suspension.
