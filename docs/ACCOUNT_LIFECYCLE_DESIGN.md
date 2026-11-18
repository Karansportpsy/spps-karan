# Account lifecycle, data rights and orderly closure — design record

Status: **agreed in principle 2026-11-16, not yet implemented.** No application code or SQL has been changed.
Legal wording is not final: every policy text and retention value below is for counsel (see
`docs/legal/COUNSEL_BRIEF_AND_DRAFT_CLAUSES.md`). This record is not legal advice.

## 1. Decisions (all nine accepted as proposed)

| # | Topic | Decision |
|---|---|---|
| 1 | Practitioner closure | Orderly closure with a notice window and transition options. **No** requirement that every client signs a clearance. |
| 2 | Owner archive | Practitioner's own professional data and a minimal closure record only. **No** archive of clients' clinical content. |
| 3 | Client records at closure | Practitioner chooses per client: export, transfer (with the client's consent), give to the client, or sealed custody for a fixed period. |
| 4 | Retention periods | Configurable per tenant; values set by counsel / accountant. No hard-coded legal periods. |
| 5 | Client deletion | 14-day grace, then portal data is purged. Practitioner-held clinical notes follow the practitioner's retention duty. |
| 6 | Minors | The guardian acts; control passes to the athlete at 18. |
| 7 | Switching practitioner | Client-initiated, immediate, no approval from the old practitioner; optional item-by-item handover. |
| 8 | Build order | Phase A practitioner side now (portal is off); Phase B client side with the portal. |
| 9 | Policy text | Drafted by us, reviewed and approved by counsel before it reaches `web/privacy.html` / `web/terms.html`. |

## 2. Principles

1. The person always controls their own account and can always export their own data.
2. Closing a relationship or account never needs another person's permission. Moving records to a third party needs
   the data subject's consent (the guardian's for a minor).
3. Notice, not veto: affected people are told in time to export, transfer or object; silence never blocks.
4. Continuity of care is a duty of the practitioner. The platform enforces only the hard blockers in section 5.
5. "Purge" removes active data and leaves a minimal record. It never promises that backups, provider logs or
   audit entries vanish.
6. Roles: a practitioner decides why and how a client's clinical record is used (likely the data fiduciary for it);
   the platform acts for the practitioner there, and is itself the fiduciary for accounts, directory, bookings and
   audit. Contracts allocate cost between them; they do not move statutory duties.

## 3. Data classes

| Class | Tables (clinical schema `spps_dev` unless noted) | Deactivate | Close / purge | Notes |
|---|---|---|---|---|
| Account and sessions | `practitioners`, `login_sessions` | `active=false`, sessions revoked | Row anonymised (email replaced, name "Closed account", password unusable); sessions deleted | `email` is unique, so the address is freed by anonymising |
| Public profile | `practitioner_profiles` | `public_approved=false` | Deleted after the owner snapshot is taken | Photo bytes deleted |
| Clinical workspace | `athletes`, `cases`, `notes`, `factors`, `observations`, `interventions`, `assignments`, `reports`, `assessment_*`, `injury_*`, `retention_holds` | Kept | Exported to the practitioner first, then erased; blocked by an active retention hold | Practitioner is responsible for retention after export |
| Enquiries, slots, bookings | `inquiries`, `appointment_slots`, `bookings`, `consultation_rooms`, `room_access`, `session_reviews`, `payment_allocations` | No new slots | Open items cancelled and counterparties notified; financial rows kept under statutory retention | Accountant to confirm retention |
| Programmes | `practitioner_programmes`, `programme_enquiries`, `organisation_quote_requests` | Unpublished | Deleted; open enquiries closed | |
| Commercial | `practitioner_subscriptions`, `*_charges`, `*_registration_intents` | Kept | Kept as financial records | Payments are off in phase 1 |
| Terms evidence | `terms_acceptances` | Kept | Copied into the owner snapshot | |
| Audit | `audit_events` (clinical), `platform_audit_events` (identity) | Kept | Kept | Clinical: application roles hold INSERT and column-limited SELECT only; no UPDATE or DELETE grant was found in migrations 0002-0026 and 0030-0031 (0027-0029 not re-checked). Identity: triggers also block UPDATE, DELETE and TRUNCATE (0006). No foreign key to practitioners, so rows survive closure |
| Identity schema `spps_identity_dev` (portal, off in phase 1) | `practitioner_athlete_links`, invitation codes, bookings, programmes, released summaries, video, notifications | Links stay | Links closed, clients notified, released summaries left with the client | `practitioner_id` is a plain integer there (no foreign key by design), so closure must call identity functions explicitly |
| Supabase Auth user | `auth.users` (outside both schemas) | Untouched | Deleted by a separate owner step | Needs the admin key, which is **not** stored in Render. Decision open, see section 9 |
| Owner snapshot (new) | `owner_practitioner_archive` | n/a | Written once at closure | Profile, credentials, review history, terms acceptances, closure record. Owner-only |

## 4. Practitioner lifecycle

```
active ──deactivate──► deactivated ──reactivate──► active
active/deactivated ──start closure──► closing ──cancel──► previous state
closing ──notice window ends, no blocker──► closed (minimal record) ──retention ends──► purged
```

- **Deactivate** (reversible): hidden from the directory, no new bookings or slots, sessions revoked, existing data kept.
  The owner can also deactivate (existing "Suspend" behaviour is retained).
- **Export**: always available, also while deactivated. See section 7.
- **Start closure**: re-authenticate with the password, then a pre-check lists clients/records, open cases, upcoming
  bookings, active retention holds and safety flags.
- **Notice window**: default 30 days (setting). During it the practitioner records a disposition per client and may cancel.
- **Hard blockers** (practitioner side only): a client with an unresolved safety flag and no recorded referral or
  handover; an active retention hold; an unsettled payment. Nothing a client does or fails to do is a blocker.
- **Close**: single transaction, in this order: write owner snapshot → close identity links (Phase B) → cancel open
  bookings → erase workspace data per disposition → delete profile and programmes → anonymise account → revoke
  sessions → write audit event `account.closed`. Any failure rolls the whole transaction back.
- **Cooling-off**: after closing, the account stays recoverable by the owner for 14 days before the anonymisation of
  workspace data is final (setting).

Phase A scope note: with the portal off there are no client accounts, so "clients" are the practitioner's own athlete
records. Dispositions available in Phase A are export-and-erase or keep-by-deactivating. Transfer and sealed custody
need the portal and the updated intake terms (Phase B / counsel).

## 5. Client lifecycle (Phase B, with the portal)

- **Deactivate / delete / export**: any adult client, no consent from anyone, re-authentication required. Delete has a
  14-day grace; the client can cancel and can export first.
- **Minors**: all of the above are done by the guardian. At 18 the athlete takes over; the guardian loses control.
- **What delete covers**: the portal account and what the client contributed or was shown. The practitioner's own
  clinical notes stay under the practitioner's retention duty; the client keeps the right to access them. The privacy
  notice says so in plain words.
- **Switch practitioner**: one guided action. The client closes the old link immediately; the old practitioner is
  notified and cannot delay or refuse it; the client may share a handover summary item by item; the new link starts.
  No closure reason is required. Closing never notifies a coach, team or organisation.
- **Safety**: closing or deleting shows crisis-support information and does not alert anyone else.

## 6. Notifications

Closure notice, reminder at half time, final notice, closure complete; client-side: closure notice, handover offer,
deletion grace and completion. Email through the existing SMTP; an in-app inbox arrives with the portal. Delivery
failures are logged (this also fixes the current silent failure of approval emails).

## 7. Export

- Content: profile, own records (athletes, cases, notes, observations, interventions, reports, assessments, injury
  data), bookings, programmes, consent/terms history. Format: machine-readable JSON plus a readable CSV/PDF set in one ZIP.
- Built asynchronously; delivered only as a time-limited signed link shown in the app (never attached to email);
  files deleted after 7 days; one export per hour per account; password re-authentication before generation.
- Every export and download writes an audit event without content.

## 8. Phases

**Phase A — practitioner side (portal off)**
- A1 deactivate / reactivate, owner and self.
- A2 export (also the prerequisite for A3).
- A3 orderly closure and purge, owner snapshot, closure record.
- A4 owner tools: closure queue, archive view, restore within the cooling-off period.

**Phase B — client side (needs `SPPS_PORTAL_ENABLED=true`, TURN, counsel's children's-data review)**
- B1 client deactivate / delete / export, guardian handling, 14-day grace.
- B2 switch practitioner and optional handover.
- B3 closure notices to linked clients and the disposition options transfer / sealed custody.

**Phase C — text and values (counsel, accountant)**
- C1 privacy notice, terms, athlete/guardian terms. C2 retention values. C3 sealed-custody consent wording at intake.

## 9. Data-model changes (proposal)

New numbered files `migrations/0032_account_lifecycle.sql` and `supabase/sql/33_0032_account_lifecycle.sql`
(never edit a file that has been run; the owner runs hosted SQL, see `DEPLOYMENT.md`):

- `practitioner_closures` (id, tenant, practitioner, state, requested_at, notice_ends_at, completed_at, cooling_off_ends_at).
- `closure_dispositions` (closure, athlete record, disposition, recorded_at).
- `owner_practitioner_archive` (tenant, practitioner, snapshot jsonb, created_at) readable only by the owner role.
- `tenant_retention_settings` (tenant, clinical_record_years, financial_years, notice_days, cooling_off_days) with no
  default legal values; unset means closure refuses to purge.
- SECURITY DEFINER functions for the ordered closure steps, owned by a role that application roles can only EXECUTE;
  RLS on all new tables; GRANTs minimal. Identity-schema functions for closing links are added in Phase B.

**Open decision — Supabase Auth user deletion:** options are (a) a manual owner step in the Supabase dashboard
(recommended for now), (b) a narrowly scoped server function with the service-role key stored in Render (increases the
blast radius of a Render compromise). Decide before Phase A3 ships.

## 10. Acceptance and negative tests (written before the code)

API:
- practitioner can deactivate, reactivate, export; closure requires password; closure with an active retention hold,
  unresolved safety flag or unsettled payment is refused with 409; unresponsive clients never block.
- closure is atomic (inject a failure mid-way; nothing changes); closed account cannot sign in; address can re-register.
- export contains only the caller's data; a second request within the hour is refused; signed link expires.
- owner snapshot exists after closure and contains no client content.

PostgreSQL RLS (ephemeral database only):
- a practitioner cannot read or write another practitioner's closure, dispositions, snapshot or export;
- application roles cannot read the owner archive, and cannot update or delete `audit_events`;
- closure functions are not executable by the anonymous or portal roles;
- erasing one practitioner leaves every other practitioner's rows unchanged (row counts compared before/after).

Never weaken or skip these to get green.

## 11. Open items for counsel / accountant

Role characterisation; Indian retention periods for sport-psychology records and whether practitioners fall under the
Rehabilitation Council regime; children's-data position for load/recovery monitoring; enforceability of the
indemnity and liability wording; sealed-custody consent at intake; financial-record retention once payments start;
effective date and grievance contact for the privacy notice.

## 12. Implementation status

**Phase A, step 1 (this release, behind `SPPS_ACCOUNT_LIFECYCLE_ENABLED`, default off):**

| Item | Where | Status |
|---|---|---|
| Deactivate / reactivate own listing | `server/account_lifecycle.py`, migration `0032` (`set_own_account_deactivation`) | built, tested on SQLite and on real PostgreSQL roles |
| Export own data (JSON, password re-confirmation, audited, 5-failure lockout) | `server/account_lifecycle.py` | built, tested; `warnings` is empty under the real application role |
| Public directory, photo and enquiry skip deactivated practitioners | migration `0032` (redefined `public_practitioner_directory`, `public_practitioner_photo`) | built, tested |
| Startup refuses the flag until `0032` is in the database | `server/release_preflight.py` | built, tested (column rename and revoked grant both refuse) |
| UI: "Your account" card, deactivate dialog, export dialog | `web/app.js` (staged to `www/`) | built; shown only when the server answers `/api/account/lifecycle` |

Choices made while building, and why:

- The ORM does **not** map `deactivated_at`. Mapping it would make every profile query fail on a database that has not
  yet had `0032` run, which is exactly the state at deploy time. The column is read and written with raw statements
  in `account_lifecycle.py` and through the SECURITY DEFINER function on PostgreSQL.
- `listing_eligible()` takes an optional `lifecycle_db` because the admin review list calls it with the restricted
  auth-role session, which may not read `practitioner_profiles`. The end-to-end run on PostgreSQL found this.
- Export leaves out the platform's own terms-acceptance log and audit trail (the application role cannot read them,
  by design) and says so in the file under `excluded`.
- Re-confirmation on the hosted service is a fresh Supabase password sign-in whose `amr` password timestamp is under
  five minutes old and whose user id matches the profile's `supabase_auth_uid`. A refreshed access token cannot pass
  as a fresh sign-in. On local installs the stored password hash is checked.

**Deliberately not built yet:** closure notice and per-client disposition (A3), owner archive, closure/purge function
(`SECURITY DEFINER`, to be proved against PostgreSQL before anything destructive ships), retention settings table,
all client-side work (Phase B), and the counsel-reviewed clauses (Phase C).
