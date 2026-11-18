# Security

This repository holds a clinical-adjacent application. Treat every change as security-relevant.

## Reporting a problem

Report privately to the owner (the address in `server/owner_profile.py`). Do not open a public issue and do not include
real client or athlete information in a report.

## Rules for contributors

- Never commit secrets: database URLs, role passwords, Supabase keys other than the public publishable key, SMTP
  credentials, signing keystores. They live only in Supabase and the Render environment. `.gitignore` blocks `.env*`
  files except `*.example`.
- Never commit real personal or clinical data. Tests use fictional data only.
- Do not weaken, skip or `xfail` a security or row-level-security test to get a build green.
- Database changes are forward-only numbered files in `migrations/` and `supabase/sql/`. Never edit a file that has
  already been run against a database.
- If a secret is ever committed or pasted somewhere public, rotate it immediately; removing it from history is not enough.
