# START HERE — SPPS Version 1.0 - Free

Your plan, in order: put the folder on your Desktop → build the Android APK → create a new Supabase database and run
the SQL in sequence → run the web app on your computer → test every function. Each step below says exactly what to
do. Nothing in this folder contains passwords or keys; you add those yourself in Step 4.

## What you need

- A Windows computer with **Python 3.12 or newer** (python.org, tick "Add to PATH"), **Node.js 22 or newer** and,
  for the APK, **Android Studio** with Android SDK Platform 36.
- A **Supabase** account. A **Render** account when you put the site online.
- For athlete and guardian accounts: an **SMTP e-mail service** (for example Resend with your domain), because
  invitations and verification links are sent by e-mail.

## Step 1 — Put the folder on your Desktop

Unzip `spps-v1.0-free.zip` to your Desktop. You get the folder `spps-v1.0-free`. All commands below are run from that
folder: open it, click the address bar, type `powershell` and press Enter.

## Step 2 — Build the Android APK

The app opens your hosted SPPS address, so use the https address the site will have (for example
`https://app.winmindperform.com`):

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-SPPS-1.0-Free-APK.ps1 -HostedOrigin https://app.winmindperform.com
```

The APK is `output\SPPS-1.0-Free.apk`. It installs over earlier SPPS builds (same package id). The app works once the
site is online at that address (Step 9); until then, test with the web app on your computer. Details:
`ANDROID_README.md`.

## Step 3 — Create the Supabase database and run the SQL in sequence

1. In Supabase create a **new project**. Choose a strong database password and the region closest to your users.
   Note the **project ref** (the 20 letters in the project address).
2. Open **SQL Editor**. For each file in `supabase\sql\`, in filename order from `00_bootstrap_roles_schemas.sql` to
   `42_0041_v2_release_hardening.sql` (43 files): open it, copy everything, paste into a new query, press **Run**. Each file
   is one transaction: if one fails, nothing from it is applied — stop and fix before going on. `MIGRATIONS.md`
   lists every file with what it does.
3. Run `supabase\sql\v2_verify.sql`. The results must match the values at the top of that file: tables 63 / 37,
   policies 95 / 98, and **0** for `force_rls_missing`, `api_roles_with_access` and `anon_execute_on_definer`.
4. Choose **four new random passwords** (32+ letters and digits each) and keep them in your password manager. Open
   `99_set_role_passwords.sql`, paste it into a new query, replace the four placeholders with your passwords, run it,
   and close the query **without saving**.
5. **Authentication → Sign In / Providers:** enable Email, turn on **Confirm email**, leave other providers off.
6. **Authentication → URL Configuration:** Site URL = your public address (e.g. `https://app.winmindperform.com`).
   Add these redirect URLs: `https://app.winmindperform.com/register`, `https://app.winmindperform.com/studio`,
   `http://localhost:8000/register`, `http://localhost:8000/studio` (the last two let you test on your computer).
7. **Connect → Session pooler:** note the host (`aws-…pooler.supabase.com`, port 5432).
8. **Project Settings → API keys:** copy the **publishable** key (`sb_publishable_…`). Never use the secret or
   service-role key.

## Step 4 — Your settings file (`.env.local`)

1. Copy `.env.production.example` and name the copy `.env.local` (in the same folder).
2. Replace every `<…>` placeholder:
   - `SPPS_SUPABASE_PROJECT_REF`, `SPPS_SUPABASE_URL`, `SPPS_SUPABASE_PUBLISHABLE_KEY` — from Step 3.
   - `DATABASE_URL`, `SPPS_AUTH_DATABASE_URL`, `SPPS_IDENTITY_AUTH_DATABASE_URL`, `SPPS_IDENTITY_PORTAL_DATABASE_URL` —
     the four lines already show the format; put in your project ref, pooler host and the matching password from
     Step 3.4 (role 1 `spps_dev_app`, 2 `spps_dev_auth`, 3 `spps_identity_auth`, 4 `spps_identity_portal`).
   - `SPPS_OWNER_EMAIL` — your owner e-mail address.
   - `SPPS_PUBLIC_BASE_URL` and `MODEL_C_ALLOWED_HOSTS` — your public address and host.
   - `SPPS_FORM_TOKEN_SECRET` — 40 random letters and digits.
   - `SPPS_FIELD_KEYS` — after Step 5 has run once (it creates the Python environment), run
     `.venv\Scripts\python.exe tools\v2_rotate_field_key.py new-key k1` and paste the printed `k1:…` value. Keep this
     key in your password manager: without it, encrypted notes cannot be read, including from backups.
3. Leave the feature switches (`…_ENABLED`) as `false` for now; Step 7 switches them on one by one.

`.env.local` contains secrets: never send it, zip it or upload it. Use exactly the same values in Render later.

## Step 5 — Run the web app on your computer

Double-click `Start-SPPS-Local.bat` (or run `powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-SPPS-Local.ps1`).
The first run installs the Python packages. Your browser opens `http://localhost:8000`; use Chrome or Edge.
Check `http://localhost:8000/health/ready` shows `"status":"ready"`. If the window shows an error instead, it names the
setting that is missing or wrong — fix it in `.env.local` and start again. Stop with **Ctrl+C**.

Your computer uses the **same database** as the live site will: everything you create is real data.

## Step 6 — Create the owner account

1. Open `http://localhost:8000/register`, enter your owner e-mail and a password, complete the form and send it.
2. Open the confirmation e-mail from Supabase and press the link.
3. Open `http://localhost:8000/studio`, sign in with the same e-mail and password. You are the administrator: you see
   the **Admin Dashboard** and your own practitioner workspace, and you are listed first on the home page.

## Step 7 — Test every function

Work down this list. After changing `.env.local`, stop the app (Ctrl+C) and start it again. How each screen works
is in the in-app guide at `/guide`.

**A. Practitioner workspace (no switches needed)**
- [ ] Settings: edit your profile; download all your data (asks for your password); open "Your data".
- [ ] Add an athlete; record consent; open a case; write a note (watch "saved"), sign and lock it, add an amendment.
- [ ] Profile, Timeline, Formulation (5Ps, save twice and see the earlier version kept), Access log.
- [ ] Assessments over time: enter subscale scores twice on different dates; open Trend; build your own scale.
- [ ] Monitoring, Load & Recovery, Injury Psychology, Interventions, Reports (v1 report draft, PDF/CSV).
- [ ] Import athletes from a small spreadsheet (check the preview, then import).
- [ ] Note and report templates in Settings.
- [ ] Home page: your listing; send a service enquiry to yourself from a private window; see it under Enquiries.

**B. Second practitioner and approval** — register with another e-mail address you own (private window):
- [ ] It shows as pending in the Admin Dashboard and cannot open the workspace.
- [ ] Request more info with a note → the applicant sees the note after signing in and answers it.
- [ ] Approve → the applicant can sign in; check the e-mail log entry.

**C. Reports and coach links** — set `SPPS_REPORT_LINKS_ENABLED=true`:
- [ ] Create a report template with a coach field and a clinical-only field; write and sign a coach report.
- [ ] Record consent; create a share link; open it in a private window (only the coach field shows); revoke it and
      reload (it stops working).

**D. Account closure** — set `SPPS_PRACTITIONER_CLOSURE_ENABLED=true`, use the second practitioner:
- [ ] Start closure, choose a disposition per athlete, cancel; start again and complete; as owner see it under
      Account closures and restore it.

**E. Athlete app** — fill in the `SPPS_SMTP_…` lines (including `SPPS_SMTP_SENDER`), then set
`SPPS_PORTAL_ENABLED=true` and `SPPS_ATHLETE_LOOP_ENABLED=true`:
- [ ] Admin Dashboard: add crisis contacts.
- [ ] On an athlete record press **Athlete app** and invite an e-mail address you own.
- [ ] Open `http://localhost:8000/athlete`, create an athlete account with that address, verify it, sign in, accept
      the invitation. E-mailed links point to your public address; until the site is online, replace the start of the
      link (`https://app.winmindperform.com`) with `http://localhost:8000` — everything is stored on the server, so
      the link works either way.
- [ ] Set a check-in plan and an alert rule; submit a check-in that triggers it; see and acknowledge the alert.
- [ ] Assign a programme; complete a task; release a summary.
- [ ] Under-18: create a second athlete with age group 15-17 and a guardian e-mail; the guardian accepts first (types
      their name), then the athlete; check what the guardian sees.
- [ ] End a link and confirm the athlete no longer sees it.

**F. Directory** — set `SPPS_DIRECTORY_ENABLED=true` and `SPPS_CRISIS_CONTACTS_TEXT`:
- [ ] Settings: fill in the public directory profile and submit it. Admin Dashboard: tick the checks, publish.
- [ ] Open `/find`, filter, send an enquiry from a private window; check the acknowledgement e-mail and your inbox;
      mark as replied; see the response time in the Admin Dashboard. Delist and confirm it disappears.

**G. Self-serve accounts** — set `SPPS_SELF_SERVE_ENABLED=true` (needs E and F):
- [ ] As an athlete account, find your listing and send an enquiry; as practitioner press **Accept and invite**; as
      the athlete accept the invitation from the account screen.
- [ ] Ask the athlete to approve a coach report; approve it in the athlete app; open the link; withdraw it.
- [ ] Switch practitioner (needs the second practitioner listed); switch the account off and back on by e-mail;
      download the athlete's data; delete the athlete account.

**H. Android** — once the site is online (Step 9): install `output\SPPS-1.0-Free.apk`, sign in as a practitioner and
as an athlete, open an e-mailed link in Chrome and in the app.

## Step 8 — Automated checks (optional)

Double-click `test_windows.bat`. It uses made-up test data in a temporary database on your computer, never Supabase.
The expected result is "passed" with no failures.

## Step 9 — Put the site online (Render)

Follow `DEPLOYMENT.md`: create the Render web service from this folder, enter the same values as `.env.local` (not
the two lines the local start adds for `localhost`), deploy, and check `/health/live` and `/health/ready`. Switch
features on in the order given there (athlete app first, then the directory, then self-serve). Before real users,
work through `LAUNCH_CHECKLIST.md`, including a lawyer's review of `/terms`, `/privacy` and `/policies`.

## If something goes wrong

- **The start window closes with an error** — read the message: it names the setting. Most often a placeholder
  `<…>` is still in `.env.local`, or a database password does not match Step 3.4.
- **"Database not ready" at `/health/ready`** — a SQL file was skipped or failed; run `v2_verify.sql` again.
- **The Supabase confirmation link opens the live address** — add the `localhost` redirect URLs (Step 3.6).
- **Sign-in works on the website but the app shows nothing** — the APK was built for a different address; rebuild
  with the right `-HostedOrigin`.
