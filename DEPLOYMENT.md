# SPPS Version 1.0 - Free — production deployment guide

First-time set-up in plain steps is in START-HERE.md; this guide is the full reference.

New Supabase project → new Render service → owner account. Do the steps in order.
Secrets (passwords, database URLs) go only into Supabase and Render, never into files in this folder.

## 0. What you need

- A Supabase account and a Render account.
- A place Render can pull the app from. Render cannot upload a local folder. Choose one:
  - **Git host** (recommended: the private GitHub repository `spps-ready`; GitLab and Bitbucket also work): connect it to Render. Render builds `Dockerfile`.
  - **Container registry** (Docker Hub, GitLab or GitHub registry): `docker build -t <registry>/<name>:<tag> .`, `docker push`, then create the Render service from **Existing Image**. Redeploys are manual.
- Four random passwords (letters and digits, 32+ characters) for the database roles.

## 1. Supabase project

1. Create a new project. Note the **project ref** (20 characters, in the project URL) and the region.
2. SQL editor: run every file in `supabase/sql/` **in filename order**, `00_…` to `46_…` (47 files), one at a time
   (v2: the full ordered list is in "v2 release, step by step" below and in `MIGRATIONS.md`).
   Each file is a single transaction: if one fails, nothing from that file is applied. Stop and fix before continuing.
   A project that already ran `00_…` to `30_…` only needs the later files (`31_…`, `32_…`: indexes only), in order.
3. Run `v2_verify.sql` (whole database). Expected values are listed at the top of the file.
4. Edit a copy of `99_set_role_passwords.sql` with your four passwords, run it, and do not save the edited copy.
5. Authentication → Sign In / Providers: enable **Email**, turn on **Confirm email**, leave every other provider off.
6. Authentication → URL Configuration: **Site URL** = your public address (for example `https://app.winmindperform.com`);
   add redirect URLs `<site>/register` and `<site>/studio`.
7. Connect → **Session pooler**: note the host (`aws-…pooler.supabase.com`) and port `5432`.
8. Project Settings → API keys: copy the **publishable** key (`sb_publishable_…`). Never use the secret or service-role key in Render.

What the SQL creates: schemas `spps_dev` (33 clinical tables), `spps_identity_dev` (22 portal tables) and `spps_ops`
(migration history); four login roles with least privilege plus one owner role; forced row-level security on every table;
the tenant `winmindperform`; one public enquiry service; and narrow functions for the public directory, public enquiry,
owner claim, registration and applicant resubmission (step `30`). Anonymous visitors and the Supabase API roles never read a table directly.
The Supabase API roles (`anon`, `authenticated`) get no access to these schemas; the app talks to PostgreSQL only through the backend.

## 2. Render service

Create a **Web Service**, runtime **Docker**, one instance, health check path `/health/ready`.
`render.yaml` lists every variable; `.env.production.example` shows the format. Set:

| Variable | Value |
|---|---|
| `MODEL_C_ENV` | `production` |
| `SPPS_HOSTED_RLS`, `SPPS_SINGLE_DATABASE_SCHEMAS`, `SPPS_BETA_V1_FREE`, `SPPS_SUPABASE_AUTH_ENABLED`, `SPPS_OWNER_BOOTSTRAP_ENABLED`, `MODEL_C_COOKIE_SECURE` | `true` |
| `SPPS_PORTAL_ENABLED`, `MODEL_C_INIT_DB`, `MODEL_C_SEED_DEMO` | `false` |
| `SPPS_PAYMENT_PROVIDER` | `disabled` |
| `SPPS_CLINICAL_DATABASE_NAME`, `SPPS_IDENTITY_DATABASE_NAME` | `postgres` |
| `MODEL_C_PUBLIC_TENANT` | `winmindperform` — required; the app refuses to start in production with any other value |
| `SPPS_TRUSTED_PROXY_HOPS` | `1` |
| `SPPS_SUPABASE_PROJECT_REF` | your project ref |
| `SPPS_SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SPPS_SUPABASE_PUBLISHABLE_KEY` | the publishable key |
| `SPPS_OWNER_EMAIL` | the owner's email (must match `server/owner_profile.py`) |
| `SPPS_PUBLIC_BASE_URL` | `https://<your public address>` |
| `MODEL_C_ALLOWED_HOSTS` | your public host and `<service>.onrender.com`, comma-separated |
| `DATABASE_URL` | `postgresql+psycopg://spps_dev_app.<ref>:<password 1>@<pooler host>:5432/postgres?sslmode=require` |
| `SPPS_AUTH_DATABASE_URL` | same form, role `spps_dev_auth`, password 2 |
| `SPPS_IDENTITY_AUTH_DATABASE_URL` | same form, role `spps_identity_auth`, password 3 |
| `SPPS_IDENTITY_PORTAL_DATABASE_URL` | same form, role `spps_identity_portal`, password 4 |
| `SPPS_PRODUCTION_RELEASE_APPROVED` | `true` — set last, by the owner, when you are ready to open |

The app refuses to start, with a named reason in the Render log, if any value is missing or inconsistent.
Do **not** set `SPPS_STARTUP_MIGRATIONS`; the database is managed with the SQL files.

Optional approval e-mails: set all four of `SPPS_SMTP_HOST`, `SPPS_SMTP_USERNAME`, `SPPS_SMTP_PASSWORD`, `SPPS_SMTP_FROM`
(and `SPPS_SMTP_PORT` if not 587), or none. Without them, applicants see the decision when they sign in.

Custom domain: add it in Render → Settings → Custom Domains, then point a CNAME at the `onrender.com` address.

## 3. First run

1. Deploy. `/health/live` and `/health/ready` must both return HTTP 200.
2. Open `<site>/register`, enter the owner email and a password, press the button, confirm the email from Supabase.
   The link returns to the site and confirms the address, but the server accepts only a password sign-in, not the link
   alone: open `<site>/studio`, enter the same email and password and press **Sign in**. The first confirmed
   password sign-in with the owner email becomes the administrator and is listed in **Find a Sport Psychologist** at the top, automatically. No other
   account can become an administrator (the database itself refuses it), and a different Supabase identity using the
   owner address is refused once the owner has signed in.
3. The owner sees one dashboard with two workspaces: **Admin Dashboard** (registration requests, with a count of the
   ones waiting) and the practitioner workspace (athletes, enquiries, own profile).
4. Register a second, test practitioner with another email. Confirm it shows as pending in the Admin Dashboard,
   cannot open the workspace, and is not in the directory. Use **Request more info** with a note: the applicant sees the
   note after signing in with their password and can answer in the app. Then approve and confirm both change.
5. Reject or delete the test account.

An approved practitioner who edits their profile goes back to review and is hidden until the owner approves again.
The owner's own edits keep the listing and sign-in. Rejecting, or asking for more information, ends that person's
sessions immediately.

## 3a. Checking the build before you deploy (optional, no Supabase needed)

```
python -m pytest tests tests_v1 -q                 # unit and contract tests (SQLite)
tests_e2e/pg_tls_cluster.sh                        # throwaway local PostgreSQL with TLS; prints two export lines
python -m pytest tests_e2e -q                      # 18 end-to-end checks on a freshly built database
```
`tests_e2e` wipes the schemas in the database it points at, so it refuses anything except a local host and needs
`SPPS_E2E_DESTROY_SCHEMAS=yes`. Never point it at Supabase. Details: `tests_e2e/README.md`.

## 4. Later changes to the database

Add a new numbered file in `migrations/` and a matching file in `supabase/sql/` (wrapped in `BEGIN; … COMMIT;`
with its `spps_ops.migration_history` row), test it on a throwaway database, then run it in the SQL editor.
Never edit a file that has already been run.

### 4a. Account lifecycle, phase A (practitioner deactivation and data export)

Code and SQL are shipped together but the feature is **off** until you switch it on. Order matters:

1. Deploy the code (this happens automatically when the commit reaches `release/v1.0-live`). With the flag unset
   nothing changes: no new route, no query, no startup check.
2. Run `supabase/sql/33_0032_account_lifecycle.sql` in the Supabase SQL editor (one transaction). It adds one nullable
   column, redefines the public directory and photo functions to skip deactivated practitioners, and adds one
   self-scoped function. No table right is widened. Check with `supabase/sql/98_verify.sql` (expect 13 functions in
   `spps_dev` and 31 history rows).
3. In Render, set `SPPS_ACCOUNT_LIFECYCLE_ENABLED=true` and let the service redeploy. If step 2 was skipped the service
   refuses to start and the previous deploy keeps serving; the log says migration 0032 has not been run.
4. Sign in and open Profile & Privacy: "Your account" appears. Take the listing offline, confirm it left Find a Sport
   Psychologist, bring it back, then export your data (you are asked for your password).

Switching the flag off again removes the "Your account" section and the routes, but the database functions keep
hiding practitioners who are offline, and they cannot bring themselves back until the flag is on again. Ask them to
reactivate first, or clear one with `UPDATE spps_dev.practitioner_profiles SET deactivated_at = NULL WHERE practitioner_id = <id>;`
in the SQL editor.
Closing and purging an account is not part of this phase (`docs/ACCOUNT_LIFECYCLE_DESIGN.md`).

## 5. Not included in phase 1

- Athlete/guardian portal, bookings, programmes and video (`SPPS_PORTAL_ENABLED=true` needs SMTP and a TURN server).
- Payments. The Razorpay code is present but disabled.
- Android release build (see `ANDROID_README.md`); point it at the public address after the web app is live.

## 6. Before real athlete data

- Read the Terms of Use, Privacy Policy and Platform Policies (`/terms`, `/privacy`, `/policies`) and have them reviewed by your lawyer; change `web/*.html` if needed, then run `npm run android:web`.
- Take a Supabase backup and practise restoring it into a second project.
- Supabase's built-in e-mail sender is for low volume; configure your own SMTP in Supabase Auth as registrations grow.

---

# SPPS v2 additions (spps-v2)

Everything above still applies. This part is the complete v2 release procedure. Every v2 feature that athletes,
guardians, coaches or the public can see sits behind a flag that is `false` by default; the code can be deployed
with every flag off and nothing visible changes except the practitioner-side phase 1–5 improvements.

## v2 release, step by step

Before you start: take a Supabase backup (Database → Backups) and note the current Render deploy, so every step
below can be undone.

### Step 1 — SQL, in this order (Supabase SQL editor, default `postgres` role, one file at a time)

| # | File | Rollback if needed |
|---|---|---|
| 1 | `00_…` to `33_0032_account_lifecycle.sql` | Already live on an existing project; a new project runs them first (section 1). |
| 2 | `34_0033_v2_registration_state_machine.sql` | Each file is one transaction: a failure leaves nothing behind. After success, roll back by restoring the backup taken above. |
| 3 | `35_0034_v2_trust_foundation.sql` | as above |
| 4 | `36_0035_v2_practitioner_record.sql` | as above |
| 5 | `37_0036_v2_assessments.sql` | as above |
| 6 | `38_0037_v2_reports.sql` | as above |
| 7 | `39_0038_v2_athlete_loop.sql` | as above |
| 8 | `40_0039_v2_directory.sql` | as above |
| 9 | `41_0040_v2_self_serve.sql` | as above |
| 10 | `42_0041_v2_release_hardening.sql` | as above |
| 11 | `43_0042_v2_appointment_only_directory.sql` | **contract step - run only AFTER the booking-only code is live.** Removes the enquiry tables/functions. Refuses to run (drops nothing) if any enquiry row exists; export or delete those rows first |
| 12 | `44_0043_v2_practitioner_services.sql` | **expand step - run BEFORE deploying the phase 2 (services and directory booking) code**; the release that is live keeps working after it. Adds `practitioner_services`, `directory_service_snapshot`, three identity columns and five identity functions (created as `spps_identity_owner`). Rollback: restore the backup taken above |
| 13 | `45_0044_v2_service_snapshot_fk_index.sql` | adds one index (covering the tenant foreign key of `directory_service_snapshot`); safe to run more than once; run right after step 12 |
| 14 | `46_0045_v2_appointments_video.sql` | **expand step — run after 0044 and BEFORE Phase 3 code**. Updates identity functions as `spps_identity_owner`; no data rewrite or new tables. |
| 15 | `v2_verify.sql` | read-only; after 0045: tables 63 / 36, policies 96 / 96, 13 v2 migrations, missing v2 tables `(none)`; all three safety rows 0. |

The new code refuses to start (and `/health/ready` stays red) until steps 2–10 (and, for the phase 2 code, steps 12 and 13) have
run, so run the SQL **before**
deploying the v2 code. The SQL only adds tables, columns, policies and functions; the v1 code keeps working on a
database that already has them, so the order "SQL first, then code" is always safe. Never edit a file that has
been run; later changes are new numbered files.

### Step 2 — variables (Render → Environment). Safe example values only; real secrets never go into files.

| Variable | Needed | Safe example / format |
|---|---|---|
| `SPPS_FORM_TOKEN_SECRET` | always | 32+ random characters (Render "Generate") |
| `SPPS_FIELD_KEYS` | always | `k1:<base64 of 32 random bytes>` from `python tools/v2_rotate_field_key.py new-key k1` |
| `SPPS_FIELD_KEY_CURRENT` | always | `k1` |
| `SPPS_BOT_MIN_FILL_SECONDS` | optional | `3` |
| `SPPS_PUBLIC_BASE_URL` | athlete loop, self-serve | `https://app.example.org` (https only; used in every e-mailed link) |
| `SPPS_SMTP_HOST`, `SPPS_SMTP_PORT`, `SPPS_SMTP_USERNAME`, `SPPS_SMTP_PASSWORD`, `SPPS_SMTP_FROM` | athlete loop, self-serve | `smtp.example.org`, `587`, `fictional-user`, *(password manager)*, `no-reply@example.org` |
| `SPPS_CRISIS_CONTACTS_TEXT` | directory | `Emergency: 112. Tele-MANAS: 14416 (24/7).` — confirm the numbers yourself before launch |
| `SPPS_CLOSURE_NOTICE_DAYS`, `SPPS_CLOSURE_COOLING_OFF_DAYS` | closure (optional) | `30`, `14` |

Flags (all `false` until their step below): `SPPS_PORTAL_ENABLED`, `SPPS_ATHLETE_LOOP_ENABLED`,
`SPPS_DIRECTORY_ENABLED`, `SPPS_SELF_SERVE_ENABLED`, `SPPS_REPORT_LINKS_ENABLED`, `SPPS_PRACTITIONER_CLOSURE_ENABLED`,
`SPPS_AI_FEATURES_ENABLED`, `SPPS_DIRECTORY_BOOKING_ENABLED`.

To see what the release preflight will check (names only, never values), run on your own computer with the same
variables loaded: `python -m server.v2.preflight`. In Render, a missing or invalid value stops the start with the
variable's name in the log and the previous deploy keeps serving.

### Step 3 — deploy the code with every flag off

Deploy, then check `/health/live` and `/health/ready` return 200. Sign in as the owner; the Admin Dashboard shows the
registration summary. Rollback: redeploy the previous Render deploy (the database additions are harmless to it).

### Step 4 — switch features on, one at a time, in this order

Each flag change redeploys the service. After each one, test it with a fictional account before going on.
Rollback for every flag: set it back to `false`. Nothing is deleted by switching a flag off; data written while it
was on stays in the database, invisible, until the flag is on again.

1. `SPPS_REPORT_LINKS_ENABLED=true` (practitioner-created coach links with recorded consent; phase 5).
2. `SPPS_PORTAL_ENABLED=true` and then `SPPS_ATHLETE_LOOP_ENABLED=true` — the athlete loop goes first. Needs SMTP and
   `SPPS_PUBLIC_BASE_URL`. Test: invite a fictional athlete, open the e-mail on a phone in Gmail, open the link in
   Chrome and in the Android app, sign in and accept. Set the crisis contacts in the Admin Dashboard.
3. `SPPS_DIRECTORY_ENABLED=true` — the public directory second. Needs `SPPS_CRISIS_CONTACTS_TEXT`. Test: submit and
   publish your own profile, find it at `/find`, send a fictional enquiry, check the auto-reply and the inbox.
4. `SPPS_SELF_SERVE_ENABLED=true` (needs steps 2 and 3). Test: create a fictional athlete account, send an enquiry
   from `/find`, accept it in the workspace, accept the invitation from the account in another browser, ask for and
   approve a coach link, then delete the fictional account and confirm the link shows "ended".
5. `SPPS_DIRECTORY_BOOKING_ENABLED=true` (needs `SPPS_PORTAL_ENABLED`, `SPPS_DIRECTORY_ENABLED` and step 12 of the SQL,
   `44_0043`; the preflight refuses to start otherwise). Before switching it on, each listed practitioner adds services
   in Settings → My services and publishes open times. Test with fictional accounts: open `/find`, choose a card,
   "Book appointment", sign in with a verified athlete account, pick a service, a time and a short note, send it; the
   practitioner sees it under Client bookings as "First request" with display name, service, time and note only;
   approve it and check the booking is CONFIRMED and the athlete record exists. Repeat as a guardian for a fictional
   dependant: refused until the guardian authority (with "Request and manage bookings") is verified.
6. Notifications (phase 4). Run `47_0046_v2_notifications_push.sql` in Supabase first, then `v2_verify.sql`. Create keys
   with `python tools/generate_vapid_keys.py` and set `SPPS_VAPID_PUBLIC_KEY`, `SPPS_VAPID_PRIVATE_KEY` (secret) and
   `SPPS_VAPID_SUBJECT` (`mailto:` an address you read). Set `SPPS_NOTIFY_WORKER_ENABLED=true` (this starts the
   delivery loop inside the web service, so queued e-mail finally sends; it needs the SMTP settings) and then
   `SPPS_PUSH_ENABLED=true`. Test with fictional accounts in two browsers: sign in, accept the "Turn on notifications"
   pop-up, have the athlete request a booking, and check the practitioner's browser shows "New appointment request"
   with no names. Approve it, and check the athlete is told. A confirmed appointment starting within
   `SPPS_REMINDER_MINUTES` (default 15) sends one reminder to each side. Android push also needs the Firebase
   plugin and `SPPS_FCM_SERVICE_ACCOUNT_JSON` (see docs/PHASE4_REPORT.md). Switching either flag off stops delivery at
   once; queued items wait. Rotating VAPID keys signs every browser out of push: people turn it on again.
7. Guest booking. Run `48_0047_v2_guest_booking.sql` in Supabase, then `v2_verify.sql` (expect 42 identity tables, 120
   identity policies, 15 v2 migrations). Needs steps 5 and 6 and a verified sending domain: guests are strangers, so
   mail must reach any address (in Resend, verify your domain and set `SPPS_SMTP_FROM` to an address on it). Set
   `SPPS_GUEST_BOOKING_ENABLED=true` (the preflight refuses without the directory booking flag and the worker). Test
   with a fictional guest address you can read, in a private window: `/find` > Book appointment > service > time >
   details > "Request submitted, check your mail"; open the e-mail, press Confirm; the practitioner sees the request,
   approves it; the guest e-mail has "create your account"; set a password, sign in, and the appointment, link and
   (for online services) Join video are there. Join video opens 15 minutes before the start; the practitioner may enter
   first and the guest waits in the room. Switching the flag off removes the guest routes at once; people who already
   confirmed keep working accounts.
8. Optional, any time: `SPPS_PRACTITIONER_CLOSURE_ENABLED=true`; `SPPS_AI_FEATURES_ENABLED=true` only after the AI
   wording has been reviewed (every AI output is a labelled draft and each practitioner must opt in).

Rollback when a flag has been on: switching `SPPS_SELF_SERVE_ENABLED` off hides account enquiries, invitations
lists, deletion and athlete-approved links (approved `/s/` links stop opening). Switching `SPPS_DIRECTORY_ENABLED`
off removes `/find` and the public API at once. Switching `SPPS_DIRECTORY_BOOKING_ENABLED` off closes the two
directory booking endpoints at once; requests already made stay in the practitioner's Client bookings and can still
be approved or declined there. Switching `SPPS_ATHLETE_LOOP_ENABLED` off hides the athlete screens;
links and answers stay stored.

### Step 5 — backup and restore drill (before real athlete data, then every quarter)

1. Supabase → Database → Backups: download or note the latest daily backup.
2. Create a second, empty Supabase project ("restore drill"). Restore the backup into it (Supabase restore, or
   `pg_restore` of a `pg_dump -Fc` taken from your own computer with the session pooler URL).
3. Run `v2_verify.sql` there: the values must match production.
4. Point a local copy of the app (never Render) at the drill project with the same `SPPS_FIELD_KEYS`, sign in as a
   fictional practitioner and open a note: if it reads, the backup and the keys are both good.
5. Delete the drill project. Record the date and the result in your launch log.

### Step 6 — hosted Android APK (Windows PowerShell, from the project folder)

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-SPPS-1.0-Free-APK.ps1 -HostedOrigin https://app.winmindperform.com
```
Use your real public https address. The script checks the pinned versions, runs the Android source tests,
configures the hosted origin with cleartext traffic off, builds a DEBUG APK into `output\` and prints its SHA-256.
Add `-Install` to install it on a connected phone with `adb`. The Android package id is unchanged. The web files
inside the APK come from `www/` (generated from `web/` by `npm run android:web`); never edit `www/` or the Android
assets by hand.

### Proof that the SQL set builds the database (for the release record)

`python tools/v2_pg_harness.py up` (throwaway local PostgreSQL), then `python tools/v2_schema_proof.py`: it builds one
empty database from `supabase/sql/` in order and another from the v1 steps plus the raw v2 `migrations/`, compares
`pg_dump --schema-only` of both and runs `v2_verify.sql`; then `python tools/v2_pg_harness.py down`.

## Health check

Render health-check path: **`/health/ready`** (already set in `render.yaml`). It returns 200 only when the database
answers and every v1 and v2 table exists, so a release whose SQL has not been run is never put into service.
`/health/live` is a process-only check with no database access.

## Logs

Every response carries `X-Request-ID`; every log line written during that request carries the same id as
`rid=…`. Log lines never contain e-mail addresses, request bodies, cookies or tokens. To trace a user-reported
problem, ask for the request id shown in the browser's network panel and search the Render log for it.

## Rate limits

Public and sign-in endpoints are rate limited per process (`server/v2/guards.py`, table `LIMITS`). Render runs one
instance; before running more than one instance, add a gateway rate limit in front of the service.

## Field encryption (phase 2)

Consultation notes and case formulations are encrypted before they are written. Set in Render:

| Variable | Value |
|---|---|
| `SPPS_FIELD_KEYS` | `k1:<base64 of 32 random bytes>` — generate with `python tools/v2_rotate_field_key.py new-key k1` on your own computer. Comma-separate old keys during a rotation. |
| `SPPS_FIELD_KEY_CURRENT` | `k1` (the id used for new writes) |

Keep a copy of every key in your password manager: without it the encrypted text cannot be read, including from
backups. Key rotation: follow the four steps at the top of `tools/v2_rotate_field_key.py` (add the new key and
make it current → redeploy → run `rotate` with a privileged connection from your own computer → remove the old key
and redeploy). The same `rotate` encrypts notes written before encryption was switched on.

## Account closure (phase 2, flag `SPPS_PRACTITIONER_CLOSURE_ENABLED`)

Optional: `SPPS_CLOSURE_NOTICE_DAYS` (default 30) and `SPPS_CLOSURE_COOLING_OFF_DAYS` (default 14). Rollback:
set the flag to `false`; nothing is deleted by switching it off, and closures already completed stay completed.


## Phase 3 — appointments and video (owner-operated release only)

For an existing installation already through 0044, the exact SQL order is:
1. Back up, then run `supabase/sql/46_0045_v2_appointments_video.sql` once as the normal installer.
2. Run `supabase/sql/v2_verify.sql`: tables `spps_dev=63`, `spps_identity_dev=36`; policies `96 / 96`;
   `force_rls_missing=0`, `api_roles_with_access=0`, `anon_execute_on_definer=0`; `v2 migrations=13`;
   `missing v2 tables=(none)`. Login roles remain the existing four restricted roles.
3. Only after verification, release the Phase 3 code through the owner's GitHub/Render process.

The migration is expand-only and replaces identity functions inside `SET LOCAL ROLE spps_identity_owner`
and `RESET ROLE`; the installer must be a member of that role and have SELECT/INSERT on the migration ledger (the serial wrapper uses ON CONFLICT).
Test the generated serial file as a non-superuser installer before using the hosted database. Do not rerun
or edit earlier serial files. The startup preflight requires the new `portal_booking_details` function.

Use Appointments → Pending / Approved / Declined / Past. Times are stored in UTC and displayed in the
viewer's local time zone. A pending first directory request discloses only display name, service and slot;
its note and guardian details stay hidden. Accepted proposals for an unapproved request return to pending
approval; approving that request creates the minimal athlete record and activates the existing pending link.
Decline reasons are optional fixed phrases. No appointment decision or reschedule is allowed after its start.

Video opens only for CONFIRMED online appointments from start minus 15 minutes through end plus 15 minutes (since migration 0047)
(inclusive). Both ticket issuance and every ticket check use the database window. Existing consent, guardian
booking authority, active-link and final-room-closure checks still apply. A third account cannot issue a ticket.
The consent screen and `room.html` use the existing two-peer signalling design; there is no recording.

A working TURN relay is required before offering reliable online appointments across different networks.
STUN alone cannot relay media through restrictive NAT/firewalls; without ICE configuration only host candidates
are used. Configure coturn with `use-auth-secret`, or an equivalent compatible relay, and provision matching
values outside the repository. Safe example (also in tracked env examples):

```text
MODEL_C_ICE_URLS=turns:turn.example.org:443?transport=tcp
MODEL_C_TURN_SECRET=<at-least-32-character-shared-secret>
```

`MODEL_C_ICE_URLS` accepts comma-separated `stun:`, `turn:` and `turns:` URLs. Prefer TURN over TLS on port 443
and offer UDP TURN where possible. `MODEL_C_TURN_SECRET` stays server-side; browsers receive only derived,
expiring credentials. Static TURN username/password are rejected in staging/production. Optional
`MODEL_C_TURN_TTL_SECONDS` defaults to 3600 (clamped to 300–7200); `MODEL_C_ICE_TRANSPORT_POLICY=relay`
can force relay-only during the owner's two-account network test. Verify relay candidates connect between
separate networks before real use. Existing `SPPS_PORTAL_ENABLED` gates video/bookings; no new flag is added.

No hosted SQL, Render environment change, push, PR, merge or deploy is performed by local Phase 3 work.


### Local Phase 3 PostgreSQL validation on Windows (Docker only)

Use the independent sanctioned slot; its lifecycle checks Compose labels, loopback binding and tmpfs storage.
These commands create fictional test databases and ignored disposable credentials only, never a hosted target:

```powershell
$env:PYTHONUTF8='1'
$env:SPPS_EPHEMERAL_SLOT='2'
$env:SPPS_V2_PG_DOCKER='1'
python -m tools.v2_pg_harness up
$env:SPPS_V2_PG_ENV=(Join-Path (Get-Location) 'output/test-ephemeral-2/v2.env')
python -m pytest tests_v2_pg -q -p no:cacheprovider
python -m tools.v2_pg_harness down
```

The harness delegates cluster creation/destruction to `tools.ephemeral_db`; the suite rechecks every URL with
`tools.ephemeral_guard`. Never set the CI bypass flag locally. Credentials are generated and consumed by the
harness and tests; do not print the env file or commit it. Run cleanup after tests, even if a test fails.
