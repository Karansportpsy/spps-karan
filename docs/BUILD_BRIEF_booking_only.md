# SPPS build brief: booking-only directory, services, appointments, notifications

Read this whole file before changing anything. Work one phase at a time. Stop at the end of each phase, run the checks, and wait for approval before starting the next.

## Status (2026-11-18)
- Phase 1 (enquiry purge) is DONE and sits uncommitted in the working tree: 543 unit tests and 45 PostgreSQL tests pass.
  `tests/test_free_practitioner_enquiry.py` must be deleted (git rm). Migration 0042 runs only after the new code is live.
- Phase 2 is next. Default decision unless the owner says otherwise: an athlete's first booking with an unlinked
  practitioner creates the 13A practitioner-athlete link (consent shown at booking; guardian consent for under-18s);
  the booking still needs practitioner approval before it is confirmed.
- Open question for the owner: purge programme enquiries / organisation quote requests too? (default: keep)
- Test commands: `python -m pytest tests tests_v1 tests_v2 -q -p no:cacheprovider`; PostgreSQL tier via
  `python tools/v2_pg_harness.py up` then `tests_v2_pg`. Regenerate web copies after any web/ change:
  `node scripts/stage-web.mjs` and `node scripts/sync-android-web.mjs`.

## Ground rules (apply to every phase)

1. Work on a new git branch per phase. Never commit to `main`.
2. Migrations are forward-only. Never edit an existing file in `migrations/` or `supabase/sql/`. Add the next number (current highest is 0041; check before numbering). Mirror each new migration into the generator-built `supabase/sql` set, and update `v2_verify.sql` / `98_verify.sql` if objects change.
3. Do not run SQL against Supabase, deploy, or push. Only edit local files and commit locally. The owner runs hosted SQL and pushes.
4. Keep the privacy model: clinical data in `spps_dev`, identity data in `spps_identity_dev`, forced RLS, SECURITY DEFINER functions for identity actions, restricted roles. Do not widen any role grant without writing it down in the phase report.
5. Notifications and e-mails stay content-free. No names, clinical text, amounts or tokens in push or e-mail.
6. Minors: a booking for an under-18 athlete must go through the existing guardian consent and authority logic. Do not bypass it.
7. Never print, log or hard-code the owner e-mail, passwords, keys or tokens. New secrets come from environment variables and are listed in `.env.production.example`, `render.yaml` and `DEPLOYMENT.md`.
8. New routes must be added to the default-deny allowlist (`server/v2/allowlist.py`) and gated by a feature flag in `server/v2/flags.py` where they are client-facing. Keep flags off by default.
9. Every phase ends with: tests added or updated, full test suite run (SQLite tier, and the PostgreSQL tier if available locally), a short report listing files changed, migrations added, env vars added, and any risk.
10. Update the web files in `web/` only. `www/` and the Android assets are generated copies; regenerate them with the project's existing script, do not hand-edit.

## Findings from code inspection (these correct earlier assumptions; read before Phase 1)

1. **Three separate enquiry systems exist, not one.**
   - v1 public enquiry: `POST /api/public/enquiries`, the practitioner inbox `/api/enquiries` (list, status, convert to athlete), the `Inquiry` model / `inquiries` table, DB function `spps_dev.submit_public_enquiry` (0029), `public_services`, the dashboard's `new_enquiries` and `enquiries` keys, and the **Enquiries** menu item. This is the one the workspace menu shows.
   - v2 directory enquiry: `server/v2/directory.py`, table `directory_enquiries`, function `v2_owner_enquiry_stats`, admin "Enquiry response times".
   - v2 self-serve: `server/v2/self_serve.py` is built on enquiry acceptance (accept enquiry creates the athlete record and invitation) and on `loop_requests` in the identity schema. Only parts 1-3 are enquiry; parts 4-5 (account deactivate/export/delete, athlete-approved report links) must survive the purge.
   - Programme enquiries / organisation quotes (`marketplace_routes.py`) are a fourth, separate feature; default is to leave them.
2. **Two identity systems.** Bookings, video and `portal.js` use the 13A identity tables (`platform_accounts`, `practitioner_athlete_links`, `platform_bookings`, guardian authorities). The athlete loop and self-serve use the v2 `loop_*` tables (`loop_accounts`, `loop_links`). Directory-to-booking (Phase 2) must target the 13A system, because that is where bookings and video live. Decide explicitly how a loop account and a 13A account relate (merge, or booking creates a 13A account) before building.
3. **A tenant-level `services` table already exists** (title, description, duration, price label). It is not per-practitioner. Phase 2 needs a new per-practitioner service table, and must not repurpose the tenant one.
4. **Table-count invariants block a naive drop.** `inquiries` is one of the pinned 33 clinical tables. The number 33 is hard-coded in `server/identity.py`, the hosted startup check in `server/main.py`, `tools/hosted_db.py`, `tools/verify_full_stack.py`, the recovery drill test, `98_verify.sql` and several tests. If the code expects 32 while the live database still has 33 (or the reverse), startup fails. Use expand/contract: first release stops using enquiry and makes the startup check derive its expectation from the ORM (every ORM table exists and is FORCE RLS, no schema table lacks FORCE RLS), then run the drop migration after that release is live. Do not just change 33 to 32.
5. **`server/main.py` has the owner e-mail written as a literal** in the Supabase exchange route, and the privacy hook blocks Bash commands that touch that file. Edit it with the Edit tool, never print the address, and replace the literal with the `OWNER_EMAIL` constant from `server/owner_profile.py` in Phase 5.
6. **Test rules.** `CLAUDE.md` forbids weakening or skipping security and RLS tests. Deleting tests that cover a removed feature is allowed, but list every deleted test file in the phase report. About 15 test files reference enquiry; `tests_v2/test_phase8_self_serve.py` and `tests/test_model_c.py` are the largest.
7. **Verification is possible locally.** `python tools/v2_pg_harness.py up` starts a throwaway PostgreSQL 16 on 127.0.0.1:55437 and applies `supabase/sql`; then `python -m pytest tests_v2_pg -q`. Baseline on the exported build: 545 tests pass in `tests tests_v1 tests_v2`, 46 pass in `tests_v2_pg`. Every phase must keep both green.

## Phase 1: Enquiry purge

Goal: no enquiry feature anywhere. Booking is the only way to contact a practitioner from the directory.

Steps, in order:
1. Inventory every reference to enquiry (code, UI text, tests, docs, flags, preflight entries, policy and terms pages, guide). Write the list into the phase report first.
2. Remove routes, UI (including the **Enquiries** menu item, the enquiry form on the directory card, the enquiry response-time stats in admin) and tests.
3. Remove the enquiry flags and preflight entries. `SPPS_SELF_SERVE_ENABLED` depends on directory enquiry acceptance: decide with the owner whether self-serve is redesigned in Phase 2 or removed; do not silently delete it.
4. Remove enquiry wording from `privacy.html`, `terms.html`, `policies.html`, `guide.html`.
5. Programme enquiries and organisation quote requests (`marketplace_routes.py`, `ProgrammeEnquiry`, `OrganisationQuoteRequest`): ask the owner whether these are in or out of the purge. Default: out of scope unless told otherwise.
6. Add ONE new forward-only migration that drops the enquiry tables, functions, policies and views (including `directory_enquiries`, `v2_owner_enquiry_stats` and anything created in 0029, 0039, 0040 for enquiries). Before dropping, the migration must be safe to run once and must not fail if an object is already gone (`IF EXISTS`).
7. Provide a read-only SQL query the owner can run first to count existing enquiry rows. Do not drop on a non-empty table without the owner confirming.
8. Update the generator's SQL set so a fresh install has no enquiry objects.

Done when: grep for "enquir" returns nothing outside old migration files, historical docs, and anything the owner explicitly kept; tests pass; app starts.

## Phase 2: Service catalogue and directory booking

Goal: practitioners list services; athletes and stakeholders choose a practitioner, pick a service and request an appointment.

1. New table for services in the clinical schema (practitioner-owned, forced RLS): name, short description, duration in minutes, mode (online / in person), active flag, sort order. No price column (payments are off).
2. Practitioner API and UI (Settings or Appointments): add, edit, reorder, deactivate services.
3. Public directory card shows active services (public read through the existing public-directory path only; do not expose clinical tables).
4. Booking request from the directory:
   - A visitor must sign in or create a portal account first (e-mail verification required).
   - Request carries: practitioner, service, preferred slot from published availability, a short free-text note (length-capped, no clinical detail prompted).
   - If the person is not yet linked, create the minimal link with status `pending` only after the practitioner approves; until then the practitioner sees only the minimum needed (display name, service, slot).
   - Under-18: require guardian authority and consent through the existing portal_guardian flow before the request can be submitted or approved.
5. Reuse `bookings.py` and the identity functions (`free_portal_request_booking`, `free_practitioner_decide_booking`, propose/respond reschedule). Extend them via a new migration if a service reference is needed. Do not rewrite the approval-first logic.
6. Rate-limit and bot-check the new public-facing request endpoint with the existing guards.

Done when: a test athlete can pick a practitioner and service, request a slot, and the practitioner sees it as pending; a minor without guardian consent cannot complete the request.

## Phase 3: Appointments screen and video join

1. **Appointments** menu (practitioner): tabs Pending, Approved, Declined, Past. Each row: service, date and time, mode, athlete display name.
2. Actions: Approve, Decline (optional reason, content-free), Propose new time.
3. After approval the booking becomes CONFIRMED. A **Join video** button appears for both practitioner and athlete from 10 minutes before the start until the end time plus a grace period. Online services only.
4. Athlete portal: **My appointments** with status, Accept/Decline for proposed new time, and **Join video**.
5. Video uses the existing `video.py`, tickets, consent screen and `room.html`. Do not change the signalling design.
6. Document the TURN requirement in DEPLOYMENT.md; add `MODEL_C_ICE_URLS` and `MODEL_C_TURN_SECRET` to the examples.

Done when: request, approve and join works for two accounts; the button appears and disappears at the right times; tests cover state changes.

## Phase 4: Notifications for every role

Browsers (Web Push):
1. Service worker in `web/`, VAPID keys from env (`SPPS_VAPID_PUBLIC_KEY`, `SPPS_VAPID_PRIVATE_KEY`, `SPPS_VAPID_SUBJECT` as a generic contact address supplied by the owner).
2. Subscribe and unsubscribe endpoints.

Android APK (Capacitor):
3. Add `@capacitor/push-notifications`, wire FCM. Firebase server credentials only through an environment variable; `google-services.json` is added by the owner to the Android project (do not invent or commit one). Check that the APK build script still accepts the Capacitor version before changing versions; do not bump Capacitor major or minor.

Server:
4. Table of devices per user across all roles: user reference, role, platform (web / android), token or subscription, created, last seen, revoked. Identity-schema, RLS-protected.
5. Table of notification preferences per user and category (appointment updates, programme and task updates, account and security). Security category cannot be fully disabled.
6. Extend the outbox worker in `notifications.py` with push delivery next to e-mail. Content-free messages only, e.g. "You have an appointment update. Open the app."
7. Remove dead tokens when the provider reports them invalid.

UI:
8. "Turn on notifications" pop-up on first sign-in for every role (owner, admin, practitioner, athlete, guardian). Choices: Turn on, Not now. If Not now, show a reminder in Settings and ask again after a sensible interval. Never ask again if the browser permission is blocked; show how to enable it.
9. Settings page: per-category toggles and a list of registered devices with remove.
10. Trigger notifications for: new booking request, approval or decline, proposed new time, reminder before a session, new assignment, released summary.

Done when: a test device receives a push for a booking approval on web and on Android; tokens are revoked on sign-out; tests cover preference rules.

## Phase 5: Owner and admin model (do after the above)

1. Owner account opens the admin workspace only; no practitioner profile, no founder card in the directory by default. Remove the hard-coded founder photo and pinned-first logic.
2. Separate admin sign-in page, reached by a small footer link on the landing page.
3. Admin invitations (owner only); several admins allowed; MFA required for owner and admins; audit log of admin actions.
4. In-app photo upload for every practitioner.
5. Practitioner approval open to admins.

## Branch and commit workflow

One branch per phase, for example `phase1-enquiry-purge`. Commit in small steps with clear messages. Do not push; the owner reviews the diff and pushes.

## Report format at the end of each phase

Files changed, migrations added (with numbers), env vars added, flags added, tests run and results, risks, questions for the owner.
