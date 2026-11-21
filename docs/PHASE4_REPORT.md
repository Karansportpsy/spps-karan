# Booking-only Phase 4: Notifications for every role

Date: 2026-11-21. Local branch: `phase4-notifications`, based on `main` at `21aa02f`.
Scope: Phase 4 only, built in the local folder. No push, PR, merge, hosted SQL, Render change or deployment was made.

## What you get

- A pop-up the first time anyone signs in (athlete, guardian, practitioner, admin or owner): "Turn on notifications?"
  with Turn on / Not now. "Not now" asks again after seven days; someone with a working device is never asked again.
- A Notifications card in Settings (workspace) and Account (portal): turn this device on or off, choose what to be told
  about (appointments and reminders; programme and task updates; account and security, which is always on), and remove
  devices.
- Web Push for browsers (Chrome, Edge, Firefox, Safari 16.4+ when added to the Home Screen) and an FCM sender for the
  Android app. Alerts are short and carry no names, times, services or health details.
- An appointment reminder, once per confirmed appointment to each side, `SPPS_REMINDER_MINUTES` (default 60) before it.
- A delivery loop inside the web service. Today `tools/notify_worker.py` is not running on Render, so queued mail
  (including booking notices) never sent; switching `SPPS_NOTIFY_WORKER_ENABLED` on fixes that too.
- `SPPS_SMTP_FROM` may be `Name <address>`; `SPPS_SMTP_SENDER` is optional (this fixes the deploy error you hit).

## Files

- SQL: `migrations/0046_v2_notifications_push.sql`, generated `supabase/sql/47_0046_v2_notifications_push.sql`,
  `MIGRATIONS.md`, `supabase/sql/v2_verify.sql` (expect identity tables 40, identity policies 112, v2 migrations 14).
- Server: `server/push_delivery.py`, `server/notify_runner.py`, `server/v2/push.py`, `server/notifications.py`,
  `server/hosted_profile.py`, `server/v2/{flags,preflight,allowlist,__init__}.py`.
- Web: `web/push.js`, `web/sw.js`, `web/app.js`, `web/portal.js`, `web/index.html`, `web/portal.html`; generated copies in
  `www/`; `scripts/stage-web.mjs` stages the two new files.
- Config and docs: `.env.example`, `.env.production.example`, `render.yaml`, `requirements-hosted.txt`
  (`pywebpush==2.5.0`, `google-auth==2.63.0`), `DEPLOYMENT.md`, `DECISIONS.md` (D-1101 to D-1110),
  `tools/generate_vapid_keys.py`.
- Tests: `tests_v2/test_phase10_notifications.py`, `tests_v2/test_phase10_browser.py`,
  `tests_v2_pg/test_notifications_phase4.py`; end-to-end pins updated (48 serial steps, 45 history rows, 40 identity tables).

## Security notes

- Subscriptions are never returned by any API; the device list shows only platform and dates.
- Endpoints must be https, port 443, no credentials, on a known push-service domain (checked at registration and
  again before each send), so the server cannot be steered to an arbitrary address.
- One device belongs to one person: signing in as someone else on the same browser moves it.
- The new identity tables have no application grants; a person can only touch their own rows.
- Rate limits on every push route; every route is in the default-deny allowlist and behind its flag.
- Message text is never stored or logged; the loop logs counts only.

## Not done in this phase

- Android app side: add `@capacitor/push-notifications`, a Firebase project and `google-services.json`, rebuild the
  APK, and set `SPPS_FCM_SERVICE_ACCOUNT_JSON`. The web code calls the plugin if it is present; it is untested on a device.
- The older invited-athlete page (`/athlete`) uses its own session and does not show the pop-up.
