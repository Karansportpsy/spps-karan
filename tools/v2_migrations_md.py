"""Regenerate MIGRATIONS.md from supabase/sql/ and migrations/ (run after adding a migration).

v1 serial files keep the one-line descriptions below; each v2 migration describes itself in its first comment line
after BEGIN (``-- 00NN (v2 phase N): ...``).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1 = {
    "00_bootstrap_roles_schemas.sql": "Extensions, the four restricted login roles (no passwords yet), schemas and the migration ledger.",
    "01_baseline_tables.sql": "Baseline clinical (spps_dev) and identity (spps_identity_dev) tables as of v1.0.",
    "02_0002_isolated_development_rls.sql": "FORCE row-level security on every clinical table; owner-scoped practitioner policies.",
    "03_0003_local_clinical_parity.sql": "Clinical write policies for notes, assignments, reports and injury records.",
    "04_0010_clinical_slot_management.sql": "Practitioners manage only their own appointment slots.",
    "05_0014_clinical_admin_review.sql": "Administrator credential/publication review under FORCE RLS (current_is_admin).",
    "06_identity_schema_ownership.sql": "Identity schema ownership moved to the NOLOGIN spps_identity_owner role.",
    "07_0004_identity_review_roles_rls.sql": "Identity roles and RLS for athlete/guardian accounts.",
    "08_0005_identity_auth_functions.sql": "Athlete/guardian authentication functions (identity schema).",
    "09_0006_guardian_authority_consent.sql": "Guardian authority, reviewer verification and the consent ledger.",
    "10_0007_practitioner_links.sql": "Practitioner-athlete links (identity schema).",
    "11_0008_link_invitation_preview.sql": "Signed-in athlete/guardian can preview which practitioner issued an invitation code.",
    "12_0009_approval_first_booking.sql": "Approval-first booking, payment ledger and notification outbox.",
    "13_0011_clinical_participation.sql": "Athlete participation projections (identity schema).",
    "14_0012_platform_video.sql": "Video consultation for confirmed platform bookings.",
    "15_0013_notification_delivery.sql": "Durable notification delivery (outbox worker).",
    "16_0015_institutional_licences.sql": "Organisation seat licences assigned by the tenant administrator.",
    "17_0016_video_room_closure.sql": "Ending a video consultation is final.",
    "18_0017_free_clinical_slots.sql": "Free appointments without rewriting historical payments.",
    "19_0018_free_booking_lifecycle.sql": "Zero-charge booking lifecycle.",
    "20_0019_practitioner_approval.sql": "Practitioner approval gate in the clinical schema.",
    "21_0020_owner_approval_rls_grants.sql": "Narrow grants for owner approval actions.",
    "22_0021_pending_practitioner_registration.sql": "Verified-email, pending-only registration function.",
    "23_0022_verified_owner_claim.sql": "One-time verified owner claim.",
    "24_0023_restricted_self_profile_edit.sql": "Practitioner profile edits without writable review flags.",
    "25_0024_owner_dual_role_featured_profile.sql": "Owner can be administrator and practitioner; featured profile.",
    "26_0025_public_tenant_read_policy.sql": "Restricted app may read only the public tenant.",
    "27_0026_pending_approval_free_launch.sql": "Fail-closed pending_approval default for every non-owner sign-up.",
    "28_0027_email_password_owner_approval.sql": "Email/password owner bootstrap; only the owner address may be admin.",
    "29_0028_production_public_tenant.sql": "Production public tenant winmindperform.",
    "30_0029_public_directory_enquiry_owner_profile.sql": "Public directory, public enquiry, resubmission and owner profile functions.",
    "31_0030_clinical_foreign_key_indexes.sql": "Covering indexes for clinical foreign keys.",
    "32_0031_identity_foreign_key_indexes.sql": "Covering indexes for identity foreign keys.",
    "33_0032_account_lifecycle.sql": "Practitioner self-service listing deactivation (account lifecycle phase A).",
    "v2_verify.sql": "Read-only check of the whole v1 + v2 database (run after step 41; expected values at the top).",
    "98_verify.sql": "Read-only check of the v1 objects (run after the serial files; v1 expected values).",
    "99_set_role_passwords.sql": "Template: set the four login-role passwords (edit a copy, never save it).",
}


def describe_v2(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines[1:6], 1):
        m = re.match(r"--\s*\d{4}\s*\(v2[^)]*\):\s*(.+)", line)
        if m:
            text = m.group(1).strip()
            for more in lines[i + 1:i + 4]:  # the summary may wrap onto following comment lines
                if not more.startswith("-- ") or more.strip() == "--" or more.startswith("-- *"):
                    break
                text += " " + more[3:].strip()
            text = text.split(". ")[0].rstrip(".") + "."
            return text[0].upper() + text[1:]
    return "v2 migration."


def main() -> None:
    serial = sorted(p for p in (ROOT / "supabase" / "sql").glob("[0-9][0-9]_*.sql"))
    verify = ROOT / "supabase" / "sql" / "v2_verify.sql"  # unnumbered: v1 keeps only 98/99 outside the serial steps
    if verify.exists():
        at = next((i for i, p in enumerate(serial) if p.name.startswith("98_")), len(serial))
        serial.insert(at, verify)
    lines = ["# Database migrations — run order", "",
             "A brand-new empty Supabase project is built by running every file in `supabase/sql/` in this order, in",
             "the SQL editor, as the default `postgres` role. Each file is one transaction. `supabase/sql/NN_XXXX_*.sql`",
             "is generated from `migrations/XXXX_*.sql` (`python tools/v2_serial_sql.py`) and records itself in",
             "`spps_ops.migration_history`. Never edit a file that may have been run: every change is a new numbered file.",
             "", "| # | File | What it does |", "|---|---|---|"]
    for i, p in enumerate(serial, 1):
        if p.name in V1:
            desc = V1[p.name]
        else:
            src = ROOT / "migrations" / p.name[3:]
            desc = describe_v2(src) if src.exists() else "Verification script."
        lines.append(f"| {i} | `{p.name}` | {desc} |")
    lines += ["", "## Source migrations (`migrations/`)", "",
              "`0001`–`0032` are the v1 sources (applied to the v1 hosted database through the serial set above).",
              "`0033` onwards are the v2 sources; each has exactly one generated serial file.", ""]
    for m in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if int(m.name[:4]) >= 33:
            lines.append(f"- `{m.name}` → `supabase/sql/{int(m.name[:4]) + 1:02d}_{m.name}` — {describe_v2(m)}")
        else:
            lines.append(f"- `{m.name}`")
    lines += ["", "After the serial files, run `v2_verify.sql` (whole database; expected values at the top), optionally",
              "`98_verify.sql` (v1 objects only; its counts describe a v1-only database), then",
              "`99_set_role_passwords.sql` from an edited copy.", ""]
    (ROOT / "MIGRATIONS.md").write_text("\n".join(lines), encoding="utf-8")
    print("MIGRATIONS.md:", len(serial), "serial files")


if __name__ == "__main__":
    main()
