# Booking-only Phase 3 — Appointments and Join video

Date: 2026-11-21. Local branch: `phase3-appointments-video`, based on `codex-handoff` at `950dade`.
Scope: Phase 3 only. No push, PR, merge, hosted SQL, Render environment change or deployment.

## Pre-flight

- Working tree was clean; origin was `Karansportpsy/spps-karan`.
- The fetched `origin/main` was an ancestor of the handoff commit. Phase 2, including migration 0043 and its
  follow-up 0044, was present on main. The phase branch was created from the current handoff branch as requested.
- Existing approval-first, reschedule, ticket, consent and room implementations were inspected before coding.
- The owner explicitly authorised tracked env examples and disposable harness credentials only. Existing real
  environment files and secrets were not read, edited or committed.

## Files changed

- Server: `server/bookings.py`, `server/video.py`, `server/v2/preflight.py`.
- Canonical UI: `web/app.js`, `web/portal.js`, `web/styles.css`; matching generated `www/assets/` copies.
  Both staging scripts also regenerated the ignored Android web assets; native identifiers and versions are unchanged.
- SQL: `migrations/0045_v2_appointments_video.sql`, generated `supabase/sql/46_0045_v2_appointments_video.sql`,
  and the read-only `supabase/sql/v2_verify.sql` expectations. No pre-existing numbered migration/serial file changed.
- Configuration/docs: `.env.example`, `.env.production.example`, `render.yaml`, `DEPLOYMENT.md`, `MIGRATIONS.md`, this report.
- Tests: new `tests_v2/test_booking_phase3_browser.py` and `tests_v2_pg/test_booking_phase3_video.py`;
  Phase 2 PG assertions updated for the stricter minimum-data and fixed-reason contract. No tests deleted or skipped.
- Windows PostgreSQL validation: `tools/v2_pg_harness.py`, `tools/v2_schema_proof.py`, `tests_v2_pg/conftest.py`.
  Docker mode uses the sanctioned ephemeral lifecycle and verifies the container and every test URL.

## Resulting behaviour

Practitioners have Pending, Approved, Declined and Past tabs. Rows show the service snapshot, local date/time,
mode and athlete display name. First requests show only display name, service and slot, with action/status
metadata; notes and guardian/payment details are omitted from practitioner API responses. This protection
continues when directory intake is switched off. Decline reasons are optional fixed phrases.

Approval still uses the Phase 2 path: create a minimal practitioner-owned athlete record, activate the pending
directory link, and confirm through the existing free approval function. A proposal retains the service snapshot.
Accepting a proposal for an unapproved request returns it to pending approval; accepting one for a confirmed
appointment keeps it confirmed. Past or declined originals cannot be decided, rescheduled or revived by a proposal response.

The athlete/guardian portal shows My appointments, proposed times and Accept/Decline. Existing guardian authority
controls management and video access. Both clients show Join video for CONFIRMED online appointments only, from
start minus 10 minutes through end plus 15 minutes. Clock-only changes update the button without overwriting forms.

The database enforces the window at ticket issue and ticket reuse. Existing active-link, consent and final-room-closure
checks remain; guest ticket reuse also rechecks current booking authority, so a revoked guardian's old ticket stops
working at status/consent/ICE/room-entry checks. The consent page, `room.html` and signalling design are unchanged.

## Migration, variables, flags and privileges

- New migration: **0045**, expand-only function additions/replacements under `SET LOCAL ROLE spps_identity_owner`
  and `RESET ROLE`. No tables, policies or foreign keys added; existing approval-first transitions are retained.
- New execute grant: only `spps_identity_auth` receives `portal_booking_details(text)`, returning authorised booking
  IDs and service snapshots. PUBLIC is revoked; `spps_identity_portal` receives no direct execute grant.
  Existing role/table grants are not widened.
- Existing env variables documented in tracked examples and the local Render template: `MODEL_C_ICE_URLS`,
  `MODEL_C_TURN_SECRET`, with placeholders only. No real relay credentials were configured.
- No new product feature flags. Bookings/video use `SPPS_PORTAL_ENABLED`; directory intake additionally needs
  `SPPS_DIRECTORY_ENABLED` and `SPPS_DIRECTORY_BOOKING_ENABLED`. Defaults remain off.
- Test-only selection: `SPPS_V2_PG_DOCKER=1`, `SPPS_EPHEMERAL_SLOT=2`, `SPPS_V2_PG_ENV` as documented in DEPLOYMENT.

## Tests and QA

Full `tests tests_v1 tests_v2` run: **570 passed, 3 failed**, 573 collected (baseline 564; 9 browser cases added).
The failures were the existing directory-photo assertion, the flag-off explanatory control removed by the initial
Phase 3 UI, and a Profile & Privacy browser navigation timeout. The flag-off control was restored and all three
unavailable-module tests then passed. The Profile & Privacy suite also passed all three tests on isolated rerun,
without a code change for that timeout. The photo failure remains unchanged as the owner requested.
The full suite was not rerun after the focused fix; **do not describe the full Python suite as green**.

Verified so far:

| Check | Result |
|---|---|
| Full Python unit/API/browser run | 570 passed, 3 failed; focused fix/rerun results below |
| New Phase 3 browser acceptance | 9 passed |
| Existing live-refresh plus Phase 3 browser acceptance | 14 passed |
| Existing flag-off/unavailable-module browser tests after restoring the explanatory control | 3 passed |
| Existing Profile & Privacy browser suite, isolated rerun after navigation timeout | 3 passed |
| Full PostgreSQL tier on a fresh sanctioned Docker cluster | **76 passed** (baseline 58; 18 added) |
| New serial SQL executed under a non-superuser, non-BYPASSRLS installer | Passed in PG tier |
| Serial set versus raw migration schema/history proof | Passed in PG tier |
| Android source checks | **62 passed** |
| Serial generator check, syntax and diff whitespace | Passed |

PostgreSQL coverage includes pending approval/decline, proposal acceptance/decline, first-link activation, terminal
and past-state refusal, another practitioner getting 404, fixed reasons, minimum data with intake off, both ticket
issuers, before/inside/after window, exact boundaries, in-person/unconfirmed refusal, unrelated accounts, and guardian
authority revocation. The disposable cluster and credential files are removed after testing.

Browser plugin not available; QA used the repository's Playwright workflow and real Chromium with fictional mocked
API responses. Viewports: 1280×900 and 390×900; timezone: Asia/Kolkata; frozen clock. The PG tier exercises the real APIs/SQL.

| Rendered QA check | Result |
|---|---|
| URL/title, meaningful content, no error overlay | Passed |
| Page errors and relevant console errors | None in Phase 3 acceptance flows |
| Desktop/mobile screenshots | Inspected; mobile actions visible without sideways scrolling |
| Tabs, fixed decline reason and proposal responses | Passed |
| Join appears/disappears when only the clock changes | Passed |

Known pre-existing issue: `tests/test_admin_directory_ui.py::test_directory_pins_the_owner_and_survives_a_missing_photo`
failed at its photo-fallback assertion. The same failure was reproduced using untouched `950dade` web assets extracted
to a temporary directory. The owner explicitly requested keeping Phase 3 scope and documenting this failure;
directory/photo behaviour and its assertions were left unchanged.

## Exact owner-operated Supabase order

For the existing database already through 0044, after a backup:

1. Run `supabase/sql/46_0045_v2_appointments_video.sql` once, before releasing Phase 3 code.
2. Run `supabase/sql/v2_verify.sql` and compare every value below.
3. Only after verification, the owner can use their separate GitHub/Render release process.

The serial installer needs membership in `spps_identity_owner` and SELECT/INSERT on the migration ledger.
No hosted step above was performed during this task. Do not rerun old serial SQL.

Expected and locally measured verification values:

| Check | Value |
|---|---|
| tables spps_dev / spps_identity_dev | 63 / 36 |
| policies spps_dev / spps_identity_dev | 96 / 96 |
| force_rls_missing | 0 |
| api_roles_with_access | 0 |
| anon_execute_on_definer | 0 |
| v2 migrations | 13 |
| missing v2 tables | (none) |
| login roles | spps_dev_app, spps_dev_auth, spps_identity_auth, spps_identity_portal |

## Risks, owner questions, Next Steps & Improvements

1. Resolve or explicitly accept the documented pre-existing directory-photo test failure before release, then rerun
   the full Python suite and investigate any recurring browser navigation timeout. No additional
   scope decision is pending for Phase 3; the owner chose documentation rather than an unrelated fix.
2. Owner-run test still needed: two fictional accounts on separate networks using a real TURN relay, including
   relay-only connection, consent and final room closure; then repeat in the Android WebView. No live media/network
   quality or physical-device APK claim is made by the mocked browser/API tests. No release APK was built.
3. Ranked regression improvements: DST/other viewer timezones, keyboard tab navigation, and additional physical-device
   accessibility checks. These are follow-ups; no Phase 4 work was started.
4. Security/compliance: maintain consent and guardian authority checks, keep real TURN credentials outside files,
   complete the handoff's owner-operated credential rotation and legal-review prerequisites before real intake.
   Existing two-peer, single-process signalling limitations remain unchanged.
5. Optional QA tooling: install the Browser plugin for in-app browser inspection; this run used repository Playwright.

To reproduce: set `PYTHONUTF8=1`, run `python -m pytest tests tests_v1 tests_v2 -q -p no:cacheprovider`,
the guarded Docker procedure in DEPLOYMENT.md for `tests_v2_pg`, and `node --test tests_android/*.test.mjs`.
Run `node scripts/stage-web.mjs` and `node scripts/sync-android-web.mjs` after any further canonical web edit.
