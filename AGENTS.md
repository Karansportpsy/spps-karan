# SPPS working agreement — owner delivery policy

Applies to all work on the SPPS web + Android platform, the Supabase database and the Render service.

## Process for every change
1. Work in this repository (private GitHub repo `spps-ready`). Commit each change on a branch so every release can be identified and rolled back.
2. Run the automated tests and fix failures before release. Never print, commit or log secrets or real client information.
3. SQL changes: add a new forward-only numbered file, test it on a throwaway database, then the owner runs it in the
   Supabase SQL editor and re-runs `supabase/sql/98_verify.sql`. Do not rerun or edit an applied file. Back up first when data exists.
4. Release the tested code to Render (Git host or container image, see `DEPLOYMENT.md`) and verify build, startup,
   `/health/ready`, sign-in, role isolation and the approval workflow.
5. Report what was released, which SQL was run, what was verified and what is still open.
6. If a prerequisite fails (database credentials, backup, legal notice, security/RLS, owner sign-in), stop. Do not bypass
   guards or alter tests to make a launch appear successful.

## Limits
- `SPPS_PRODUCTION_RELEASE_APPROVED=true` is set by the owner only.
- Do not enable real client intake while `web/privacy.html` and `web/terms.html` are review drafts.

## Handing over to another coding agent (Codex)
Read `docs/CODEX_HANDOFF.md` first: current state, open items, local build loop, release flow and hard rules. The command guard in `.claude/hooks/` only protects Claude Code, so another agent must follow `CLAUDE.md` prohibitions itself: no push to `main`, no force push, no merge, no SQL on Supabase, no Render changes or deploys, no secrets in files or output.
