# SPPS Version 1.0 - Free — build progress

This release was built in nine phases (the "v2" stack) and then prepared as SPPS Version 1.0 - Free; the release
section is at the end of this file. Phase names and the `v2` labels in file and module names refer to that build.


Resume rule: read this file first and continue from the first phase that is not **done**. A phase is done only when
its acceptance points pass in real tests. Test counts below are copied from real runs.

How to run everything:

```
python -m pytest tests tests_v1 tests_v2 -q          # v1 unit/contract + v2 API + browser tests (SQLite, Chromium)
python tools/v2_pg_harness.py up                     # throwaway PostgreSQL 16 on 127.0.0.1:55437, serial SQL applied
python -m pytest tests_v2_pg -q                      # row-level-security + app-over-PostgreSQL tests
python tools/v2_pg_harness.py down                   # stop and delete the throwaway database
```

| Phase | Status |
|---|---|
| 1 Registration and operations baseline | done |
| 2 Trust foundation | done |
| 3 Practitioner record | done |
| 4 Assessments over time | done |
| 5 Reports for coaches and the wider team | done |
| 6 Practitioner-invited athlete loop | done |
| 7 Find a Sports Psychologist directory | done |
| 8 Self-serve athlete and guardian accounts | done |
| 9 Final export | done |

## Phase 1 — done

Built:
- Server-side pending application (`server/v2/registration.py`, migration `0033`): stored at sign-up behind a
  signed form token, honeypot and rate limits; bound to the Supabase account by a claim in `user_metadata`;
  promoted on the first authenticated call from any browser or the app (`/promote`, the sign-in exchange or the
  application status check). v1 carried-details fallback kept. Photo can be added after confirmation.
- Registration state machine applied → email_verified → under_review → more_info_requested → approved/rejected,
  every transition in `registration_transitions` (who, when). Owner decisions and applicant resubmissions move it.
- Admin Dashboard: new-application count badge, "Confirmed, no application yet" list, undelivered review e-mails
  with a Resend button (`email_deliveries`).
- Structured request logging: request id on every response and on every log line; e-mail addresses and token-like
  strings are redacted from every log message.
- `/health/ready` also requires the v2 tables. Render health-check path: `/health/ready`.
- v2 platform: `server/v2/` package, separate `V2Base`, default-deny v2 allowlist, feature flags (all off), v2
  preflight (hosted startup refuses a missing `SPPS_FORM_TOKEN_SECRET`, bad flag values, unmet flag dependencies or
  un-run v2 migrations), serial-SQL generator, MIGRATIONS.md generator, throwaway PostgreSQL harness, export builder.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **413 passed, 1 skipped** (v1: 383 passed, 1 skipped — unchanged;
  v2: 30 passed, of which 4 are real-Chromium browser tests).
- `python -m pytest tests_v2_pg -q` on a fresh throwaway database with all 35 serial files → **6 passed**.
- Acceptance covered: sign-up in one browser and confirmation in another reaches the Admin Dashboard without
  retyping (API, browser and app-over-PostgreSQL tests); a confirmed account with no application is listed for the
  owner. Negative: a practitioner cannot read another applicant's pending application (API 403 and PostgreSQL RLS:
  zero rows, zero updates); unverified accounts never reach review (API and PostgreSQL).

v1 test files touched (fixture additions only, no assertion changed): `tests/test_hosted_profile.py` and
`tests/test_production_phase1_contract.py` gained the fictional `SPPS_FORM_TOKEN_SECRET` their hosted env needs.

Remains for later phases: nothing in phase 1. The v1 Docker tiers (`tests_identity`, `tests_integration`,
`tests_e2e`, Android build) were not run in this workspace (no Docker daemon, no Android SDK); see DECISIONS D-007.

## Phase 2 — done

Built:
- Record-access audit (`record_access_log`, migration `0034`): every read and write of an athlete record (all v1 and
  v2 routes through `athlete_for`, plus the caseload list) stores who, what (route), when and the request id —
  never content. Practitioners see the log per athlete ("Access log" button); the owner sees counts only.
- Practitioner data export: one ZIP (JSON + one CSV per record type + README) after password re-authentication;
  each export's hash and size recorded. Contains only the requesting practitioner's data.
- Orderly account closure (flag `SPPS_PRACTITIONER_CLOSURE_ENABLED`, off): start with password, notice window,
  disposition per athlete record, blockers, cancel, complete; owner archive with professional data only; owner
  restore within cooling-off and purge of erased records after it.
- "Your data" page (`/your-data`) from one editable file `web/your-data.html`, linked from Profile & Privacy.
- Global AI switch `SPPS_AI_FEATURES_ENABLED=false` + per-practitioner opt-in (`require_ai` is the only gate);
  with the switch off every AI route answers 404 and no app screen contains AI wording.
- Field-level encryption (AES-256-GCM) for consultation notes (formulations use it from phase 3), with a documented
  key-rotation tool (`tools/v2_rotate_field_key.py`); hosted startup refuses to run without keys.
- Per-IP rate limits on every public and auth endpoint, on top of the per-endpoint limits.
- UI: `web/v2.js` (loaded after `app.js`, existing classes only) — Your data, export, closure cards; athlete access
  log; owner access counts and closures.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **433 passed, 1 skipped** (v1: 383 passed, 1 skipped; v2: 50
  passed, of which 7 are real-Chromium browser tests).
- `python -m pytest tests_v2_pg -q` on a fresh throwaway database (36 serial files) → **12 passed**.
- Acceptance covered on PostgreSQL: practitioner A cannot read practitioner B's athletes, cases, notes, access-log
  rows or export records; the owner role reads no clinical rows (and the auth role cannot select note content);
  the access log is append-only (no UPDATE/DELETE); closure state cannot be forged; erase/purge functions are not
  executable by other roles; an export over RLS contains only the requester's data; notes are ciphertext at rest.

Gaps carried forward: export is streamed directly rather than built asynchronously behind a signed link (D-203).

## Phase 3 — done

Built (migration `0035`, `server/v2/record.py`, screens in `web/v2.js`):
- Athlete profile: level, age group, guardian contact (required under 18, also enforced by the database), consent
  status (withdrawal blocks new cases), referral source and encrypted referral notes.
- Consultation notes: practitioner templates; server autosave with revision conflict detection; encrypted offline
  draft in the browser that syncs when back online; sign-and-lock with a content hash; amend-with-history.
- Import: CSV or Excel → map columns on screen → preview listing every bad row → all-or-nothing commit; optional
  past scores against the practitioner's own measures.
- 5Ps case formulation, practitioner-written, versioned, encrypted.
- Per-athlete timeline: notes, amendments, measures, assessments, interventions, reports, formulations.
- Export and closure purge include every new table.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **445 passed, 1 skipped** (v1: 383 passed, 1 skipped; v2: 62
  passed, of which 9 are real-Chromium browser tests).
- `python -m pytest tests_v2_pg -q` on a fresh throwaway database (37 serial files) → **16 passed**.
- Acceptance: 25 fictional athletes imported from CSV and from Excel (API and over PostgreSQL RLS), a note written,
  signed/locked and amended, visible on the timeline. Negative: every new route refuses another practitioner's ids
  (12 routes, 404) and every new table is private on PostgreSQL (practitioner A and the owner read zero rows of B's);
  signatures/amendments only on one's own final note; amendments and signatures cannot be changed or deleted.

## Phase 4 — done

Built (migration `0036`, `server/v2/assessments.py`, screens in `web/v2.js`):
- Instruments as data: items, response scale and labels, per-subscale scoring (sum/mean, reverse-scored items),
  interpretation bands, validation status, licence (own / open / licensed-score-entry-only), assignable flag.
- Custom instrument builder with consistency checks (scale, labels, subscale membership, band ranges).
- Score entry: item by item for own scales; subscale score entry for published tests. CSAI-2, SMS-28, POMS and
  ACSI-28 shipped as name + subscale score entry only, flagged for the owner to confirm licensing (Admin Dashboard).
- Sparse-data trends per athlete: every dated point, missing marked, no interpolation, no line across a gap.
- Timeline, export and closure purge include instrument data.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **474 passed, 1 skipped** (v1: 383 passed, 1 skipped; v2: 91
  passed, of which 10 are real-Chromium browser tests; 16 are table-driven scoring/band tests).
- `python -m pytest tests_v2_pg -q` on a fresh throwaway database (38 serial files) → **21 passed**.
- Acceptance: a custom instrument scores correctly (table-driven rules + API + over PostgreSQL); a licensed
  instrument never exposes item text through any API (list, detail, administrations, trends, timeline, owner
  licence route and the export were all checked with a planted marker), and the database refuses such text.

## Phase 5 — done

Built (migration `0037`, `server/v2/reports.py`, screens in `web/v2.js`):
- Practitioner report templates; every field tagged with the audiences allowed to see it (athlete, guardian,
  coach, medical/MDT; none = clinical-only). Server-side redaction in one function.
- Practitioner types the narrative; scores and charts come from the athlete's instrument data. Review, sign
  (snapshot frozen with a hash), then PDF download.
- Sharing (flag `SPPS_REPORT_LINKS_ENABLED`, off): expiring, revocable link requiring recorded consent for that
  audience (guardian for under-18s); every view audited; expired/revoked/withdrawn links return nothing.
- Fix found by the full run: the key-rotation tool now quotes column names (`shared_reports."values"`).

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **483 passed, 1 skipped** (v1: 383 passed, 1 skipped; v2: 100).
- `python -m pytest tests_v2_pg -q` on a fresh throwaway database (39 serial files) → **25 passed**.
- Acceptance: a coach-level report contains no clinical-only (or medical-only) field — checked on the rendered
  JSON, the PDF text and the shared page, in SQLite and over PostgreSQL; expired and revoked links return nothing.

## Phase 6 — done (flags `SPPS_PORTAL_ENABLED` + `SPPS_ATHLETE_LOOP_ENABLED`, both off)

Built (migration `0038` in the identity schema, `server/v2/athlete_loop.py`, `web/athlete.html|js`, practitioner
screens in `web/v2.js`): server-side single-use expiring invitations; athlete/guardian accounts with e-mail
verification; consent screen listing what the practitioner sees; guardian-first flow for under-18s with recorded
consent; assignment of assignable instruments and practitioner-written programmes; check-ins (3–5 items, weekly or
daily, missing marked) with e-mail reminders; transparent alert rules with forced urgent review; alerts in the
workspace and by e-mail; not-a-crisis notice and owner-configured crisis contacts on every athlete screen and
response; guardian view (participation + released summaries; raw only when both enable); either side ends the
link; athlete export of own data; loop activity on the practitioner timeline.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **495 passed, 1 skipped** (v2: 112).
- `python -m pytest tests_v2_pg -q` (two throwaway databases, 40 serial files each) → **34 passed**.
- Acceptance on PostgreSQL: an athlete sees only their own data, a guardian only participation and released
  summaries (raw only with both switches), a practitioner only their own links, ending a link removes access in the
  same transaction, and the portal roles cannot read any clinical table; full invite → consent → check-in → alert
  → end flow over PostgreSQL with both real flags.

## Phase 7 — done (flag `SPPS_DIRECTORY_ENABLED`, off)

Built (migration `0039`, `server/v2/directory.py`, `web/find.html|js`, practitioner and owner cards in `web/v2.js`):
opt-in practitioner profile (sports, levels, age groups, languages, delivery, city, taking new clients); owner
publication separate from registration approval; public read model is a snapshot written on publication; verified
badges with check date, yearly re-verification list and delisting with reason; filters only, alphabetical, no
ranking/reviews/payments; enquiry form (name, contact, role athlete/parent/coach, sport, short non-clinical message)
with form token, honeypot, per-IP and per-practitioner limits and an auto-reply carrying crisis contacts;
practitioner inbox with "responded"; owner overview of response times.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **504 passed, 1 skipped** (v2: 121).
- `python -m pytest tests_v2_pg -q` (throwaway databases, 41 serial files) → **37 passed**.
- Acceptance: every public endpoint's keys are compared with the snapshot field list; unapproved, unpublished,
  delisted, deactivated and expired-badge practitioners never appear (SQLite and PostgreSQL, triggers included);
  enquiries from one address are throttled (429) after five per hour; missing/early form token and filled honeypot refused.

## Phase 8 — done (flag `SPPS_SELF_SERVE_ENABLED`, off; needs the athlete loop and directory flags on)

Built (migration `0040`, `server/v2/self_serve.py`, account screens in `web/athlete.js`, account enquiries and
"switch practitioner" in `web/find.js`, accept/decline and coach-link requests in `web/v2.js`):
1. Practitioner accepts a directory enquiry → athlete record + profile in their caseload, then the phase 6
   invitation (guardian first for under-18s); decline is recorded too.
2. Accounts first: an athlete or guardian account sends an enquiry from `/find`; the practitioner sees only that
   enquiry. Invitations addressed to the account are listed in it and accepted there (no e-mailed link needed), so
   it works in any browser and in Chrome from the Android app. Nothing flows until both sides accept.
3. Switch practitioner: an enquiry goes to the new practitioner first; then the old link ends. The old practitioner
   keeps what was shared; the new one starts with nothing.
4. Lifecycle without anyone's consent: deactivate (reactivate by e-mail), export, delete (account, sessions,
   invitations to the address and enquiry copies deleted; every link ended and severed; approved coach links
   withdrawn).
5. Athlete-approved coach links (also needs `SPPS_REPORT_LINKS_ENABLED`): the practitioner asks, the athlete
   (guardian for under-18s) previews the exact signed coach-level snapshot and approves, declines or later
   withdraws; approval mints an expiring `/s/…` link with a view log. No coach accounts.

Tests (real run, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **521 passed, 1 skipped** (v2: 138).
- `python -m pytest tests_v2_pg -q` (fresh throwaway databases, 42 serial files) → **43 passed**.
- Acceptance: an unlinked account is invisible to every practitioner (API and RLS); deletion removes identity data,
  severs links and closes coach links (SQLite and PostgreSQL end to end); flows run across separate clients standing
  in for Gmail → Chrome → app; the database trigger refuses a practitioner approving on the athlete's behalf.

## Phase 9 — done (final)

- Release preflight (`server/v2/preflight.py`) now covers every flag, variable and migration: always-required
  secrets; per-flag variables (SMTP and an https `SPPS_PUBLIC_BASE_URL` for the athlete loop and self-serve,
  crisis contacts for the directory); flag dependencies; the local outbox switch refused in hosted mode; every v2
  migration's objects checked in the database; `python -m server.v2.preflight` prints the checklist (names only).
- Proof on a brand-new empty local PostgreSQL (`tools/v2_schema_proof.py`, also run as a test): the 42 files of
  `supabase/sql/` in order and the v1 steps plus the 8 raw v2 `migrations/` give identical `pg_dump --schema-only`
  output (6,861 lines) and identical migration history; `supabase/sql/v2_verify.sql` reports 63 / 37 tables,
  95 / 98 policies, 0 tables without forced RLS, 0 API-role access, 0 definer functions executable by PUBLIC.
  The throwaway database was then stopped and deleted.
- `DEPLOYMENT.md` finalised (ordered SQL list, variables with safe examples, flag order athlete loop → directory →
  self-serve, rollback per step, backup-restore drill, PowerShell hosted-APK command); `LAUNCH_CHECKLIST.md` added.

Final test counts (real runs, 2026-11-17):
- `python -m pytest tests tests_v1 tests_v2 -q` → **528 passed, 1 skipped** (reference suites `tests` 376 and
  `tests_v1` 8 collected, unchanged apart from three fixture variables; v2: 145, including the audit-pool
  regression test added after screenshots exposed a pool deadlock, D-904).
- `python -m pytest tests_v2_pg -q` on fresh throwaway databases → **44 passed** (row-level security, flows over
  PostgreSQL with real flags, preflight, schema proof).
- Not run here (no Docker or Android SDK in this workspace; D-007): the reference `tests_identity`,
  `tests_integration`, `tests_e2e` Docker tiers and the Android Gradle build.

## Release — SPPS Version 1.0 - Free (2026-11-17)

Built from the phase 9 stack, with no new features and no database change (the SQL set is unchanged: 42 files).
- Named SPPS Version 1.0 - Free throughout the web app, e-mails, PDFs, API title (version 1.0.0) and Android
  (versionName 1.0.0-free, code 151; package id unchanged).
- Removed everything simulated or test-only from the application: the simulated (“sandbox”) payment provider and its
  checkout route and screen, the local test-data erase/purge routes and their buttons, the v2 local e-mail outbox
  (tests now capture mail themselves), the demo preview launchers, and beta, fictional, draft and local-test wording.
- Terms of Use, Privacy Policy and Platform Policies written in full for India (operator and contact filled in at
  serve time from `server/owner_profile.py`; courts at Patiala); new pages `/policies` and `/guide` (How to use SPPS),
  linked from every footer and the settings screen.
- Owner run-book `START-HERE.md`, `Start-SPPS-Local.ps1/.bat` (runs the real app against Supabase from `.env.local`),
  `Build-SPPS-1.0-Free-APK.ps1`, `test_windows.bat`; test folders excluded from the Docker build context.

Metadata pass (TypeSafe review): package name/description, document titles and leftover beta labels in code
comments aligned to the release; a test now checks they stay aligned.

Tests (real runs, 2026-11-17): `pytest tests tests_v1 tests_v2` → 531 passed, 1 skipped; `tests_v2_pg` on fresh
throwaway PostgreSQL → 44 passed; Android source tests → 62 passed.


## Hardening — TypeSafe final assessment (2026-11-17)

See `ACCEPTANCE_REPORT.md` for the item-by-item result. In short: report links stop when the practitioner is
inactive, not approved or closes the account (migration 0041, step 43); closure ends athlete-app links and shares;
guardians declare parental/lawful authority; tokens are cleared from the address bar and token pages are never cached
or indexed; a role-matrix test found and fixed one non-uniform 404. No database was started this round.

Tests (real runs, 2026-11-17): `pytest tests tests_v1 tests_v2` → 544 passed, 1 skipped; Android source tests → 62
passed; `tests_v2_pg` written (+2), to be run at the database-testing stage.

## Booking-only directory, phase 1 (enquiry purge)
Removed the four enquiry paths (v1 public, v2 directory, v2 self-serve account enquiries, enquiry-based switching) from
server, web, legal text and tests; added `migrations/0042_v2_appointment_only_directory.sql` (guarded drop; run after the
code is live); startup check made name-based; regression tests in `tests/test_enquiry_purge.py`.
