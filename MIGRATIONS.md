# Database migrations — run order

A brand-new empty Supabase project is built by running every file in `supabase/sql/` in this order, in
the SQL editor, as the default `postgres` role. Each file is one transaction. `supabase/sql/NN_XXXX_*.sql`
is generated from `migrations/XXXX_*.sql` (`python tools/v2_serial_sql.py`) and records itself in
`spps_ops.migration_history`. Never edit a file that may have been run: every change is a new numbered file.

| # | File | What it does |
|---|---|---|
| 1 | `00_bootstrap_roles_schemas.sql` | Extensions, the four restricted login roles (no passwords yet), schemas and the migration ledger. |
| 2 | `01_baseline_tables.sql` | Baseline clinical (spps_dev) and identity (spps_identity_dev) tables as of v1.0. |
| 3 | `02_0002_isolated_development_rls.sql` | FORCE row-level security on every clinical table; owner-scoped practitioner policies. |
| 4 | `03_0003_local_clinical_parity.sql` | Clinical write policies for notes, assignments, reports and injury records. |
| 5 | `04_0010_clinical_slot_management.sql` | Practitioners manage only their own appointment slots. |
| 6 | `05_0014_clinical_admin_review.sql` | Administrator credential/publication review under FORCE RLS (current_is_admin). |
| 7 | `06_identity_schema_ownership.sql` | Identity schema ownership moved to the NOLOGIN spps_identity_owner role. |
| 8 | `07_0004_identity_review_roles_rls.sql` | Identity roles and RLS for athlete/guardian accounts. |
| 9 | `08_0005_identity_auth_functions.sql` | Athlete/guardian authentication functions (identity schema). |
| 10 | `09_0006_guardian_authority_consent.sql` | Guardian authority, reviewer verification and the consent ledger. |
| 11 | `10_0007_practitioner_links.sql` | Practitioner-athlete links (identity schema). |
| 12 | `11_0008_link_invitation_preview.sql` | Signed-in athlete/guardian can preview which practitioner issued an invitation code. |
| 13 | `12_0009_approval_first_booking.sql` | Approval-first booking, payment ledger and notification outbox. |
| 14 | `13_0011_clinical_participation.sql` | Athlete participation projections (identity schema). |
| 15 | `14_0012_platform_video.sql` | Video consultation for confirmed platform bookings. |
| 16 | `15_0013_notification_delivery.sql` | Durable notification delivery (outbox worker). |
| 17 | `16_0015_institutional_licences.sql` | Organisation seat licences assigned by the tenant administrator. |
| 18 | `17_0016_video_room_closure.sql` | Ending a video consultation is final. |
| 19 | `18_0017_free_clinical_slots.sql` | Free appointments without rewriting historical payments. |
| 20 | `19_0018_free_booking_lifecycle.sql` | Zero-charge booking lifecycle. |
| 21 | `20_0019_practitioner_approval.sql` | Practitioner approval gate in the clinical schema. |
| 22 | `21_0020_owner_approval_rls_grants.sql` | Narrow grants for owner approval actions. |
| 23 | `22_0021_pending_practitioner_registration.sql` | Verified-email, pending-only registration function. |
| 24 | `23_0022_verified_owner_claim.sql` | One-time verified owner claim. |
| 25 | `24_0023_restricted_self_profile_edit.sql` | Practitioner profile edits without writable review flags. |
| 26 | `25_0024_owner_dual_role_featured_profile.sql` | Owner can be administrator and practitioner; featured profile. |
| 27 | `26_0025_public_tenant_read_policy.sql` | Restricted app may read only the public tenant. |
| 28 | `27_0026_pending_approval_free_launch.sql` | Fail-closed pending_approval default for every non-owner sign-up. |
| 29 | `28_0027_email_password_owner_approval.sql` | Email/password owner bootstrap; only the owner address may be admin. |
| 30 | `29_0028_production_public_tenant.sql` | Production public tenant winmindperform. |
| 31 | `30_0029_public_directory_enquiry_owner_profile.sql` | Public directory, public enquiry, resubmission and owner profile functions. |
| 32 | `31_0030_clinical_foreign_key_indexes.sql` | Covering indexes for clinical foreign keys. |
| 33 | `32_0031_identity_foreign_key_indexes.sql` | Covering indexes for identity foreign keys. |
| 34 | `33_0032_account_lifecycle.sql` | Practitioner self-service listing deactivation (account lifecycle phase A). |
| 35 | `34_0033_v2_registration_state_machine.sql` | Server-side pending practitioner application, registration state machine and the review e-mail delivery log. |
| 36 | `35_0034_v2_trust_foundation.sql` | Record-access audit log, data-export ledger, practitioner settings (AI opt-in), orderly closure and the owner archive. |
| 37 | `36_0035_v2_practitioner_record.sql` | Athlete profile, note templates, signatures, amendments, server drafts, 5Ps formulation and spreadsheet imports. |
| 38 | `37_0036_v2_assessments.sql` | Instruments as data, a custom instrument builder, score entry and sparse-data trends. |
| 39 | `38_0037_v2_reports.sql` | Report templates with audience-tagged fields, signed reports, per-audience sharing consent and expiring, revocable report links. |
| 40 | `39_0038_v2_athlete_loop.sql` | The practitioner-invited athlete loop in the identity schema (accounts, invitations, consent links, assignments, check-ins, alerts, released summaries, crisis contacts). |
| 41 | `40_0039_v2_directory.sql` | Opt-in public directory profiles, the public read model (directory_snapshot) and directory enquiries. |
| 42 | `41_0040_v2_self_serve.sql` | Self-serve athlete and guardian accounts — account-first enquiries, accepting an enquiry into a record and an invitation, switching practitioner, account deletion, athlete-approved report links. |
| 43 | `42_0041_v2_release_hardening.sql` | A practitioner's report links stop opening as soon as that practitioner is deactivated, closes their account, or is rejected or sent back for review by the platform owner. |
| 44 | `43_0042_v2_appointment_only_directory.sql` | v2 migration. |
| 45 | `44_0043_v2_practitioner_services.sql` | Practitioner service catalogue and appointment requests from the directory. |
| 46 | `v2_verify.sql` | Read-only check of the whole v1 + v2 database (run after step 41; expected values at the top). |
| 47 | `98_verify.sql` | Read-only check of the v1 objects (run after the serial files; v1 expected values). |
| 48 | `99_set_role_passwords.sql` | Template: set the four login-role passwords (edit a copy, never save it). |

## Source migrations (`migrations/`)

`0001`–`0032` are the v1 sources (applied to the v1 hosted database through the serial set above).
`0033` onwards are the v2 sources; each has exactly one generated serial file.

- `0001_REVIEW_ONLY_postgresql_schema.sql`
- `0002_isolated_development_rls.sql`
- `0003_local_clinical_parity.sql`
- `0004_identity_review_roles_rls.sql`
- `0005_identity_auth_functions.sql`
- `0006_guardian_authority_consent.sql`
- `0007_practitioner_links.sql`
- `0008_link_invitation_preview.sql`
- `0009_approval_first_booking.sql`
- `0010_clinical_slot_management.sql`
- `0011_clinical_participation.sql`
- `0012_platform_video.sql`
- `0013_notification_delivery.sql`
- `0014_clinical_admin_review.sql`
- `0015_institutional_licences.sql`
- `0016_video_room_closure.sql`
- `0017_free_clinical_slots.sql`
- `0018_free_booking_lifecycle.sql`
- `0019_practitioner_approval.sql`
- `0020_owner_approval_rls_grants.sql`
- `0021_pending_practitioner_registration.sql`
- `0022_verified_owner_claim.sql`
- `0023_restricted_self_profile_edit.sql`
- `0024_owner_dual_role_featured_profile.sql`
- `0025_public_tenant_read_policy.sql`
- `0026_pending_approval_free_launch.sql`
- `0027_email_password_owner_approval.sql`
- `0028_production_public_tenant.sql`
- `0029_public_directory_enquiry_owner_profile.sql`
- `0030_clinical_foreign_key_indexes.sql`
- `0031_identity_foreign_key_indexes.sql`
- `0032_account_lifecycle.sql`
- `0033_v2_registration_state_machine.sql` → `supabase/sql/34_0033_v2_registration_state_machine.sql` — Server-side pending practitioner application, registration state machine and the review e-mail delivery log.
- `0034_v2_trust_foundation.sql` → `supabase/sql/35_0034_v2_trust_foundation.sql` — Record-access audit log, data-export ledger, practitioner settings (AI opt-in), orderly closure and the owner archive.
- `0035_v2_practitioner_record.sql` → `supabase/sql/36_0035_v2_practitioner_record.sql` — Athlete profile, note templates, signatures, amendments, server drafts, 5Ps formulation and spreadsheet imports.
- `0036_v2_assessments.sql` → `supabase/sql/37_0036_v2_assessments.sql` — Instruments as data, a custom instrument builder, score entry and sparse-data trends.
- `0037_v2_reports.sql` → `supabase/sql/38_0037_v2_reports.sql` — Report templates with audience-tagged fields, signed reports, per-audience sharing consent and expiring, revocable report links.
- `0038_v2_athlete_loop.sql` → `supabase/sql/39_0038_v2_athlete_loop.sql` — The practitioner-invited athlete loop in the identity schema (accounts, invitations, consent links, assignments, check-ins, alerts, released summaries, crisis contacts).
- `0039_v2_directory.sql` → `supabase/sql/40_0039_v2_directory.sql` — Opt-in public directory profiles, the public read model (directory_snapshot) and directory enquiries.
- `0040_v2_self_serve.sql` → `supabase/sql/41_0040_v2_self_serve.sql` — Self-serve athlete and guardian accounts — account-first enquiries, accepting an enquiry into a record and an invitation, switching practitioner, account deletion, athlete-approved report links.
- `0041_v2_release_hardening.sql` → `supabase/sql/42_0041_v2_release_hardening.sql` — A practitioner's report links stop opening as soon as that practitioner is deactivated, closes their account, or is rejected or sent back for review by the platform owner.
- `0042_v2_appointment_only_directory.sql` → `supabase/sql/43_0042_v2_appointment_only_directory.sql` — v2 migration.
- `0043_v2_practitioner_services.sql` → `supabase/sql/44_0043_v2_practitioner_services.sql` — Practitioner service catalogue and appointment requests from the directory.

After the serial files, run `v2_verify.sql` (whole database; expected values at the top), optionally
`98_verify.sql` (v1 objects only; its counts describe a v1-only database), then
`99_set_role_passwords.sql` from an edited copy.
