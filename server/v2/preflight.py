"""v2 release preflight: refuse to start hosted mode with a missing variable, an inconsistent flag or a migration
that has not been run. Called from server.hosted_profile.validate_startup (environment) and
server.release_preflight.verify_production (database). Every refusal names its reason in the Render log.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import text

from . import flags

# migration file -> objects it creates that the running code depends on: ("table"|"function", name)
MIGRATION_OBJECTS: dict[str, list[tuple[str, str]]] = {
    "0033_v2_registration_state_machine.sql": [
        ("table", "registration_applications"), ("table", "registration_transitions"),
        ("table", "email_deliveries"), ("function", "v2_submit_pending_application"),
        ("function", "v2_promote_application"), ("function", "v2_set_application_photo"),
        ("function", "v2_application_needs_photo"), ("function", "v2_applicant_resubmitted"),
    ],
    "0034_v2_trust_foundation.sql": [
        ("table", "record_access_log"), ("table", "data_exports"), ("table", "practitioner_settings"),
        ("table", "practitioner_closures"), ("table", "closure_dispositions"),
        ("table", "owner_practitioner_archive"), ("function", "v2_owner_access_counts"),
        ("function", "v2_complete_own_closure"), ("function", "v2_owner_restore_closure"),
        ("function", "v2_owner_purge_closure"), ("function", "v2_erase_athlete_records"),
    ],
    "0035_v2_practitioner_record.sql": [
        ("table", "athlete_profiles"), ("table", "note_templates"), ("table", "note_signatures"),
        ("table", "note_amendments"), ("table", "note_drafts"), ("table", "case_formulations"),
        ("table", "athlete_imports"),
    ],
    "0036_v2_assessments.sql": [
        ("table", "instruments"), ("table", "instrument_subscales"), ("table", "instrument_items"),
        ("table", "instrument_administrations"), ("table", "instrument_responses"), ("table", "instrument_scores"),
        ("function", "v2_no_item_text_for_licensed"),
    ],
    "0037_v2_reports.sql": [
        ("table", "report_templates"), ("table", "shared_reports"), ("table", "report_consents"),
        ("table", "report_links"), ("table", "report_link_views"), ("function", "v2_open_report_link"),
    ],
    "0038_v2_athlete_loop.sql": [
        ("table_identity", "loop_links"), ("table_identity", "loop_accounts"), ("table_identity", "loop_checkins"),
        ("function", "v2_loop_role"), ("function", "v2_loop_participation"),
    ],
    "0039_v2_directory.sql": [
        ("table", "directory_profiles"), ("table", "directory_snapshot"), ("function", "v2_directory_unlist"),
    ],
    "0040_v2_self_serve.sql": [
        ("table_identity", "loop_share_requests"), ("table_identity", "loop_share_views"),
    ],
    "0041_v2_release_hardening.sql": [("function", "v2_open_report_link")],
    # 0042 only drops enquiry objects (expand/contract), so it is checked by the function it recreates.
    "0042_v2_appointment_only_directory.sql": [("function", "v2_erase_athlete_records")],
    "0043_v2_practitioner_services.sql": [
        ("table", "practitioner_services"), ("table", "directory_service_snapshot"),
        ("function", "portal_directory_booking_context"), ("function", "portal_request_directory_booking"),
        ("function", "practitioner_booking_details"), ("function", "practitioner_approve_directory_booking"),
        ("function", "practitioner_decline_directory_link"),
    ],
    "0044_v2_service_snapshot_fk_index.sql": [("table", "directory_service_snapshot")],  # index only
    "0045_v2_appointments_video.sql": [("function", "portal_booking_details")],
    "0046_v2_notifications_push.sql": [("table_identity", "notification_devices"),
                                       ("table_identity", "notification_preferences"),
                                       ("table_identity", "notification_prompts"),
                                       ("table_identity", "notification_reminders"),
                                       ("function", "notification_push_targets"),
                                       ("function", "notification_enqueue_due_reminders"),
                                       ("function", "portal_push_register"),
                                       ("function", "practitioner_push_register")],
}
# flag -> migrations that must have been run before the flag may be switched on
FLAG_MIGRATIONS: dict[str, list[str]] = {}
# variables required in hosted mode: name -> (validator regex, reason)
REQUIRED_HOSTED_VARS: dict[str, tuple[str, str]] = {
    "SPPS_FORM_TOKEN_SECRET": (r".{32,}", "the bot check needs a 32+ character secret"),
    "SPPS_FIELD_KEYS": (r"[A-Za-z0-9]{1,16}:[A-Za-z0-9+/=]{43,44}(?:,[A-Za-z0-9]{1,16}:[A-Za-z0-9+/=]{43,44})*",
                        "consultation notes and formulations are encrypted: <id>:<base64 32-byte key>[,...]"),
    "SPPS_FIELD_KEY_CURRENT": (r"[A-Za-z0-9]{1,16}", "the key id used for new writes"),
}
# variables required only when a flag is on
FLAG_VARS: dict[str, dict[str, tuple[str, str]]] = {}
FLAG_MIGRATIONS["SPPS_PRACTITIONER_CLOSURE_ENABLED"] = ["0034_v2_trust_foundation.sql"]
FLAG_MIGRATIONS["SPPS_REPORT_LINKS_ENABLED"] = ["0037_v2_reports.sql", "0041_v2_release_hardening.sql"]
FLAG_MIGRATIONS["SPPS_ATHLETE_LOOP_ENABLED"] = ["0038_v2_athlete_loop.sql"]
FLAG_MIGRATIONS["SPPS_DIRECTORY_ENABLED"] = ["0039_v2_directory.sql"]
FLAG_MIGRATIONS["SPPS_SELF_SERVE_ENABLED"] = ["0038_v2_athlete_loop.sql", "0039_v2_directory.sql",
                                              "0040_v2_self_serve.sql"]
FLAG_VARS["SPPS_DIRECTORY_ENABLED"] = {
    "SPPS_CRISIS_CONTACTS_TEXT": (r"(?s).{10,600}", "the public directory and every booking confirmation show crisis contacts"),
}
FLAG_MIGRATIONS["SPPS_DIRECTORY_BOOKING_ENABLED"] = ["0039_v2_directory.sql", "0043_v2_practitioner_services.sql"]
FLAG_MIGRATIONS["SPPS_AI_FEATURES_ENABLED"] = ["0034_v2_trust_foundation.sql"]
_MAIL = {
    "SPPS_SMTP_HOST": (r"[A-Za-z0-9.-]{3,253}", "athlete, guardian and coach e-mails are sent by SMTP"),
    "SPPS_SMTP_USERNAME": (r".{1,320}", "SMTP sign-in"),
    "SPPS_SMTP_PASSWORD": (r".{1,500}", "SMTP sign-in"),
    "SPPS_SMTP_FROM": (r"(?:[^<>@]{1,100}\s<[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+>|[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+)",
                       "the sender on every e-mail: name@example.org or Name <name@example.org>"),
    "SPPS_PUBLIC_BASE_URL": (r"https://[A-Za-z0-9.-]+(?::\d+)?/?", "e-mailed links must open the public https site in "
                                                                     "any browser or the app"),
}
FLAG_MIGRATIONS["SPPS_PUSH_ENABLED"] = ["0046_v2_notifications_push.sql"]
FLAG_MIGRATIONS["SPPS_NOTIFY_WORKER_ENABLED"] = ["0046_v2_notifications_push.sql"]
FLAG_VARS["SPPS_PUSH_ENABLED"] = {
    "SPPS_VAPID_PUBLIC_KEY": (r"[A-Za-z0-9_-]{80,100}", "the browser needs the public VAPID key (base64url)"),
    "SPPS_VAPID_PRIVATE_KEY": (r"[A-Za-z0-9_=-]{40,120}", "the server signs web push with the private VAPID key"),
    "SPPS_VAPID_SUBJECT": (r"(?:mailto:[^@\s]+@[^@\s]+\.[^@\s]+|https://[A-Za-z0-9.-]+(?:/\S*)?)",
                           "a generic contact for the push services: mailto:address or https://site"),
}
FLAG_VARS["SPPS_ATHLETE_LOOP_ENABLED"] = dict(_MAIL)
FLAG_VARS["SPPS_SELF_SERVE_ENABLED"] = dict(_MAIL)
FLAG_VARS["SPPS_NOTIFY_WORKER_ENABLED"] = dict(_MAIL)
# Turn-on order recorded in DEPLOYMENT.md: each flag lists the flags that must already be on.
FLAG_ORDER: dict[str, tuple[str, ...]] = {
    "SPPS_ATHLETE_LOOP_ENABLED": ("SPPS_PORTAL_ENABLED",),
    "SPPS_DIRECTORY_ENABLED": (),
    "SPPS_SELF_SERVE_ENABLED": ("SPPS_PORTAL_ENABLED", "SPPS_ATHLETE_LOOP_ENABLED", "SPPS_DIRECTORY_ENABLED"),
    "SPPS_DIRECTORY_BOOKING_ENABLED": ("SPPS_PORTAL_ENABLED", "SPPS_DIRECTORY_ENABLED"),
    "SPPS_PUSH_ENABLED": ("SPPS_PORTAL_ENABLED",),
    "SPPS_NOTIFY_WORKER_ENABLED": ("SPPS_PORTAL_ENABLED",),
}


class PreflightError(RuntimeError):
    pass


def validate_env(env=None) -> None:
    env = os.environ if env is None else env
    for name in flags.FLAGS:
        value = env.get(name, flags.FLAGS[name][0]).strip().lower()
        if value not in ("true", "false"):
            raise PreflightError(f"{name} must be true or false")
    for name, (pattern, reason) in REQUIRED_HOSTED_VARS.items():
        if not re.fullmatch(pattern, env.get(name, "")):
            raise PreflightError(f"{name} is missing or invalid: {reason}")
    if env.get("SPPS_FIELD_KEY_CURRENT", "") not in {
            part.split(":", 1)[0] for part in env.get("SPPS_FIELD_KEYS", "").split(",") if part}:
        raise PreflightError("SPPS_FIELD_KEY_CURRENT must name one of the SPPS_FIELD_KEYS ids")
    on = {n for n in flags.FLAGS if env.get(n, flags.FLAGS[n][0]).strip().lower() == "true"}
    if "SPPS_ATHLETE_LOOP_ENABLED" in on and "SPPS_PORTAL_ENABLED" not in on:
        raise PreflightError("SPPS_ATHLETE_LOOP_ENABLED requires SPPS_PORTAL_ENABLED=true")
    if "SPPS_SELF_SERVE_ENABLED" in on and not {"SPPS_ATHLETE_LOOP_ENABLED", "SPPS_DIRECTORY_ENABLED"} <= on:
        raise PreflightError("SPPS_SELF_SERVE_ENABLED requires the athlete loop and the directory to be on")
    if "SPPS_DIRECTORY_BOOKING_ENABLED" in on and not {"SPPS_PORTAL_ENABLED", "SPPS_DIRECTORY_ENABLED"} <= on:
        raise PreflightError("SPPS_DIRECTORY_BOOKING_ENABLED requires SPPS_PORTAL_ENABLED and SPPS_DIRECTORY_ENABLED")
    for flag in ("SPPS_PUSH_ENABLED", "SPPS_NOTIFY_WORKER_ENABLED"):
        if flag in on and "SPPS_PORTAL_ENABLED" not in on:
            raise PreflightError(f"{flag} requires SPPS_PORTAL_ENABLED=true")
    for flag, needed in FLAG_VARS.items():
        if flag in on:
            for name, (pattern, reason) in needed.items():
                if not re.fullmatch(pattern, env.get(name, "")):
                    raise PreflightError(f"{flag} is on but {name} is missing or invalid: {reason}")


def missing_objects(conn, migrations) -> list[str]:
    missing = []
    for migration in migrations:
        for kind, name in MIGRATION_OBJECTS.get(migration, []):
            if kind in ("table", "table_identity"):
                schema = "spps_dev" if kind == "table" else "spps_identity_dev"
                # Catalog lookup instead of to_regclass(): to_regclass needs USAGE on the schema, and the clinical
                # connection (rightly) has none on the identity schema. pg_class is readable by every role, so the
                # check works on any connection with no grant. It still requires the table and FORCE RLS.
                ok = conn.scalar(text("""SELECT c.relforcerowsecurity FROM pg_class c
                                         JOIN pg_namespace n ON n.oid = c.relnamespace
                                         WHERE n.nspname = :s AND c.relname = :t AND c.relkind IN ('r', 'p')"""),
                                 {"s": schema, "t": name})
            elif kind == "column":
                table, column = name.split(".", 1)
                ok = conn.scalar(text("""SELECT EXISTS (SELECT 1 FROM information_schema.columns
                                         WHERE table_schema='spps_dev' AND table_name=:t AND column_name=:c)"""),
                                 {"t": table, "c": column})
            else:
                ok = conn.scalar(text("""SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                                         WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND p.proname=:n)"""),
                                 {"n": name})
            if not ok:
                missing.append(f"{migration}: {kind} {name}")
    return missing


def verify_database(conn, env=None) -> None:
    env = os.environ if env is None else env
    gaps = missing_objects(conn, MIGRATION_OBJECTS)
    if gaps:
        raise PreflightError("v2 migrations have not been run (see MIGRATIONS.md): " + "; ".join(gaps[:5]))
    for flag, needed in FLAG_MIGRATIONS.items():
        if env.get(flag, "false").strip().lower() == "true":
            gaps = missing_objects(conn, needed)
            if gaps:
                raise PreflightError(f"{flag} is on but its migration has not been run: " + "; ".join(gaps[:5]))


def describe(env=None) -> list[str]:
    """Plain lines for the release checklist: every flag, what it needs, which variables are set (names only, never
    values) and which migrations it relies on. Used by ``python -m server.v2.preflight``."""
    env = os.environ if env is None else env
    on = {n for n in flags.FLAGS if env.get(n, flags.FLAGS[n][0]).strip().lower() == "true"}
    lines = ["Always required in hosted mode:"]
    lines += [f"  {n}: {'set' if re.fullmatch(pat, env.get(n, '')) else 'MISSING/INVALID'}"
              for n, (pat, _) in REQUIRED_HOSTED_VARS.items()]
    lines.append("All v2 migrations must be applied: " + ", ".join(sorted(MIGRATION_OBJECTS)))
    for name in flags.FLAGS:
        state = "on" if name in on else "off"
        needs = [d for d in FLAG_ORDER.get(name, ()) if d not in on]
        lines.append(f"{name}: {state}" + (f" (needs {', '.join(needs)} first)" if state == "on" and needs else ""))
        for var, (pat, _) in FLAG_VARS.get(name, {}).items():
            lines.append(f"  {var}: {'set' if re.fullmatch(pat, env.get(var, '')) else 'missing'}")
        if FLAG_MIGRATIONS.get(name):
            lines.append("  migrations: " + ", ".join(FLAG_MIGRATIONS[name]))
    return lines


if __name__ == "__main__":  # pragma: no cover - manual release aid
    print("\n".join(describe()))

