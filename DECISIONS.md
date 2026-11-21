# SPPS Version 1.0 - Free — decisions made during the build

Phases 1–9 built the "v2" stack that became this release; the release decisions (R-1 to R-4) are at the end.

Each entry: what was unclear, what was decided, and why. Newest phase last.

## Platform (all phases)

- **D-001 Reference stays read-only; v2 starts as a copy.** The uploaded `spps-ready` export was unpacked into its
  own folder and never modified. `spps-v2` is a byte copy of it plus the changes listed in PROGRESS.md.
- **D-002 New code lives in `server/v2/`.** Each phase is a route-group module registered from `create_app` through
  `server/v2/__init__.py`. The v1 modules are edited only where a v1 flow must call into v2 (sign-in exchange,
  approval decision, startup checks). This keeps the proven v1 code recognisable and diffable.
- **D-003 Separate declarative bases.** v2 clinical tables are on `V2Base` (`server/v2/models.py`) so the v1
  metadata keeps its documented 33 tables (v1 contract tests unchanged). References from v2 tables to v1 tables are
  plain integer columns in the ORM; the real foreign keys, FORCE RLS and grants are in the SQL migrations.
- **D-004 Startup RLS count.** `server/identity.check_role` required exactly 33 FORCE-RLS clinical tables. It now
  requires `33 + len(V2Base tables)`, so every v2 table must also be FORCE RLS or the app refuses to start.
- **D-005 One allowlist for v2.** `server/v2/allowlist.py` is consulted first by the default-deny check. A v2 route
  whose feature flag is off is refused even if registered; any path under a v2 prefix that is not listed is refused.
  A test checks that every registered v2 route is listed.
- **D-006 Serial SQL is generated, never rewritten.** `tools/v2_serial_sql.py` writes `supabase/sql/NN_XXXX.sql`
  from `migrations/XXXX.sql` (header + `migration_history` row, same format as v1 step 33) and fails if an existing
  serial file would change. Numbering continues the v1 convention: serial step = migration number + 1.
- **D-007 Throwaway PostgreSQL without Docker.** The workspace has no Docker daemon, so the v1 `spps-test-ephemeral`
  container cannot run here. `tools/v2_pg_harness.py` runs a PostgreSQL 16 server from `/tmp` on
  `127.0.0.1:55437`, applies the serial set, and is destroyed with `down`. Databases are named `spps_dev_v2*` so the
  v1 `require_local_database` guard accepts them and the real app can run against PostgreSQL in its isolated profile
  (`tests_v2_pg/`). The v1 Docker-based tiers (`tests_identity`, `tests_integration`, `tests_e2e`) are kept unchanged
  and were not run here.
- **D-008 Owner address.** The owner e-mail is read from the single v1 constant (`server/owner_profile.py`) or
  `SPPS_OWNER_EMAIL`; v2 code and tests never repeat it.

## Phase 1

- **D-101 How a pending application is bound to an unconfirmed account.** At sign-up there is no verified session,
  so the server cannot know the Supabase subject. The server returns a random *claim* (only its SHA-256 is stored);
  the browser puts it into `user_metadata.spps_claim` at `signUp`. GoTrue returns user metadata with the verified
  user, so whichever browser later holds a confirmed session presents the claim and the server promotes the matching
  row — but only if the e-mail also matches. A stranger who learns a claim cannot use it with another address.
- **D-102 Never overwrite a pending row.** Every sign-up attempt inserts a new `applied` row (rate limited per IP and
  per hashed e-mail), so nobody can replace another person's pending text before they confirm. Unconfirmed rows
  expire after 7 days and are purged on the next submission.
- **D-103 Promotion accepts the e-mail-link session.** Promotion only moves an application into review; it grants
  no access. So `/promote` accepts a confirmed session that did not use a password (the link session). Signing in to
  the workspace still requires the password session, as in v1.
- **D-104 "Applied → email verified → under review" happen together.** The two system transitions are written in
  the same transaction at promotion, so the audit shows both steps with the same time.
- **D-105 Bot check without a third-party captcha.** A signed, time-stamped form token (HMAC, minimum 3 s fill time,
  2 h maximum) plus a honeypot field. No external script, so it works in the Android WebView and needs no new CSP
  entry. `SPPS_FORM_TOKEN_SECRET` is required in hosted mode (the preflight refuses to start without it).
- **D-106 Rate limits are per process.** Same model as v1. Render runs one instance per service; DEPLOYMENT.md says
  to add a gateway limit before scaling out.
- **D-107 Owner badge = applications in `under_review`.** That includes applicants who answered a "more information"
  request (they move back to `under_review`), so the badge counts everything waiting for an owner decision.
- **D-108 Deciding an unverified application is impossible.** The review queue lists practitioner profiles, and a
  profile only exists after promotion (verified e-mail). The state machine also refuses an owner decision on an
  `applied` or `email_verified` row.
- **D-109 Email delivery log stores the failure class only** (`mail_not_configured`, `smtp_error`), never the
  address, subject, body or server reply. A new failure supersedes older failed rows for the same practitioner, so
  the owner sees one resend button per person.
- **D-110 Request ids.** A context variable holds the id for the whole request and a logging filter stamps it on
  every record from any logger; a last-line redactor masks e-mail addresses and token-like strings in every message.
  Access log paths mask token-like segments (invite and report links in later phases).
- **D-111 Health check.** `/health/ready` now also requires every v2 table, so a release whose SQL has not been run
  is never marked ready and Render keeps the previous instance. Render health-check path: **`/health/ready`**.
- **D-112 Android assets without the Capacitor CLI.** `npx cap sync` is not available offline here; the new
  `npm run android:web` (stage + `scripts/sync-android-web.mjs`) copies `www/` into the Android asset folder exactly
  as `cap sync` does for web files. Native files and package ids are not touched.

## Phase 2

- **D-201 Access logging point.** Every v1 and v2 route that opens an athlete record goes through `athlete_for`
  (cases, notes, observations, reports and timelines call it too), so the audit hook sits there and in the caseload
  list. Entries are buffered per request and written after the response is decided, in their own transaction as
  the same practitioner, only for successful requests, one row per athlete per request. A failed audit write is
  logged loudly; it does not turn a completed clinical action into an error.
- **D-202 Owner sees numbers only.** The owner's view is a SECURITY DEFINER aggregate (reads/writes/exports per
  practitioner). The owner has no SELECT on the log rows themselves (they name athletes and routes).
- **D-203 Export format.** One ZIP: `export.json` (the v1 export plus every v2 table, registered in
  `trust.EXPORTERS` by later phases), `csv/<record type>.csv` (spreadsheet-safe), `README.txt`. Streamed directly
  after password re-authentication (v1 `reauthenticate`), rate limited to 5 per hour; only the hash and size are
  stored. The asynchronous signed-link delivery in the design record is not built (single-instance Render, files
  are small); recorded as a gap in PROGRESS.md.
- **D-204 Closure scope.** Implements the agreed design A3/A4 for the practitioner side: start (password), notice
  window (`SPPS_CLOSURE_NOTICE_DAYS`, default 30), a disposition per athlete record (export-then-erase, or keep with
  the account deactivated), blockers (retention hold, missing dispositions, no export since starting when anything
  will be erased, notice window not over), cancel, complete. Completing writes the owner archive (professional
  data and closure record only), deactivates the account, hides the listing and revokes sessions in one
  transaction. Erasure runs only after the cooling-off period (`SPPS_CLOSURE_COOLING_OFF_DAYS`, default 14), when
  the owner presses Purge; the owner can restore inside the cooling-off period. Transfer to another practitioner and
  sealed custody need the portal and counsel's wording (design phase B) and are not offered. Behind
  `SPPS_PRACTITIONER_CLOSURE_ENABLED` (off).
- **D-205 Erasure function is not callable by any application role.** `v2_erase_athlete_records` is executed only
  inside `v2_owner_purge_closure`, which checks owner, tenant, state and cooling-off itself. Later migrations that
  add athlete-linked tables replace the erase function in their own new file.
- **D-206 Field encryption.** AES-256-GCM in the application (`server/v2/fieldcrypt.py`), associated data = column
  name, stored as `enc:v1:<key id>:...` in the existing text columns, so no schema or policy change was needed for
  `notes.content`. Hosted startup refuses to run without a valid `SPPS_FIELD_KEYS`/`SPPS_FIELD_KEY_CURRENT`.
  Rows written before encryption stay readable and are encrypted by the rotation tool. Rotation needs a privileged
  connection run by the owner (the app role has no UPDATE on note content by design).
- **D-207 "No AI wording" means app screens.** With the switch off, no AI route answers (404) and no app screen
  contains the word. The legal documents (privacy notice, terms, "Your data") keep their promise that data is never
  used to train AI models — that is a statement about data use, not a feature. Four v1 UI phrases were reworded
  ("No AI diagnostics" → "Practitioner-led decisions", etc.); no layout, class or style changed.
- **D-208 Global rate limits.** Every `/api/public/`, `/api/auth/`, portal-auth, invite and report-link path has a
  per-IP ceiling in the request middleware on top of the per-endpoint limits. A repository `conftest.py` resets the
  windows between tests because every test client shares one address.
- **D-209 v2 screens live in `web/v2.js`.** `app.js` exposes a frozen `window.SPPSHost` with its existing helpers
  and delegates `v2:` actions/forms, extra cards and extra views to `v2.js`. Only existing CSS classes are used. Lists
  on the Admin Dashboard use `strip` rows (not a second data table) so the v1 dashboard layout is unchanged.

## Phase 3

- **D-301 Notes stay in the v1 `notes` table.** Signing is the v1 `draft → final` transition plus a
  `note_signatures` row holding the SHA-256 of the signed text; the note view reports whether the text still
  matches. Amendments are separate append-only rows; a signed note never changes. Drafts are edited until signed.
- **D-302 Server autosave.** Drafts live in `note_drafts` (encrypted), keyed by a browser-generated id with an
  optimistic revision, so a second window cannot silently overwrite the first (409). Committing a draft creates the
  note and removes the draft.
- **D-303 Offline draft.** When the server cannot be reached the composer encrypts the draft in the browser
  (WebCrypto AES-GCM) with a per-practitioner key that the server derives from the field key (`/api/v2/offline-key`)
  and the browser keeps in memory only. The stored copy is therefore unreadable without signing in again, and is
  sent automatically when the browser is online.
- **D-304 Upload as JSON/base64.** The import takes the file as base64 in JSON (2 MB, 1000 rows), like the v1 photo
  upload, so no multipart dependency is added to the hosted image. CSV (comma/semicolon/tab) and .xlsx are read.
- **D-305 Import is all-or-nothing.** Preview checks every row (codes, duplicates in the file and caseload, level,
  age group, guardian contact for under-18s, e-mail, consent yes/no, score range and date). Commit re-checks and
  writes everything in one transaction or nothing; the staged rows are cleared after commit or discard. Past scores
  are imported against the practitioner's own monitoring/assessment measures (v1 `factors`) — instrument scores
  arrive in phase 4. Empty score cells stay missing; nothing is filled in.
- **D-306 Age group instead of date of birth.** The profile records an age group; under-18 groups require a
  guardian name and an e-mail or phone (also a database CHECK). Withdrawing consent clears the v1 consent stamp, so
  no new case can be opened.
- **D-307 Formulation.** One versioned 5Ps record per athlete (append-only versions, optimistic version check),
  encrypted. The screen states that SPPS never generates formulation text; there is no generation code.

## Phase 4

- **D-401 New instrument tables beside the v1 offline assessments.** v1 `assessment_administrations` (free-text
  instrument names) stay untouched and still appear on the timeline; v2 instruments are defined as data in
  `instruments`, `instrument_subscales`, `instrument_items`, with administrations, responses and scores separate.
- **D-402 Published tests: names and subscale ranges only.** CSAI-2, SMS-28, POMS and ACSI-28 are seeded with
  their commonly published subscale names and score ranges, licence `licensed_score_entry_only`,
  `licence_confirmed=false`, not assignable to athletes. No item text is shipped. The ranges and the licensing
  position are on LAUNCH_CHECKLIST.md for the owner to confirm. A trigger refuses item text for any licensed
  instrument (even as the database owner), the API has one item serializer that drops text for licensed
  instruments, and practitioners cannot create licensed instruments or change a platform licence.
- **D-403 Missing is missing.** A subscale with any unanswered item is stored as NULL; a licensed subscale left
  empty is NULL. Trends list every dated administration, return the missing dates per subscale and state
  `interpolated: false`. The chart joins two points only when no administration between them lacks a score.
- **D-404 Rounding.** Means are rounded to 2 decimal places; sums are exact.
- **D-405 Band edges are inclusive** (`min <= score <= max`); a score outside every band has no label (no guess).

## Phase 5

- **D-501 New report tables; v1 `reports` untouched.** v1 private drafts stay as they are. v2 reports
  (`shared_reports`) are built from practitioner templates whose fields carry audience tags; an empty tag list
  means clinical-only.
- **D-502 One redaction point.** `reports.render(audience)` copies only fields tagged with that audience into the
  output; preview, signed snapshot, PDF and shared link are all produced from it. Tests plant markers in
  clinical-only and medical-only fields and check the coach JSON, the PDF text (pypdf) and the shared page.
- **D-503 Signing freezes the snapshot.** The rendered snapshot and its SHA-256 are stored (encrypted) at signing;
  the database refuses updates to a signed report. PDF download and links serve the frozen snapshot, so later
  assessment entries do not change a report that has been shared.
- **D-504 Consent per audience.** Recorded by the practitioner (written/verbal) for one audience; for an under-18
  athlete only guardian consent counts. Phase 8 adds athlete-given consent through the portal.
- **D-505 Link semantics.** 32-byte random token, only its SHA-256 stored, 1–30 days (database limit 31), revocable;
  expired, revoked, consent-withdrawn and unknown links all answer 404 with no content. Anonymous access only via
  `v2_open_report_link()`, which records every attempt on a known link. Behind `SPPS_REPORT_LINKS_ENABLED` (off).
- **D-506 Charts in shared reports** are server-rendered SVG with the same sparse-data rules as the workspace
  chart; the PDF shows the same data as a dated table (missing rows labelled "missing").

## Phase 6

- **D-601 Loop accounts are new identity-schema tables, not v1 `platform_accounts`.** The v1 portal reaches its
  tables only through ~100 SECURITY DEFINER functions; giving the loop direct access to them would widen v1 grants.
  The loop therefore keeps its own `loop_*` tables in the same `spps_identity_dev` schema, owned by
  `spps_identity_owner`, reached only by the two existing identity roles: `spps_identity_auth` for
  pre-authentication work (same posture as v1 `spps_dev_auth`) and `spps_identity_portal` for data under policies
  keyed on the server-set request identity. A later migration can merge accounts with `platform_accounts`.
- **D-602 Both switches.** Routes exist only when `SPPS_PORTAL_ENABLED` and `SPPS_ATHLETE_LOOP_ENABLED` are both
  true (and are refused by the allowlist otherwise). The v1 portal needs PostgreSQL, so the SQLite API tests patch
  the portal switch; the PostgreSQL tier runs the app with both real flags.
- **D-603 Consent and minors.** The consent screen lists exactly what the practitioner will see. For an under-18
  athlete the invitation goes to the guardian (profile e-mail by default); the guardian's consent is recorded with a
  typed-name signature as evidence, then the guardian gives the athlete's e-mail and the athlete is invited. This
  is "recorded" guardian consent; identity verification of the guardian beyond e-mail control is a counsel item
  (LAUNCH_CHECKLIST.md).
- **D-604 Guardian view.** Participation counts (through a definer function that returns numbers only) and
  summaries the practitioner releases. Raw answers become readable to the guardian only when both the practitioner
  and the athlete switch it on; the database policy checks both flags.
- **D-605 Ending a link** sets status `ended`; every athlete/guardian policy requires a non-ended link, so access
  stops with the same statement. The athlete keeps their account and can export their own data.
- **D-606 Check-ins.** 3–5 of stress, confidence, anxiety, mood, motivation (0–10), weekly by default or daily, one
  per period; earlier periods without a check-in are reported as missing. Reminders are e-mailed when the owner (or
  a scheduler calling the endpoint as the owner) runs `/api/v2/admin/loop/send-reminders`; no background worker
  was added (single Render instance).
- **D-607 Alerts are transparent rules** (`item >= / <= threshold`) set by the practitioner; the urgent question
  always raises an urgent alert whatever the scores; alerts appear in the workspace and are e-mailed.
- **D-608 Assigned instruments are copied** (items, scale, scoring) into the assignment when assigned, so portal
  code never reads the clinical instrument tables; only instruments marked assignable (own/open) can be assigned.
- **D-609 Athlete page.** `/athlete` (`web/athlete.html`, `web/athlete.js`) uses the existing stylesheet; invitation
  and verification links point there and work in any browser. The v1 `/portal` page is unchanged.

## Phase 7 — directory

- **D-701 Snapshot read model.** Public directory endpoints read only `directory_snapshots`, a row written when the
  owner publishes. Profile edits after publication do not reach the public until the owner publishes again. The
  public role has SELECT on snapshots only, and only rows whose verification date is current.
- **D-702 Unlisting is automatic.** Database triggers delete the snapshot when the practitioner is deactivated or
  the practitioner profile leaves `approved`; the API also calls `remove_from_directory` on rejection and closure.
- **D-703 Badges carry a date** and expire 12 months after the check date; the owner screen lists profiles due for
  re-verification within 30 days. Expired badges stop the listing appearing until the owner re-verifies.
- **D-704 Enquiries are non-clinical.** The message is limited to 500 characters and refused when it contains common
  clinical terms (diagnosis, medication, self-harm and similar); the refusal explains that clinical details are
  shared privately later and shows crisis contacts.
- **D-705 Anonymous enquiry insert** uses an INSERT without RETURNING so the public role needs no SELECT on enquiries.
- **D-706 Crisis contacts for the public directory** come from `SPPS_CRISIS_CONTACTS_TEXT` (no tenant is known on
  a public page); the in-app crisis contact list (phase 6) remains for linked athletes.
- **D-707 No ranking.** Results are alphabetical; there are no reviews, ratings, prices or payments.

## Phase 8 — self-serve accounts

- **D-801 Accepting an enquiry** requires the practitioner to tick that the service was agreed and consent recorded
  (that sets `service_consent_recorded_at`, as v1's enquiry conversion does). The record is committed before the
  invitation is sent, so a mail failure leaves a record the practitioner can invite from.
- **D-802 Account enquiries** go through exactly the same anonymous insert, bot check and limits as the public
  form; a random reference ties the practitioner's inbox row to the account's own copy in the identity schema
  (`loop_requests`). Outcomes reach the account copy through the server-side auth role.
- **D-803 Invitations for an account** are matched by address and role and accepted from the account itself; the
  e-mailed token link still works. An account-first enquiry always invites the sending account, whatever address
  the practitioner types.
- **D-804 Switching** sends the new enquiry first, so a refused or throttled enquiry leaves the current link open.
  After a link ends its data stays with that practitioner; the athlete's copy is the export (offered on screen).
- **D-805 Deletion** deletes the account, its sessions, every invitation to its address and its enquiry copies;
  links are ended and the account id removed; check-ins and answers already shared stay attached to the ended link
  as part of that practitioner's record (their retention duties apply). The enquiry message in a practitioner's
  inbox is correspondence they received and is not altered.
- **D-806 Deactivation** blocks sign-in and reminders and leaves links as they are; reactivation is by e-mail link.
- **D-807 Athlete-approved coach links live in the identity schema** (`loop_share_requests`): the practitioner
  copies the signed coach-level snapshot (encrypted, rotation tool covers it); the approver decides there; the
  public `/s/{token}` page reads it through the server's auth role. No portal or public code touches clinical
  tables, and phase 5's recorded-consent links are unchanged. Only coach-audience reports qualify.
- **D-808 Transitions are enforced in PostgreSQL**: a trigger allows only approver approve/decline/withdraw (the
  approval must name the approver's own account), practitioner revoke, and auth-role withdraw on deletion; column
  grants stop anyone changing the snapshot or recipient after the request. Ending the link closes the coach link.

## Phase 9 — release

- **D-901 v2 verification file is unnumbered** (`supabase/sql/v2_verify.sql`): a v1 contract test requires the
  numbered serial steps to be gap-free with only `98`/`99` outside them, and that test is not weakened.
  `MIGRATIONS.md` lists it after step 41.
- **D-902 Every v2 migration is required at start-up**, whatever the flags: the SQL only adds objects, so "SQL first,
  then code" is always safe and a half-migrated database never serves traffic.
- **D-903 Hosted mail is required for the athlete loop and self-serve** (invitations, verification, reactivation and
  coach links are e-mailed); the preflight refuses to start without SMTP and an https public base URL.
- **D-904 Audit writes use their own pool** (found while taking screenshots after phase 9): the record-access audit
  row is written by the middleware while the audited request still holds its connection; with several record
  requests in parallel the shared pool (2 connections hosted) ran dry and requests stalled until the pool timeout.
  `app.state.AuditDB` is now a separate one-connection pool (+1 overflow) on the same role, and the identity is set
  on that transaction directly. Regression test: `tests_v2/test_v2_audit_pool.py`.

## Release 1.0 - Free

- **R-1 Operator details come from `server/owner_profile.py` when a legal page is served** (`server/v2/pages.py`), so
  the contact and grievance address are never duplicated in HTML. Postal address not included (not provided);
  flagged in LAUNCH_CHECKLIST for counsel.
- **R-2 Internal identifiers keep their names** (`SPPS_BETA_V1_FREE`, `beta_routes.py`, `model-c-demo` default
  tenant slug for local runs, CSS class names, Android package `…beta15`): renaming them would change hosted
  configuration or package ids for no user-visible gain. Production refuses any tenant other than `winmindperform`.
- **R-3 The v1 athlete/guardian portal file outbox (`SPPS_PORTAL_DEV_OUTBOX`) stays**: it is refused in hosted mode and
  the v1 Docker integration tests depend on it.
- **R-4 Tests stay in the folder** (owner's choice) but are excluded from the Docker image and the APK.

- **R-5 Practitioner standing gates report links** (0041 + `practitioner_in_good_standing`): inactive, rejected or
  pending-approval practitioners' links answer 404 and log `revoked`; reactivation restores them because the
  consent and expiry checks still apply. Closure is final: hooks revoke links and shares and end athlete-app links,
  and a restored account starts with none.
- **R-6 Closure side effects are hooks** (`trust.add_closure_hook`), registered by the reports and athlete-loop
  modules, so trust.py does not import feature modules and a failed hook is logged without undoing the closure.
- **R-7 Guardian authority is a declaration plus typed signature**, stored as evidence and shown to the
  practitioner. SPPS does not check identity documents; the policy and the practitioner's own safeguarding duty
  cover that (Policies B.2, B.7).
- **R-8 Tokens leave the address bar at once** and live only in the tab's sessionStorage until used; every token page
  and API response is `no-store` and `noindex`. Links stay as query strings so they keep working when opened in
  another browser or in Chrome from the Android app.

## Booking-only directory (supersedes D-704, D-705, D-801 to D-805 where they concern enquiries)
- **D-1001 Enquiry is removed entirely.** There is no enquiry form, inbox, auto-reply, enquiry stats, account enquiry,
  accept/decline-enquiry or switch-by-enquiry. Directory tiles lead to "Book appointment" (the athlete portal).
  Invitations (practitioner invites an athlete) and report sharing are unchanged.
- **D-1002 Expand/contract.** The code stops using the enquiry objects first; the owner then runs migration `0042`,
  which refuses to run (drops nothing) while any enquiry row exists. Forward-only: there is no down migration.
- **D-1003 Startup check is name-based.** `check_role` verifies that every ORM table exists and is FORCE RLS and that
  no schema table lacks it, instead of a hard-coded table count, so it passes before and after `0042`.
- **D-1004 Kept deliberately:** programme enquiries and organisation quote requests (separate marketplace feature)
  until the owner decides otherwise.
- **D-1005 Directory booking creates a pending 13A link without a clinical record** (phase 2, owner decision
  2026-11-18). `practitioner_athlete_links.athlete_record_id` is NULL only while a directory link waits
  (`PRACTITIONER_REVIEW`, `origin='directory'`); a CHECK keeps every ACTIVE link tied to a record. When the practitioner
  approves the first booking, the server creates a minimal athlete record (display name only) under the practitioner's
  own RLS, attaches it, activates the link and confirms the booking through the unchanged
  `free_practitioner_decide_booking`. Declining closes the pending link. Until approval the practitioner sees display
  name, service, time and the requester's note.
- **D-1006 Services reach the public card through their own projection** (`directory_service_snapshot`), written by
  the practitioner for their own active services and readable by anyone only while `directory_snapshot` lists them.
  `directory_snapshot` stays owner-written; practitioners never get write access to badges or verification dates.
- **D-1007 `SPPS_DIRECTORY_BOOKING_ENABLED`** (default off) gates the two request endpoints and needs
  `SPPS_PORTAL_ENABLED` and `SPPS_DIRECTORY_ENABLED`. Service management itself is not flagged. The endpoints live under
  `/api/portal/` because the portal session cookie is scoped to that path; they are still listed (flag-gated) in the
  v2 default-deny allowlist.

## Booking-only Phase 4: notifications for every role

- **D-1101 One outbox, three channels.** Every event already lands in `platform_notifications` (migration 0009). Phase 4
  adds no second queue: the delivery pass sends mail as before and, when `SPPS_PUSH_ENABLED` is on, a content-free Web
  Push (browsers) or FCM message (Android app) to each active device. A notification counts as delivered when any channel
  succeeded and is retried with backoff only when every attempted channel failed.
- **D-1102 Devices, preferences and the first-sign-in answer live in the identity database** (four owner-owned, FORCE
  RLS tables, no application grants). Athletes and guardians are identified by their portal session; the owner, admins and
  practitioners by their signed-in practitioner id. Handlers never take the owner from the request body. No function the
  application calls for a person returns a stored subscription; only `notification_push_targets` does, for the worker.
- **D-1103 Subscription endpoints are allow-listed** to the browser vendors' push services (`fcm.googleapis.com`,
  `push.services.mozilla.com`, `push.apple.com`, `notify.windows.com`; https, port 443, no credentials). The server POSTs to
  the endpoint, so an open field would be a server-side request forgery hole.
- **D-1104 Messages carry no content:** a fixed title per kind, one generic line and the page to open.
- **D-1105 Categories:** appointments, programme and task updates, account and security. The last cannot be switched off
  (CHECK constraint and API). A preference filters push only; mail for important updates keeps going.
- **D-1106 The pop-up follows the server's answer:** none when a device is active; "Not now" asks again after seven days;
  none when the browser has blocked notifications (the card explains how to allow them). No browser storage is used.
- **D-1107 The delivery loop runs inside the web service** (`SPPS_NOTIFY_WORKER_ENABLED`) because Render runs one service
  and `tools/notify_worker.py` was never deployed, so queued mail never sent. It is safe beside the script or a second
  instance (leased claims) and reminders are idempotent through a ledger row per appointment.
- **D-1108 Reminder window** is `SPPS_REMINDER_MINUTES` (default 60): one reminder per confirmed appointment to each side.
- **D-1109 Android push** uses FCM HTTP v1 from the server (`SPPS_FCM_SERVICE_ACCOUNT_JSON`). The app side (Capacitor push
  plugin, `google-services.json`, a new APK) is a separate owner-run step.
- **D-1110 `SPPS_SMTP_FROM` may be a plain address or `Name <address>`; `SPPS_SMTP_SENDER` is optional** and wins when set.

## Guest booking: ask for an appointment without an account

- **D-1201 The request comes first, the account second.** A visitor gives a name, an e-mail address, a service and a
  time. Nothing is booked and no account exists until the address is proven by a one-time link. The press of a button on
  that page (not the page load) confirms it, so mail scanners that open links cannot confirm on someone's behalf.
- **D-1202 Confirmation creates an account shell,** VERIFIED (the mailbox was proven), with a random password hash nobody
  can match, the adult attestation from the form, the ATHLETE role, the consents the form showed (terms, privacy, case
  link, appointment booking, and video for an online service) and the usual PENDING_APPROVAL booking. From there the
  practitioner's workflow is exactly the signed-in one.
- **D-1203 The approval e-mail carries the create-account link.** The delivery worker issues a single-use 14-day claim
  token only for a shell account that has not been claimed; the message contains no names, times or details.
  Choosing a password sets `claimed_at`; the person then signs in and finds the appointment, the link and the video room.
  "Forgot password" also works for the address, so a lost link is never a dead end.
- **D-1204 Neutral answers.** The request answers the same whether the address is new, already has a claimed account
  (it then receives a "sign in instead" notice) or has too many open requests, so the form cannot be used to find out who
  has an account. Limits: 10 requests per hour per IP, 3 per hour and 3 open per address.
- **D-1205 Adults only.** The form attests 18+. A minor is booked by a verified guardian from an account, as before.
- **D-1206 Unconfirmed requests are purged** a week after they expire (name and address are not kept for strangers).
- **D-1207 Video opens 15 minutes before the start** (migration 0047 replaces the 0045 function; `SPPS_REMINDER_MINUTES`
  now defaults to 15, so the reminder and the button agree). The practitioner may enter first; a guest who arrives first
  waits in the room (the existing signalling already allows either order). The room shows who is waiting for whom.
- **D-1208 Flag `SPPS_GUEST_BOOKING_ENABLED`** (default off) needs the portal, the directory, directory booking and the
  notification worker. Public routes: `GET /api/v2/public/directory/{id}/slots`, `POST /api/v2/public/guest-bookings`,
  `POST /api/v2/public/guest-bookings/confirm`, `POST /api/v2/public/guest-claim`.
