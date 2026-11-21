# SPPS: prompts for Phases 3, 4 and 5 (give one at a time to the coding agent)

Use each prompt only when the previous phase is merged into main AND its SQL has been run on Supabase AND
v2_verify.sql shows the expected values. Common rules are repeated in each prompt on purpose (each session starts cold).

---

## Phase 3: Appointments screen and video join

```text
You are working in C:\My Computer\Codex\spps-v1.0-free (SPPS v1.0 Free; FastAPI server/, vanilla JS web/, Capacitor Android; Supabase spps-ready-1; Render deploys Karansportpsy/spps-karan main).

Read fully first: CLAUDE.md, docs/BUILD_BRIEF_booking_only.md (Ground rules, Findings, Phase 3, Branch/commit workflow, Report format), DECISIONS.md, MIGRATIONS.md, and the Phase 2 report/commits.

TASK: Phase 3 only (Appointments screen + Join video). Do not start Phase 4.

Pre-flight (stop and tell me if any fails)
1. git status clean; origin = Karansportpsy/spps-karan; Phase 2 merged into origin/main; migration 0043 is on main.
2. Create branch phase3-appointments-video from origin/main.
3. Inspect and report in 5 lines before coding: server/bookings.py states and transitions; server/video.py tickets, consent screen and room.html; ice_config.py and TURN env; where the practitioner menu and athlete portal render; which feature flags gate bookings/video.

Build
1. Practitioner "Appointments" menu: tabs Pending, Approved, Declined, Past. Row = service, date/time (shown in the viewer's local time zone, stored UTC), mode, athlete display name. Pending rows for unlinked people show only display name, service and slot (Phase 2 minimum-data rule).
2. Actions: Approve, Decline (optional content-free reason from a fixed list), Propose new time. Reuse free_practitioner_decide_booking and the reschedule functions; do not rewrite approval-first logic. Approval of a first directory booking must activate the pending link exactly as Phase 2 defined.
3. Athlete/guardian portal "My appointments": status, Accept/Decline a proposed time, Join video. A guardian acts for an under-18 athlete through the existing guardian authority.
4. Join video: shown to both sides only for CONFIRMED online bookings, from 10 minutes before start until end + 15 minutes grace. Enforce the window on the SERVER when issuing the video ticket (not only by hiding the button). Reuse video.py, tickets, the consent screen and room.html; do not change the signalling design.
5. DEPLOYMENT.md: document the TURN requirement; add MODEL_C_ICE_URLS and MODEL_C_TURN_SECRET to the env examples (placeholders only).
6. Any migration needed (only if needed): next number, expand-only, identity objects under SET LOCAL ROLE spps_identity_owner ... RESET ROLE; generate serial file with tools/v2_serial_sql.py, never rewrite existing serial files; update MIGRATIONS.md, v2_verify.sql, server/v2/preflight.py, DEPLOYMENT.md.
7. After web/ changes: node scripts/stage-web.mjs and node scripts/sync-android-web.mjs.

Tests (required)
- State machine: pending -> approved/declined/proposed -> accepted/declined; no transition from declined/past; only the owning practitioner can decide; another practitioner gets 404.
- Video ticket refused before the window, issued inside it, refused after grace, refused for in-person or unconfirmed bookings, refused for a third account.
- Browser tests: button appears/disappears with a frozen clock; portal shows the proposed time and Accept works.
- Run tests tests_v1 tests_v2 (report the new baseline) and the PostgreSQL tier through the sanctioned ephemeral DB (tools.ephemeral_db / Docker). Test any new serial SQL as a NON-superuser.

Guardrails
- Do NOT apply SQL to Supabase, change Render env vars, or trigger a deploy. Do NOT merge to main. Commit to the phase branch, push, open a PR only after I approve.
- Never commit .env, secrets, SPPS_FIELD_KEYS; never print the owner e-mail in server/main.py.
- Stop and ask if the brief and the code disagree in a way that changes scope.

Finish with the brief's report format and "Next Steps & Improvements" (tests to run, gaps, ranked enhancements, security/compliance), plus the exact Supabase run order (if any SQL) and expected v2_verify.sql values.
```

---

## Phase 4: Notifications for every role (Web Push + Android FCM)

```text
You are working in C:\My Computer\Codex\spps-v1.0-free (SPPS v1.0 Free; FastAPI server/, vanilla JS web/, Capacitor Android; Supabase spps-ready-1; Render deploys Karansportpsy/spps-karan main).

Read fully first: CLAUDE.md, docs/BUILD_BRIEF_booking_only.md (Ground rules, Findings, Phase 4, Branch/commit workflow, Report format), DECISIONS.md, MIGRATIONS.md, and the Phase 2-3 reports.

TASK: Phase 4 only (notifications for owner, admin, practitioner, athlete, guardian). Do not start Phase 5.

Pre-flight (stop and tell me if any fails)
1. git status clean; origin correct; Phase 3 merged into origin/main.
2. Branch phase4-notifications from origin/main.
3. Inspect and report in 6 lines before coding: notifications.py outbox and worker; how each role signs in (practitioner/owner session vs 13A portal vs v2 loop accounts) and therefore which user id a device row should reference; current Capacitor version and the APK build script constraints; existing service worker (if any) and CSP headers; which events already enqueue e-mail; where Settings lives for each role.

Build
1. Migration (next number, expand-only): identity-schema tables, FORCE RLS, created under SET LOCAL ROLE spps_identity_owner ... RESET ROLE:
   - notification_devices: user reference + role, platform (web/android), subscription or token (store encrypted or at least never returned to clients after registration), created_at, last_seen_at, revoked_at. Unique per (user, platform, token).
   - notification_preferences: user + category (appointments, programme_tasks, account_security). account_security cannot be fully disabled (enforce in SQL and API).
   Policies: a user sees and changes only their own rows; the worker role can read active devices for delivery; nobody else.
   Serial file via tools/v2_serial_sql.py; update MIGRATIONS.md, v2_verify.sql, server/v2/preflight.py, DEPLOYMENT.md.
2. Web Push: service worker in web/ (scope correct for Capacitor and browser), VAPID from env SPPS_VAPID_PUBLIC_KEY, SPPS_VAPID_PRIVATE_KEY, SPPS_VAPID_SUBJECT (generic contact address I will supply). Subscribe/unsubscribe endpoints with CSRF, rate limit, allowlist entries. Use a maintained library (pywebpush); pin the version.
3. Android: add @capacitor/push-notifications matching the CURRENT Capacitor major/minor (do not bump Capacitor); FCM server credentials only via env (e.g. SPPS_FCM_SERVICE_ACCOUNT_JSON). Do NOT create or commit google-services.json; document that I add it. Confirm the APK build still works.
4. Delivery: extend the outbox worker with push next to e-mail. Payloads are CONTENT-FREE only ("You have an appointment update. Open the app."): no names, times, services or clinical words. Respect preferences per category. Delete/revoke tokens the provider reports as invalid (404/410, UNREGISTERED). Revoke a device's token on sign-out.
5. Triggers: new booking request, approval or decline, proposed new time, reminder before a session (e.g. 1 hour; idempotent, no duplicates on worker restart), new assignment, released summary.
6. UI: "Turn on notifications" pop-up on first sign-in for every role, with Turn on / Not now. Not now -> reminder in Settings and ask again after 7 days. If the browser permission is "denied", never prompt; show how to enable it. Settings page: per-category toggles (security locked on) and registered devices with Remove.
7. Feature flag for push, off by default; preflight checks the VAPID/FCM env only when the flag is on.
8. After web/ changes: node scripts/stage-web.mjs and node scripts/sync-android-web.mjs.

Tests (required)
- Preference rules (security cannot be disabled; per-category opt-out respected).
- Payload test: no personal or clinical data in any push body (scan every trigger).
- Device RLS: user A cannot read/revoke B's devices; tokens never returned in API responses.
- Invalid-token cleanup; sign-out revokes; reminder sent once only.
- Pop-up logic with mocked Notification.permission (default/granted/denied) in a browser test.
- Run the unit tiers and the PostgreSQL tier via the ephemeral DB; test new serial SQL as a NON-superuser.

Guardrails
- Do NOT apply SQL to Supabase, set Render env vars (VAPID/FCM), or deploy; give me the exact env var list and how to generate VAPID keys instead. Do NOT merge to main; push the branch, PR only after I approve.
- Never commit .env, keys, service-account JSON, google-services.json, SPPS_FIELD_KEYS; never print the owner e-mail in server/main.py.
- Stop and ask if scope changes.

Finish with the brief's report format and "Next Steps & Improvements", the Supabase run order, expected v2_verify.sql values, and the Render env vars I must set before turning the flag on.
```

---

## Phase 5: Owner and admin model, MFA, audit log, photo upload

```text
You are working in C:\My Computer\Codex\spps-v1.0-free (SPPS v1.0 Free; FastAPI server/, vanilla JS web/, Capacitor Android; Supabase spps-ready-1; Render deploys Karansportpsy/spps-karan main).

Read fully first: CLAUDE.md, docs/BUILD_BRIEF_booking_only.md (Ground rules, Findings, Phase 5, Branch/commit workflow, Report format), DECISIONS.md, MIGRATIONS.md, and the Phase 2-4 reports.

TASK: Phase 5 only.

Pre-flight (stop and tell me if any fails)
1. git status clean; origin correct; Phase 4 merged into origin/main.
2. Branch phase5-owner-admin from origin/main.
3. Inspect and report in 6 lines before coding: how the owner is identified today (OWNER_EMAIL constant, the literal e-mail inside the Supabase-exchange route in server/main.py, founder card/photo and pinned-first logic); how practitioner approval works; Supabase Auth MFA availability for this project vs. an in-app TOTP option; existing audit/event tables; current photo handling (v2_set_application_photo, public_practitioner_photo) and storage limits.

Build
1. Owner account = admin workspace only by default: no practitioner profile, no founder card in the directory. Remove the hard-coded founder photo and the pinned-first ordering. Replace the owner e-mail literal in server/main.py with the OWNER_EMAIL constant/env (never print the address).
2. Separate admin sign-in page (/admin) reached by a small footer link on the landing page; admin sessions separate from practitioner sessions, shorter lifetime, CSRF, rate-limited, allowlisted.
3. Admins: owner-only invitations (single-use, expiring token by e-mail), several admins allowed, owner can revoke; owner cannot be removed or demoted by an admin.
4. MFA required for owner and every admin before any admin action: prefer Supabase Auth MFA (TOTP) if available; otherwise in-app TOTP with encrypted secrets and one-time recovery codes. Enforcement on the SERVER for every admin route, not only in the UI.
5. Audit log (append-only, FORCE RLS; no UPDATE/DELETE grants): actor, role, action, target type/id, timestamp, IP hash. Record: admin invite/accept/revoke, MFA enrol/reset, practitioner approve/reject/more-info, directory listing changes, account closures. Owner and admins can read it; nobody can edit it.
6. Practitioner approval open to admins (not just the owner), audited.
7. In-app photo upload for every practitioner: JPEG/PNG/WebP only, size cap (e.g. 2 MB), re-encode server-side and strip EXIF/GPS, square crop, moderation by approval (new photo goes live only after admin approval if that matches the current approval rule). No external hosting.
8. Migration(s): next number, expand-only; identity objects under SET LOCAL ROLE spps_identity_owner ... RESET ROLE; serial files via tools/v2_serial_sql.py; update MIGRATIONS.md, v2_verify.sql, server/v2/preflight.py, DEPLOYMENT.md. A data step that removes the owner's practitioner profile/founder card must be guarded (refuse if that profile has athletes, bookings or links) and explained in DEPLOYMENT.md.
9. Feature flag(s) where the brief requires; after web/ changes run node scripts/stage-web.mjs and node scripts/sync-android-web.mjs.

Tests (required)
- Admin routes refuse without MFA, refuse practitioner sessions, refuse revoked admins; owner-only invite enforced.
- Audit rows written for each listed action; audit table rejects UPDATE/DELETE at the database level.
- Directory has no founder card or pinned ordering; owner e-mail literal absent from server source (scan).
- Photo upload: wrong type/oversize refused, EXIF stripped, other practitioners cannot replace someone's photo.
- Unit tiers + PostgreSQL tier via the ephemeral DB; new serial SQL tested as a NON-superuser.

Guardrails
- Do NOT apply SQL to Supabase, change Render env, change Supabase Auth settings, or deploy; list exactly what I must switch on (e.g. Supabase MFA) instead. Do NOT merge to main; push the branch, PR only after I approve.
- Do not lock me out: keep a documented owner recovery path and test it before asking me to enable MFA enforcement in production.
- Never commit .env, secrets, SPPS_FIELD_KEYS; never print the owner e-mail.
- Stop and ask if scope changes.

Finish with the brief's report format and "Next Steps & Improvements", the Supabase run order, expected v2_verify.sql values, and a production enablement checklist (MFA, admin invite of yourself, recovery codes stored offline).
```
