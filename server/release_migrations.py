"""Guarded, transactional, in-container hosted SQL migration runner.

Runs only with SPPS_STARTUP_MIGRATIONS=true and separate migration-owner URLs.
The normal FastAPI application continues to use restricted clinical/auth roles.
The runner never creates Supabase users or enables public/clinical access.
Each migration is applied once with a checksum ledger and advisory lock.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = {
    "clinical": ("0017_free_clinical_slots.sql",
                 "0019_practitioner_approval.sql",
                 "0020_owner_approval_rls_grants.sql",
                 "0021_pending_practitioner_registration.sql",
                 "0022_verified_owner_claim.sql",
                 "0023_restricted_self_profile_edit.sql",
                 "0024_owner_dual_role_featured_profile.sql",
                 "0025_public_tenant_read_policy.sql",
                 "0026_pending_approval_free_launch.sql",
                 "0027_email_password_owner_approval.sql",
                 "0028_production_public_tenant.sql",
                 "0029_public_directory_enquiry_owner_profile.sql",
                 "0030_clinical_foreign_key_indexes.sql"),
    "identity": ("0018_free_booking_lifecycle.sql",
                 "0031_identity_foreign_key_indexes.sql"),
}
SCHEMAS = {"clinical": "spps_dev", "identity": "spps_identity_dev"}
URL_KEYS = {"clinical": "SPPS_CLINICAL_MIGRATION_URL",
            "identity": "SPPS_IDENTITY_MIGRATION_URL"}
ROLE_KEYS = {"clinical": "SPPS_CLINICAL_MIGRATION_ROLE",
             "identity": "SPPS_IDENTITY_MIGRATION_ROLE"}
DB_KEYS = {"clinical": "SPPS_CLINICAL_DATABASE_NAME",
           "identity": "SPPS_IDENTITY_DATABASE_NAME"}
DEFAULT_DBS = {"clinical": "spps_dev", "identity": "spps_dev_identity"}
MIGRATION_LOCK = 2506202601


class MigrationRefused(RuntimeError):
    """Release preflight rejected a migration without opening application data."""


def sql_payload(path: Path) -> tuple[str, str]:
    if not path.is_file() or path.name not in {x for files in MIGRATIONS.values() for x in files}:
        raise MigrationRefused("Requested migration file unavailable")
    raw = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    # The existing checked-in migration scripts include BEGIN/COMMIT.
    # The runner provides a single transaction covering script and ledger.
    body, started = re.subn(r"(?m)^BEGIN;[ \t]*(?:\n|$)", "", raw, count=1)
    if started != 1:
        raise MigrationRefused("Migration requires a standalone outer BEGIN")
    body, removed = re.subn(r"\s*COMMIT\s*;\s*$", "", body,
                            count=1, flags=re.IGNORECASE)
    if removed != 1 or not body.strip():
        raise MigrationRefused("Migration requires an outer COMMIT")
    return body, checksum


def validate_connection(kind: str, env: dict[str, str]) -> str:
    if kind not in SCHEMAS:
        raise MigrationRefused("Invalid migration target")
    raw = env.get(URL_KEYS[kind], "")
    expected_role = env.get(ROLE_KEYS[kind], "")
    expected_database = env.get(DB_KEYS[kind], DEFAULT_DBS[kind])
    if not (raw and expected_role and expected_database):
        raise MigrationRefused("Migration owner connection, role and database must be configured")
    try:
        url = make_url(raw)
        if url.drivername not in ("postgresql", "postgresql+psycopg", "postgres"):
            raise MigrationRefused("Migration connection must use PostgreSQL")
        if url.username != expected_role or url.database != expected_database:
            raise MigrationRefused("Migration connection role or database mismatch")
        if expected_role in ("spps_dev_app", "spps_dev_auth",
                             "spps_identity_auth", "spps_identity_portal",
                             "anon", "authenticated"):
            raise MigrationRefused("Refusing an application or public database role for migrations")
        if not url.host or not url.password:
            raise MigrationRefused("Migration connection requires host and password")
        if url.query.get("sslmode", "require") not in ("require", "verify-ca", "verify-full"):
            raise MigrationRefused("Migration connection must use TLS")
        if set(url.query) - {"sslmode", "sslrootcert"}:
            raise MigrationRefused("Unsupported migration database URL options")
        # psycopg accepts postgresql://, but not SQLAlchemy's +psycopg scheme.
        return raw.replace("postgresql+psycopg://", "postgresql://", 1)
    except MigrationRefused:
        raise
    except Exception:
        raise MigrationRefused("Invalid migration connection settings") from None


def migrate_database(kind: str, env: dict[str, str]) -> list[str]:
    uri = validate_connection(kind, env)
    schema = SCHEMAS[kind]
    applied: list[str] = []
    with psycopg.connect(uri, sslmode=make_url(env[URL_KEYS[kind]]).query.get("sslmode","require"), connect_timeout=12) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_user,current_database()")
                current_user, current_db = cursor.fetchone()
                if current_user != env[ROLE_KEYS[kind]] or current_db != env.get(DB_KEYS[kind], DEFAULT_DBS[kind]):
                    raise MigrationRefused("Connected database identity does not match release configuration")
                cursor.execute("SELECT to_regnamespace(%s)", (schema,))
                if cursor.fetchone()[0] is None:
                    raise MigrationRefused("Base protected schema has not been provisioned")
                required = ("practitioner_profiles" if kind == "clinical" else "platform_bookings")
                cursor.execute("SELECT to_regclass(%s)", (f"{schema}.{required}",))
                if cursor.fetchone()[0] is None:
                    raise MigrationRefused("Required earlier schema migrations are absent")
                cursor.execute("CREATE SCHEMA IF NOT EXISTS spps_ops")
                cursor.execute("REVOKE ALL ON SCHEMA spps_ops FROM PUBLIC")
                cursor.execute("""CREATE TABLE IF NOT EXISTS spps_ops.migration_history(
                    name text PRIMARY KEY,
                    sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )""")
                cursor.execute("REVOKE ALL ON spps_ops.migration_history FROM PUBLIC")
        for name in MIGRATIONS[kind]:
            payload, checksum = sql_payload(ROOT / "migrations" / name)
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
                    cursor.execute("SELECT sha256 FROM spps_ops.migration_history WHERE name=%s", (name,))
                    row = cursor.fetchone()
                    if row:
                        if row[0] != checksum:
                            raise MigrationRefused("Already-applied migration checksum changed: " + name)
                        continue
                    # One migration per transaction; privilege changes, DDL and
                    # migration history are rolled back together on any failure.
                    cursor.execute(payload)
                    cursor.execute(
                        "INSERT INTO spps_ops.migration_history(name,sha256) VALUES (%s,%s)",
                        (name,checksum)
                    )
                    applied.append(name)
    return applied


def main() -> None:
    env = dict(os.environ)
    if env.get("SPPS_STARTUP_MIGRATIONS", "false").lower() != "true":
        raise MigrationRefused("Startup migration opt-in not enabled")
    if env.get("MODEL_C_ENV") not in ("staging", "production"):
        raise MigrationRefused("Migrations require a hosted deployment")
    if env.get("SPPS_HOSTED_RLS", "false").lower() != "true":
        raise MigrationRefused("Refusing migrations outside the hosted RLS profile")
    # Fail before touching either DB when the second DB credential is absent.
    for kind in ("clinical","identity"):
        validate_connection(kind,env)
    if env.get("SPPS_SINGLE_DATABASE_SCHEMAS","false").lower()=="true":
        # Identical privileged owner URLs are valid for the ONE database,
        # but every app/API connection uses its own restricted PostgreSQL role.
        targets=[make_url(env[URL_KEYS[kind]]) for kind in ("clinical","identity")]
        if len({(u.host,u.port,u.database,u.username) for u in targets}) != 1:
            raise MigrationRefused("Single-database mode requires the same privileged migration connection for both schemas")
    # Preflight BOTH schemas before applying either side; fail closed before
    # a partial clinical-only release when the identity baseline is missing.
    for kind in ("clinical","identity"):
        uri=validate_connection(kind,env)
        with psycopg.connect(uri,connect_timeout=12) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (SCHEMAS[kind]+("."+"practitioner_profiles" if kind=="clinical" else ".platform_bookings"),))
                if cur.fetchone()[0] is None:
                    raise MigrationRefused("Base protected clinical/identity tables are not provisioned")
    for kind in ("clinical","identity"):
        names = migrate_database(kind,env)
        print(f"SPPS {kind} migrations verified; newly applied: {', '.join(names) or 'none'}", flush=True)
    if env.get("SPPS_OWNER_INVITE_ON_STARTUP","false").lower()=="true":
        from .owner_invitation import invite_owner
        print("SPPS owner invitation:",invite_owner(env),flush=True)


if __name__ == "__main__":
    main()
