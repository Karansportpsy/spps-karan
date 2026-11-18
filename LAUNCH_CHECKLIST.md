# SPPS Version 1.0 - Free — launch checklist

Work through this before the first real athlete, guardian or member of the public uses SPPS. Tick each
line in your own launch log with the date and who checked it. Nothing here is done by the code for you.

## 1. Legal wording — send to counsel before any flag is switched on

- [ ] The Terms of Use, Privacy Policy and Platform Policies are written in full for India (operator and contact are
      filled in automatically from `server/owner_profile.py`). Have your lawyer review them, in particular the
      liability cap (INR 5,000), grievance timelines (7/30 days), add a postal address if advised, and confirm (data
      controller, purposes, legal basis, retention, rights, contact for requests, cross-border hosting).
- [ ] Athlete and guardian consent screens (athlete app: link consent, guardian consent, "who sees what" lists).
- [ ] Directory pages and the booking confirmation: "not a crisis or emergency service" wording; that listing is not
      an endorsement; verified-badge wording ("checks made by the platform owner on <date>").
- [ ] Coach / club report links: the notice on the report page ("shared with the athlete's approval; do not
      forward") and the e-mail sent to the coach.
- [ ] Account deletion wording: what is deleted (account, sign-in, invitations) and what each
      practitioner keeps in their own record (see `DECISIONS.md` D-805).
- [ ] Practitioner terms: retention duties for records that remain after an athlete leaves or deletes the account,
      and for practitioner closure (`SPPS_PRACTITIONER_CLOSURE_ENABLED`).
- [ ] AI features (`SPPS_AI_FEATURES_ENABLED`): wording of the opt-in and the "draft" label, if you ever switch it on.

## 2. Children's data position

- [ ] Decide and write down the position counsel approves, for example: under-18 athletes are linked only through
      a guardian who consents first (built in: guardian-first invitations, guardian consent evidence, guardian sees
      participation and released summaries only, raw answers only when both the practitioner and the athlete allow).
- [ ] Confirm the age bands in use (`under-12`, `12-14`, `15-17`) match the law where you practise and that no
      under-18 person can create a self-serve account as an athlete without a guardian link (self-serve enquiries
      for under-18s must come from a guardian account; the practitioner's accept screen enforces the age band).
- [ ] Coach links for under-18s are approved by the guardian (built in). Agree whether a club may receive them at all.
- [ ] Decide the retention period for minors' records and put it in the practitioner terms.

## 3. Instrument licences

- [ ] For every published instrument in the catalogue (CSAI-2, SMS-28, POMS, ACSI-28) confirm you hold the right to
      use it in practice. SPPS stores subscale scores only for licensed instruments; the item text is never stored
      (the database refuses it). Record the licence holder and date for each in the owner screen.
- [ ] Custom scales built by practitioners are marked "own" or "open"; remind practitioners not to type in items
      from published tests.

## 4. Crisis contacts

- [ ] Set `SPPS_CRISIS_CONTACTS_TEXT` (public directory and every booking confirmation) with numbers you have
      called and confirmed for the countries you serve.
- [ ] Admin Dashboard → Crisis contacts (shown on every athlete and guardian screen): same check.
- [ ] Re-check both every six months; note the date.

## 5. Credential rotation (before launch, then on any suspicion and at least yearly)

- [ ] Rotate the four database login-role passwords (edit a copy of `99_set_role_passwords.sql`, run it, update the
      four `*_DATABASE_URL` values in Render, redeploy; do not save the edited copy).
- [ ] Rotate `SPPS_FORM_TOKEN_SECRET` (open forms simply ask for a new token).
- [ ] Rotate the field-encryption key with the four steps in `tools/v2_rotate_field_key.py`; keep every key in the
      password manager — without it backups cannot be read.
- [ ] Rotate the SMTP password and the Supabase publishable key if either was ever shared.
- [ ] Confirm no `.env` file and no secret is in any folder, zip or repository (the export tool refuses them).

## 6. Health check and monitoring

- [ ] Render health-check path is `/health/ready` (returns 200 only when the database answers and every v1 and v2
      table exists). `/health/live` is the process-only check.
- [ ] Run `python -m server.v2.preflight` with the production variables loaded: every line says "set" for the
      features you are switching on.
- [ ] Know how to trace a problem: ask for the `X-Request-ID`, search the Render log for `rid=…`.

## 7. Backup and restore

- [ ] Do the backup and restore drill in `DEPLOYMENT.md` (v2 step 5) and record the result before real data.
- [ ] Schedule the drill quarterly.

## 8. Order of switching on (from `DEPLOYMENT.md`)

- [ ] SQL steps 34–41 run, `v2_verify.sql` matches, code deployed with all flags off, health green.
- [ ] Athlete loop on and tested end to end on a phone (Gmail → Chrome → Android app).
- [ ] Directory on and tested; your own listing published and verified.
- [ ] Self-serve on and tested with a fictional account, including deletion.
- [ ] `SPPS_PRODUCTION_RELEASE_APPROVED=true` set last, by the owner.
