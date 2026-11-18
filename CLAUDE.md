# SPPS — Claude Code operating guide

Sport Psychology Practitioner Suite, production stack (exported 2026-11-15 from `release/v1.0-live`).
This folder is the source of truth. Deployment steps: `DEPLOYMENT.md`. Delivery rules: `AGENTS.md`.

## Architecture
- FastAPI app factory `server/main.py:create_app()`; routes also in `beta_routes.py`, `subscription_routes.py`,
  `marketplace_routes.py`, `practitioner_review.py`; practitioner auth = Supabase verified email + password exchanged
  for the `modelc_session` cookie + `X-CSRF-Token`.
- **Clinical ORM** `server/models.py`: exactly 33 tables (schema `spps_dev`), FORCE RLS via transaction-local
  `spps.practitioner_id`/`spps.tenant_id` (`server/identity.py`). Roles `spps_dev_app`, `spps_dev_auth`.
- **Platform identity ORM** `server/platform_identity_models.py` (schema `spps_identity_dev`, 22 tables): athlete/guardian
  accounts, sessions, consent, links, bookings, programmes, video. Roles `spps_identity_owner` (NOLOGIN),
  `spps_identity_auth`, `spps_identity_portal`. Off in phase 1 (`SPPS_PORTAL_ENABLED=false`).
  Never add identity tables to `server/models.py`; never let portal code touch clinical base tables directly.
- Hosted production uses ONE Supabase database with both schemas (`SPPS_SINGLE_DATABASE_SCHEMAS=true`); startup
  validation is in `server/hosted_profile.py` and `server/release_preflight.py`.
- Database: `supabase/sql/` is the serial set for a new project (generated from `migrations/`; verified to reproduce
  the previous hosted schema exactly). Schema names `spps_dev` / `spps_identity_dev` are identifiers, not environments.
- `web/` is canonical UI. `www/` is GENERATED (`npm run web:stage` / `android:sync`) — never hand-edit; keep byte-identical.
- Android: Capacitor 8; release id `com.winmindperform.spps.beta15`. Never change ids.

## Local-only environments
Never read/print `.env`, `.env.integration`, `.env.identity-review*` (hook-enforced). Keep only `*.example` env files in the folder.
The only database automation may create/destroy: Compose project `spps-test-ephemeral`, `127.0.0.1:55436`,
tmpfs storage (`compose.test-ephemeral.yml`, managed by `tools/ephemeral_db.py`, verified by `tools/ephemeral_guard.py`).

## Absolute prohibitions (enforced by `.claude/hooks/guard_commands.py`)
Docker volume rm/prune, system prune, `compose down -v`, actions on protected containers/projects; `gh pr merge`;
push to `main`; force push; deploys (Render/Vercel/Supabase/Play); live payment keys; real personal or clinical data;
disabling/skipping/xfail-ing security or RLS tests or weakening assertions to get green.
The owner runs hosted Supabase SQL and Render changes personally, following `DEPLOYMENT.md`. Changing this section or
the guard hook is the owner's decision, made by the owner.

## Testing
```
python -m pytest tests tests_v1 -q          # unit + contract (SQLite tmp)
.\scripts\spps-check.ps1 -Tier db            # fresh ephemeral PG: identity RLS, integration, browser, hosted
.\scripts\spps-check.ps1 -Tier android       # debug APK build + package id + permission allowlist
```
New behaviour needs acceptance and negative tests (API and PostgreSQL RLS). A new SQL change needs a new numbered file in
`migrations/` and `supabase/sql/`; never edit one that has been run.
