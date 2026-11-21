# SPPS handoff to Codex (written 2026-11-21)

Read this first, then `AGENTS.md`, `CLAUDE.md` (same architecture guide; Codex should follow it), `PROGRESS.md`,
`DECISIONS.md`, `MIGRATIONS.md`, `DEPLOYMENT.md`, `docs/BUILD_BRIEF_booking_only.md`.
Note: the command guard in `.claude/hooks/` only protects Claude Code. Codex must obey the prohibitions by itself.

## 1. What and where
- App: SPPS Version 1.0 Free (WinMindPerform Sport Psychology Practitioner Suite). FastAPI `server/`, vanilla JS `web/`
  (`www/` and Android assets are GENERATED, never hand-edit), Capacitor Android, Supabase Postgres with FORCE RLS
  (schemas `spps_dev` clinical, `spps_identity_dev` identity).
- Local folder: `C:\My Computer\Codex\spps-v1.0-free`. GitHub: `Karansportpsy/spps-karan` (default branch `main`).
- Supabase project `spps-ready-1` (ref zloqbulgqtoqbdapxaap). Render web service `spps-ready-1` deploys `main`
  automatically; public site https://app.winmindperform.com.
- Goal ("booking-only directory"): practitioners list services; athletes/guardians pick a practitioner + service and
  book; the request lands in the practitioner's Appointments for approval; then a join-video link shows for both;
  notifications for every role. Enquiries were removed entirely.

## 2. State today
- Done and merged to `main`: Phase 1 (enquiry purge, migration 0042 applied after the new code) and Phase 2
  (services catalogue, directory booking, migrations 0043 and 0044; CI green).
- Not started: Phase 3 appointments screen + video, Phase 4 notifications, Phase 5 owner/admin, MFA, audit log, photos.
  Prompts for them: `docs/PHASES_3-5_PROMPTS.md`.
- Live flags (Render): portal, athlete loop, directory, self-serve, directory booking.

## 3. Open items to close BEFORE new phases (owner does Render/Supabase steps)
1. Render env: add `SPPS_SMTP_SENDER` (plain address) and set `SPPS_SMTP_FROM` to the same plain address (the startup
   check rejects "Name <addr>"); add `SPPS_CRISIS_CONTACTS_TEXT` (10-600 chars). Then redeploy and check `/health/ready`.
2. Rotate the Resend API key and add a second field-encryption key (`SPPS_FIELD_KEYS=k1:...,k2:...`,
   `SPPS_FIELD_KEY_CURRENT=k2`; never drop k1). Both were exposed in a screenshot.
3. Publish a practitioner in the directory (practitioner submits the profile in Settings; owner publishes in the Admin
   directory list). Today the directory has 0 listings, so `/find` is empty and booking cannot be tested.
4. Smoke test: fictional athlete books a service from `/find`; request appears under Client bookings as "First request".
5. Supabase: `45_0044_...sql` is already applied. Run `v2_verify.sql` (expect 63/36 tables, 96/96 policies, 12 v2
   migrations, 0 on the safety rows). Then revoke the temporary USAGE grant on the identity schema.
6. Pending small branch: `fix-resend-label` (button text "Resend link"). Merge or fold into Phase 3.
7. Small cleanup: unify `SPPS_SMTP_SENDER` and `SPPS_SMTP_FROM` into one variable and allow a display name; add a
   Forgot-password link to the "SPPS sign-in help" e-mail; empty-directory message and a Find-a-practitioner button
   on the athlete home screen.
8. Owner decisions: purge programme enquiries and organisation quote requests (default: keep); enable Dependabot
   security updates; replace the owner e-mail literal in `server/main.py` with the `OWNER_EMAIL` constant (Phase 5).

## 4. Local build loop (PowerShell, in the repo folder)
```powershell
git fetch origin
git switch -c <branch-name> origin/main
python -m pytest tests tests_v1 tests_v2 -q -p no:cacheprovider      # unit/API/browser (SQLite)
python tools/v2_pg_harness.py up                                       # throwaway PostgreSQL on 127.0.0.1:55437
python -m pytest tests_v2_pg -q                                        # RLS + app-over-PostgreSQL
python tools/v2_pg_harness.py down
npm run web:stage                                                      # after any web/ change (www/ must be identical)
node --test tests_android/*.test.mjs
.\scripts\spps-check.ps1 -Tier db                                      # fresh ephemeral PG tier
```
Last known baselines: unit 564, PostgreSQL tier 58, e2e 27 (`tests_e2e`, needs the TLS cluster script), Android 62.
Run locally with `.\Start-SPPS-Local.ps1` (development profile, no real data).

## 5. Release flow (the only allowed path)
1. Branch from `origin/main`, change code, add acceptance AND negative tests (API and RLS), run all tiers above.
2. `git add -A; git commit -m "..."; git push -u origin <branch>` (never push to `main`, never force-push).
3. Open a PR on GitHub. CI must be green (Python 3.13/3.15 unit jobs, Android job, e2e job). The OWNER merges.
4. Merge to `main` makes Render deploy. Rule of thumb: expand SQL runs BEFORE the new code, contract SQL AFTER it is live.
5. The OWNER runs new SQL in the Supabase SQL editor (never edit an applied file; new number, generator
   `tools/v2_serial_sql.py`, update `MIGRATIONS.md`, `server/v2/preflight.py`, `supabase/sql/v2_verify.sql`).
6. Verify `/health/ready`, sign-in, role isolation, the approval flow. Report what shipped, which SQL ran, what is open.

## 6. Hard rules (from CLAUDE.md / AGENTS.md)
- Never read, print or commit `.env*`, API keys, SMTP passwords, `SPPS_FIELD_KEYS`, DB URLs, or the owner e-mail.
- No real personal or clinical data; fictional accounts only. No payment keys.
- Do not disable, skip or weaken security/RLS tests to get green. Do not apply SQL to Supabase, change Render env,
  deploy, or merge PRs yourself.
- Identity objects must be created/altered under `SET LOCAL ROLE spps_identity_owner ... RESET ROLE` (the Supabase
  `postgres` login does not own them). Every foreign key needs a covering index.
- `web/portal.js` must not use localStorage/sessionStorage/cookies/IndexedDB (portal contract test).
- Android ids and Capacitor versions stay unchanged.
- The legal pages (`policies/terms/privacy/guide.html`) are review drafts and need legal review before real intake.

## 7. Gotchas learned
- Hosted startup needs `SPPS_FORM_TOKEN_SECRET`, `SPPS_FIELD_KEYS`, `SPPS_FIELD_KEY_CURRENT`; the e2e harness sets throwaway values.
- Hosted-mode allowlist answers 503 "outside the isolated development milestone" for routes that are switched off.
- The portal flags need SMTP (Resend: host smtp.resend.com, port 587, user `resend`) and `SPPS_PUBLIC_BASE_URL`; the
  Resend domain must be verified.
- Registering an existing e-mail sends "SPPS sign-in help" by design (no account enumeration).
- Two identity systems exist: 13A (`platform_*`, bookings, video) and the v2 loop (`loop_*`). Directory booking targets 13A.
- `npm run web:stage` needs delete permission on the folder; if that is blocked, copy `web/<file>` to `www/assets/<file>`
  only for identical staged files and run the contract test.

## 8. Start-up prompt to give Codex
"Open C:\My Computer\Codex\spps-v1.0-free. Read docs/CODEX_HANDOFF.md, AGENTS.md, CLAUDE.md, PROGRESS.md. Confirm git
status is clean and report the baseline test counts. Then work on <task> on a new branch from origin/main, following
section 5 and 6 of the handoff. Stop and ask before anything that touches Supabase, Render, secrets or main."
