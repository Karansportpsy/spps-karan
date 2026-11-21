# Guest booking: ask for an appointment without an account

Migration 0047 (serial `48_0047_v2_guest_booking.sql`), flag `SPPS_GUEST_BOOKING_ENABLED` (default off).

## The flow

1. **Find** (`/find`): the card lists the practitioner's services. *Book appointment* opens a dialog: service, then an
   open time (public read, nothing clinical), then name, e-mail, a practical note (no health details), the 18+ box and
   one consent box (terms, privacy, sharing the booking details with the practitioner, and live video for an online
   service). A honeypot and the signed form token guard the form.
2. **"Request submitted, check your mail"** pop-up. Nothing exists yet except a stored request and the hash of a
   one-time link. No account. The practitioner sees nothing.
3. **E-mail confirmation**: the link opens `/find?confirm=...`; pressing *Confirm my request* (not just opening the
   link) creates the account shell, records the consents and creates the usual pending booking.
4. **Practitioner** sees a normal request in Appointments (display name, service, time, note) and approves it.
5. **Approval e-mail** to the guest carries a single-use *create your account* link (14 days). The message names nothing.
6. **Claim**: `/portal?claim=...` asks for a password. The person signs in and finds the appointment, the practitioner
   link (ACTIVE after approval), reminders and Join video.
7. **Video**: Join opens 15 minutes before the start (server window, buttons and reminder all agree). Either side may
   enter first; the room tells the guest they are waiting for the practitioner and tells the practitioner when the
   client arrives. Reminder default is now 15 minutes (`SPPS_REMINDER_MINUTES`).

## Why services were missing and "Book appointment" asked for an account

Services appear only for practitioners who are **published** in the directory: the practitioner submits the profile in
Settings, the owner publishes it in Admin. Until then `/find` is empty. The button used to send everyone to account
creation; it now books as a guest, and keeps a "Have an account? Sign in to book" link.

## Safety properties (tested)

- No booking, account or practitioner-visible row without the proven e-mail address; tokens only as SHA-256 hashes.
- The request answers identically for a new address, an address with a real account (which gets a "sign in instead"
  notice) and a rate-limited address.
- The account shell cannot be signed into until claimed (random unmatchable hash); claim tokens are single use.
- A slot taken between request and confirmation is refused at confirmation and no account is created.
- Adults only; a clinical-word note is refused; limits per IP (10/h) and per address (3/h, 3 open).
- New tables: FORCE RLS, no application grants, covering FK indexes; functions owned by `spps_identity_owner`,
  executable by the auth role only. Unconfirmed requests are purged a week after expiry.

## Tests

Python 631 passed (incl. 5 browser tests, 4 flag/preflight tests); PostgreSQL tier 101 passed (7 new); Android 61/62
(the one failure is the existing missing gitignored `capacitor.config.json`); e2e pins updated to 49 serial steps, 42
identity tables, 46 migration history rows (they run only with the TLS PostgreSQL harness).

## Next Steps & Improvements

### Do now (in this order)
1. Commit, push, merge. In Supabase run `48_0047_v2_guest_booking.sql`, then `v2_verify.sql` (expect 42 identity tables,
   120 identity policies, 15 v2 migrations).
2. **Verify `winmindperform.com` in Resend** and set `SPPS_SMTP_FROM` to an address on it. Until then Resend delivers only
   to your own address, so a real guest receives nothing. Set `SPPS_REMINDER_MINUTES=15`.
3. Make sure Render is live (the VAPID subject fix), then set `SPPS_GUEST_BOOKING_ENABLED=true`.
4. Publish a practitioner: Settings > profile > submit; Admin > publish; add a service and a time.
5. Test in a private window with an address you can read, end to end (DEPLOYMENT.md, step 7).

### Known gaps
- If a guest never claims the account, the appointment still happens; reminders by e-mail go out, but no push (no
  device). Their e-mail link remains valid 14 days; "Forgot password" works for the address at any time.
- A guest who is already an unclaimed shell and books again before claiming reuses the same shell; the claim e-mail is
  re-issued on each approval, older links stop working.
- Slot choice shows the first 36 times; no calendar view or time-zone selector yet (device time zone is used).
- The practitioner cannot yet message the guest before approval (by design: no free text channel).
- Per-process rate limits only; with a second Render instance add a gateway limit.

### Enhancements, ranked
1. Calendar view of open times, grouped by day, with the practitioner's time zone shown.
2. "Add to calendar" (.ics) in the approval e-mail and the Appointments card.
3. Guest cancellation or reschedule from the e-mail link before they have an account.
4. Waiting-room status for the guest ("practitioner is in the room") with a sound cue, and a test-camera step.
5. Optional SMS reminder for guests without push.

### Security and compliance
- Guests are strangers: keep the Terms and Privacy pages current with the guest flow (name and address held until
  confirmation, then in the account) and review the consent wording with your counsel.
- Rotate the Resend key and `SPPS_FIELD_KEYS` that were exposed earlier; keep Dependabot alerts (25) on the list.
- Configure a TURN relay (`MODEL_C_TURN_*`) before relying on video across mobile networks.
- Watch `guest_booking_requests` growth and abuse: the purge runs inside the worker; alert on many `LIMITED` outcomes.
