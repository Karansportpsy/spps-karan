"""Hosted-staging PostgreSQL provisioning and forward-only migrations — LOCKED STAGING, FICTIONAL DATA.

Targets a NEW, EMPTY Render managed PostgreSQL 17 instance (or, in automated tests only, the
verified disposable ``spps-test-ephemeral`` cluster). It never targets Supabase or any protected
local database, never needs a superuser and never prints credentials.

    python -m tools.hosted_db preflight --confirm-host <host>
    python -m tools.hosted_db provision --confirm-host <host> --identity-database <name> \
        --public-host <spps hostname> --confirm FRESH_FICTIONAL_STAGING_ONLY
    python -m tools.hosted_db verify    --confirm-host <host> --identity-database <name>
    python -m tools.hosted_db migrate   --confirm-host <host> --identity-database <name> \
        --backup-manifest <dump>.sha256

``SPPS_HOSTED_ADMIN_URL`` (environment only) is the instance's default-user connection URL from
the Render dashboard. Generated role passwords and fictional practitioner passwords are written
once to an ignored ``output/hosted-staging/`` file (mode 0600, never overwritten), to be copied
into Render's secret environment variables.

Privilege model (works with a CREATEROLE + CREATEDB database owner, no superuser):
* clinical database: tables owned by the admin, FORCE RLS (0002), login roles ``spps_dev_app`` /
  ``spps_dev_auth`` with column-level grants;
* identity database: tables, schema and SECURITY DEFINER functions owned by NOLOGIN
  ``spps_identity_owner``; the admin holds a SET grant on it only while provisioning/migrating;
* CONNECT/TEMP revoked from PUBLIC on both databases; each login role may connect to one database.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from server import hosted_profile as hp

ROOT = Path(__file__).resolve().parents[1]
CONFIRM = "FRESH_FICTIONAL_STAGING_ONLY"
OWNER = "spps_identity_owner"
LOGIN_ROLES = (hp.CLINICAL_APP_ROLE, hp.CLINICAL_AUTH_ROLE, hp.IDENTITY_AUTH_ROLE, hp.IDENTITY_PORTAL_ROLE)
ALL_ROLES = LOGIN_ROLES + (OWNER,)
RENDER_HOST = re.compile(r"dpg-[a-z0-9]{10,40}-a(?:\.[a-z0-9-]+-postgres\.render\.com)?")
REFUSED_HOST_FRAGMENTS = ("supabase.co", "supabase.com", "supabase.net", "pooler.supabase")
FICTIONAL_PRACTITIONERS = ("alice", "bob", "carol")  # carol: fictional tenant administrator (13D reviews)
OUTPUT = ROOT / "output" / "hosted-staging"


class HostedDbError(RuntimeError):
    pass


def _migrations():
    from tools.development_db import CLINICAL_MIGRATIONS
    from tools.identity_review_db import IDENTITY_MIGRATIONS, IDENTITY_TABLES
    return CLINICAL_MIGRATIONS, IDENTITY_MIGRATIONS, IDENTITY_TABLES


# ------------------------------------------------------------------ target safety

def admin_target(raw: str, *, confirm_host: str) -> URL:
    """Validate (never connect) the admin URL. Returns it WITHOUT query options."""
    url = hp.normalize(raw)
    host = (url.host or "").lower()
    if url.get_backend_name() != "postgresql" or not host or not url.username or not url.password or not url.database:
        raise HostedDbError("SPPS_HOSTED_ADMIN_URL must be a complete PostgreSQL URL")
    if any(f in host for f in REFUSED_HOST_FRAGMENTS):
        raise HostedDbError("Supabase databases are never provisioned by this tool")
    if host != confirm_host.lower():
        raise HostedDbError("--confirm-host does not match the admin URL host")
    if set(url.query) - hp.ALLOWED_QUERY:
        raise HostedDbError("Admin URL options are restricted to sslmode/sslrootcert")
    hp.sslmode(url)
    if host in ("localhost", "127.0.0.1", "::1"):
        # Loopback is accepted ONLY for the verified disposable test cluster (never protected ports).
        from tools.ephemeral_guard import EphemeralGuardError, verify_ephemeral_target
        try:
            verify_ephemeral_target(url.set(query={}).render_as_string(hide_password=False))
        except EphemeralGuardError as exc:
            raise HostedDbError(str(exc)) from None
    elif not RENDER_HOST.fullmatch(host):
        raise HostedDbError("Hosted provisioning targets Render PostgreSQL hostnames (dpg-…-a) only")
    return url


def _engine(url: URL, database: str | None = None, **kw):
    target = url.set(database=database or url.database)
    return create_engine(target.set(query={}), connect_args=hp.connect_args(url), **kw)


def _raw(conn):
    return conn.connection.driver_connection


# ------------------------------------------------------------------ preflight

def preflight(url: URL) -> dict:
    """Read-only capability report. Raises when the instance cannot host the profile safely."""
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            facts = conn.execute(text(
                "SELECT current_setting('server_version_num')::int AS version, r.rolsuper, r.rolcreaterole, "
                "r.rolcreatedb, r.rolbypassrls, (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls, "
                "pg_get_userbyid(d.datdba)=current_user AS owns_database "
                "FROM pg_roles r, pg_database d WHERE r.rolname=current_user AND d.datname=current_database()")).one()
            existing_roles = conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:r)"),
                                          {"r": list(ALL_ROLES)}).scalars().all()
            schemas = conn.execute(text("SELECT nspname FROM pg_namespace WHERE nspname IN ('spps_dev','spps_meta')"))\
                .scalars().all()
            user_tables = conn.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
                                           "('pg_catalog','information_schema')"))
            gist = conn.scalar(text("SELECT count(*) FROM pg_available_extensions WHERE name='btree_gist'"))
    finally:
        engine.dispose()
    report = {"postgres_major": facts.version // 10000, "tls": bool(facts.tls), "superuser": facts.rolsuper,
              "createrole": facts.rolcreaterole, "createdb": facts.rolcreatedb, "bypassrls": facts.rolbypassrls,
              "owns_database": facts.owns_database, "existing_roles": sorted(existing_roles),
              "existing_schemas": sorted(schemas), "user_tables": user_tables, "btree_gist": bool(gist)}
    problems = []
    if report["postgres_major"] < 17:
        problems.append("PostgreSQL 17 or newer required")
    if not report["tls"]:
        problems.append("admin connection is not using TLS")
    if not (report["createrole"] or report["superuser"]):
        problems.append("admin role lacks CREATEROLE")
    if not report["owns_database"]:
        problems.append("admin role does not own the clinical database")
    if not report["btree_gist"]:
        problems.append("btree_gist extension unavailable")
    report["problems"] = problems
    return report


# ------------------------------------------------------------------ provisioning

@contextmanager
def _owner_set_grant(conn):
    """Temporarily allow the admin to SET ROLE / assign ownership to the NOLOGIN identity owner."""
    raw = _raw(conn)
    raw.execute(sql.SQL("GRANT {} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE").format(sql.Identifier(OWNER)))
    try:
        yield
    finally:
        raw.execute(sql.SQL("REVOKE {} FROM CURRENT_USER").format(sql.Identifier(OWNER)))


def _create_role(raw, name: str, password: str | None) -> None:
    if password is None:
        raw.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                            "NOREPLICATION NOBYPASSRLS").format(sql.Identifier(name)))
    else:
        raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20").format(
            sql.Identifier(name), sql.Literal(password)))


def _restrict_connect(raw, database: str, roles) -> None:
    db = sql.Identifier(database)
    raw.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(db))
    raw.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
        db, sql.SQL(", ").join(sql.Identifier(r) for r in roles)))


def _seed_clinical(conn, credentials: dict) -> None:
    from server.models import (Factor, Intervention, Practitioner, PractitionerProfile, PractitionerSubscription,
                               Tenant, now)
    from server.security import make_password
    with Session(bind=conn) as db:
        tenant = Tenant(slug="fictional-hosted-staging", display_name="Fictional Hosted Staging Practice")
        db.add(tenant)
        db.flush()
        for name in FICTIONAL_PRACTITIONERS:
            user = Practitioner(tenant_id=tenant.id, email=f"{name}@spps.test", name=f"Fictional {name.title()}",
                                password_hash=make_password(credentials[name]), tier="core", is_admin=name == "carol")
            db.add(user)
            db.flush()
            # Explicitly pre-approve only the installer-controlled fictional
            # practitioners so hosted test sessions can exercise clinical RLS.
            # This does not affect any public registration path or directory listing.
            db.add(PractitionerProfile(practitioner_id=user.id, qualifications=f"Fictional MSc ({name})",
                                       experience_years=5, public_approved=False, credentials_verified=False,
                                       status="approved", email_verified_at=now(), approved_at=now()))
            db.add(PractitionerSubscription(practitioner_id=user.id, plan="core", amount_paise=19900, status="active",
                                            test_mode=True, current_period_start=now(),
                                            current_period_end=now() + timedelta(days=90)))
        db.add(Factor(tenant_id=tenant.id, label="Confidence", group_name="Fictional monitoring", kind="monitoring",
                      low=0, high=10, unit="points"))
        db.add(Intervention(tenant_id=tenant.id, name="Fictional pre-performance breathing",
                            purpose="Fictional demonstration only", method="Practice a brief breathing reset",
                            evidence_note="Synthetic reference entry; professional review required",
                            reference_url="", approved=True))
        db.flush()


def _connection_url(url: URL, role: str, password: str, database: str) -> str:
    mode = hp.sslmode(url)
    query = {"sslmode": mode}
    if url.query.get("sslrootcert"):
        query["sslrootcert"] = url.query["sslrootcert"]
    return url.set(username=role, password=password, database=database, query=query)\
        .render_as_string(hide_password=False)


def provision(url: URL, *, identity_database: str, public_host: str) -> dict:
    """Create roles, both databases' schemas, FORCE RLS, ledgers and fictional practitioners. Fresh only."""
    clinical_migrations, identity_migrations, identity_tables = _migrations()
    clinical_database = url.database
    hp.database_names({"SPPS_CLINICAL_DATABASE_NAME": clinical_database,
                       "SPPS_IDENTITY_DATABASE_NAME": identity_database})
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+", public_host):
        raise HostedDbError("--public-host must be the exact public SPPS hostname (no scheme, no wildcard)")
    report = preflight(url)
    if report["problems"]:
        raise HostedDbError("Preflight failed: " + "; ".join(report["problems"]))
    if report["existing_roles"] or report["existing_schemas"] or report["user_tables"]:
        raise HostedDbError("Instance is not fresh (SPPS roles, schemas or tables exist); refusing to modify it")
    from server.db import Base
    from server.platform_identity_models import IdentityBase
    import server.models  # noqa: F401 - registers the 32 clinical tables
    if len(Base.metadata.tables) != 32 or len(IdentityBase.metadata.tables) != identity_tables:
        raise HostedDbError("Unexpected ORM table count")
    credentials = {role: secrets.token_urlsafe(32) for role in LOGIN_ROLES}
    credentials.update({name: secrets.token_urlsafe(18) for name in FICTIONAL_PRACTITIONERS})
    from tools.db_drill import record_ledger

    admin = _engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            raw = _raw(conn)
            for role in LOGIN_ROLES:
                _create_role(raw, role, credentials[role])
            _create_role(raw, OWNER, None)
            exists = conn.scalar(text("SELECT count(*) FROM pg_database WHERE datname=:d"), {"d": identity_database})
            if not exists:
                raw.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(identity_database)))
            _restrict_connect(raw, clinical_database, (hp.CLINICAL_APP_ROLE, hp.CLINICAL_AUTH_ROLE))
            _restrict_connect(raw, identity_database, (hp.IDENTITY_AUTH_ROLE, hp.IDENTITY_PORTAL_ROLE))
    finally:
        admin.dispose()

    clinical = _engine(url)
    try:
        with clinical.begin() as conn:
            raw = _raw(conn)
            conn.execute(text("CREATE SCHEMA spps_dev"))
            conn.execute(text("SET LOCAL search_path=spps_dev"))
            Base.metadata.create_all(conn)
            _seed_clinical(conn, credentials)
            for name in clinical_migrations:
                raw.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
            record_ledger(conn, clinical_migrations)
            forced = conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                      "WHERE n.nspname='spps_dev' AND c.relkind='r' AND c.relforcerowsecurity"))
            if forced != 32:
                raise HostedDbError("Every clinical table must FORCE RLS")
    finally:
        clinical.dispose()

    identity = _engine(url, identity_database)
    try:
        with identity.begin() as conn:
            raw = _raw(conn)
            if conn.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
                                "('pg_catalog','information_schema')")):
                raise HostedDbError("Identity database is not empty")
            with _owner_set_grant(conn):
                # Build everything AS the NOLOGIN owner: tables, sequences, SECURITY DEFINER functions and the
                # schema are owner-held from creation (no superuser needed for later ALTER TABLE in migrations).
                raw.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                    sql.Identifier(identity_database), sql.Identifier(OWNER)))
                raw.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER)))
                raw.execute("CREATE SCHEMA spps_identity_dev")
                IdentityBase.metadata.create_all(conn)
                for name in identity_migrations:
                    raw.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
                raw.execute("RESET ROLE")
                record_ledger(conn, identity_migrations)
                raw.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
                    sql.Identifier(identity_database), sql.Identifier(OWNER)))
            forced = conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                      "WHERE n.nspname='spps_identity_dev' AND c.relkind='r' "
                                      "AND c.relrowsecurity AND c.relforcerowsecurity"))
            if forced != identity_tables:
                raise HostedDbError("Every identity table must FORCE RLS")
    finally:
        identity.dispose()

    problems = verify(url, identity_database=identity_database)
    if problems:
        raise HostedDbError("Post-provision verification failed: " + "; ".join(problems))
    return {
        "DATABASE_URL": _connection_url(url, hp.CLINICAL_APP_ROLE, credentials[hp.CLINICAL_APP_ROLE], clinical_database),
        "SPPS_AUTH_DATABASE_URL": _connection_url(url, hp.CLINICAL_AUTH_ROLE, credentials[hp.CLINICAL_AUTH_ROLE],
                                                  clinical_database),
        "SPPS_IDENTITY_AUTH_DATABASE_URL": _connection_url(url, hp.IDENTITY_AUTH_ROLE,
                                                           credentials[hp.IDENTITY_AUTH_ROLE], identity_database),
        "SPPS_IDENTITY_PORTAL_DATABASE_URL": _connection_url(url, hp.IDENTITY_PORTAL_ROLE,
                                                             credentials[hp.IDENTITY_PORTAL_ROLE], identity_database),
        "SPPS_CLINICAL_DATABASE_NAME": clinical_database,
        "SPPS_IDENTITY_DATABASE_NAME": identity_database,
        "MODEL_C_ALLOWED_HOSTS": public_host,
        "SPPS_PUBLIC_BASE_URL": f"https://{public_host}",
        **{f"FICTIONAL_{name.upper()}_PASSWORD": credentials[name] for name in FICTIONAL_PRACTITIONERS},
    }


# ------------------------------------------------------------------ verification

def verify(url: URL, *, identity_database: str) -> list[str]:
    """Read-only. Problems in roles, RLS, ownership, CONNECT isolation or migration ledgers."""
    clinical_migrations, identity_migrations, identity_tables = _migrations()
    from tools.db_drill import verify_ledger
    problems: list[str] = []
    checks = ((url.database, "spps_dev", 32, clinical_migrations, (hp.CLINICAL_APP_ROLE, hp.CLINICAL_AUTH_ROLE)),
              (identity_database, "spps_identity_dev", identity_tables, identity_migrations,
               (hp.IDENTITY_AUTH_ROLE, hp.IDENTITY_PORTAL_ROLE)))
    for database, schema, tables, migrations, allowed in checks:
        engine = _engine(url, database)
        try:
            with engine.connect() as conn:
                forced = conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                          "WHERE n.nspname=:s AND c.relkind='r' AND c.relrowsecurity "
                                          "AND c.relforcerowsecurity"), {"s": schema})
                if forced != tables:
                    problems.append(f"{schema}: {forced}/{tables} tables FORCE RLS")
                problems += [f"{schema}: {p}" for p in verify_ledger(conn, migrations)]
                connectable = set(conn.execute(text(
                    "SELECT r.rolname FROM pg_roles r WHERE r.rolcanlogin AND NOT r.rolsuper "
                    "AND r.rolname = ANY(:roles) AND has_database_privilege(r.oid, current_database(), 'CONNECT')"),
                    {"roles": list(LOGIN_ROLES)}).scalars())
                if connectable != set(allowed):
                    problems.append(f"{database}: unexpected CONNECT privileges")
                if conn.scalar(text("SELECT has_database_privilege('public', current_database(), 'CONNECT')")):
                    problems.append(f"{database}: PUBLIC may connect")
                owned = conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                         "WHERE n.nspname=:s AND pg_get_userbyid(c.relowner) = ANY(:roles)"),
                                    {"s": schema, "roles": list(LOGIN_ROLES)})
                if owned:
                    problems.append(f"{schema}: objects owned by a login role")
                if schema == "spps_identity_dev":
                    stray = conn.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                                             "ON n.oid=c.relnamespace WHERE n.nspname=:s AND c.relkind IN ('r','S') "
                                             "AND pg_get_userbyid(c.relowner) <> :o"), {"s": schema, "o": OWNER})
                    if stray:
                        problems.append(f"{schema}: {stray} relations not owned by {OWNER}")
                unsafe = conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname = ANY(:r) AND "
                                          "(rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)"),
                                     {"r": list(ALL_ROLES)})
                if unsafe:
                    problems.append("an SPPS role has elevated attributes")
                members = conn.scalar(text("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                                           "WHERE r.rolname = ANY(:r)"), {"r": list(LOGIN_ROLES)})
                if members:
                    problems.append("an SPPS login role is a member of another role")
        finally:
            engine.dispose()
    return sorted(set(problems))


# ------------------------------------------------------------------ forward-only migrations

def _verified_backup(manifest: Path) -> None:
    """Require a dump + its .sha256 that match (python -m tools.db_drill style) before any migration."""
    if manifest.suffix != ".sha256" or not manifest.is_file():
        raise HostedDbError("--backup-manifest must be an existing <dump>.sha256 file")
    dump = manifest.with_suffix("")
    if not dump.is_file():
        raise HostedDbError("Backup dump named by the manifest is missing")
    expected = manifest.read_text(encoding="utf-8").split()[0].lower()
    digest = hashlib.sha256()
    with dump.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise HostedDbError("Backup checksum mismatch; refusing to migrate")


def pending(url: URL, *, identity_database: str) -> dict:
    clinical_migrations, identity_migrations, _ = _migrations()
    out = {}
    for database, migrations in ((url.database, clinical_migrations), (identity_database, identity_migrations)):
        engine = _engine(url, database)
        try:
            with engine.connect() as conn:
                applied = set(conn.execute(text("SELECT version FROM spps_meta.schema_migrations")).scalars())
        finally:
            engine.dispose()
        out[database] = [m for m in migrations if m.removesuffix(".sql") not in applied and m not in applied]
    return out


def migrate(url: URL, *, identity_database: str, backup_manifest: Path) -> dict:
    """Apply new migration files, in order, one transaction per database. Never edits applied files."""
    _verified_backup(backup_manifest)
    problems = [p for p in verify(url, identity_database=identity_database) if "not applied" not in p]
    if problems:
        raise HostedDbError("Refusing to migrate an inconsistent database: " + "; ".join(problems))
    from tools.db_drill import record_ledger
    todo = pending(url, identity_database=identity_database)
    for database, names in todo.items():
        if not names:
            continue
        engine = _engine(url, database)
        try:
            with engine.begin() as conn:
                raw = _raw(conn)
                if database == identity_database:
                    with _owner_set_grant(conn):
                        raw.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER)))
                        for name in names:
                            raw.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
                        raw.execute("RESET ROLE")
                        record_ledger(conn, names)
                else:
                    conn.execute(text("SET LOCAL search_path=spps_dev"))
                    for name in names:
                        raw.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
                    record_ledger(conn, names)
        finally:
            engine.dispose()
    problems = verify(url, identity_database=identity_database)
    if problems:
        raise HostedDbError("Post-migration verification failed: " + "; ".join(problems))
    return todo


# ------------------------------------------------------------------ CLI

def _write_access_file(host: str, values: dict) -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{host.split('.')[0]}-render-env.secret"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("# SECRET. Copy into Render environment variables (secret), then delete this file.\n")
        stream.write("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["preflight", "provision", "verify", "migrate"])
    ap.add_argument("--confirm-host", required=True)
    ap.add_argument("--identity-database")
    ap.add_argument("--public-host")
    ap.add_argument("--confirm")
    ap.add_argument("--backup-manifest", type=Path)
    args = ap.parse_args(argv)
    url = admin_target(os.environ.get("SPPS_HOSTED_ADMIN_URL", ""), confirm_host=args.confirm_host)
    if args.command == "preflight":
        report = preflight(url)
        for key, value in report.items():
            print(f"{key}: {value}")
        return 1 if report["problems"] else 0
    if not args.identity_database:
        raise HostedDbError("--identity-database is required")
    if args.command == "provision":
        if args.confirm != CONFIRM or not args.public_host:
            raise HostedDbError(f"provision requires --public-host and --confirm {CONFIRM}")
        path = _write_access_file(url.host, {})  # reserve the file first: never provision without a place for secrets
        try:
            values = provision(url, identity_database=args.identity_database, public_host=args.public_host)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        path.unlink()
        path = _write_access_file(url.host, values)
        print("PASS: hosted staging provisioned (32 clinical + identity FORCE RLS tables, ledgers verified).")
        print(f"Secrets written once to ignored {path.relative_to(ROOT)}; nothing printed.")
        return 0
    if args.command == "verify":
        problems = verify(url, identity_database=args.identity_database)
        print("PASS: hosted staging verified" if not problems else "FAIL:\n  " + "\n  ".join(problems))
        return 1 if problems else 0
    if not args.backup_manifest:
        raise HostedDbError("migrate requires --backup-manifest <dump>.sha256")
    applied = migrate(url, identity_database=args.identity_database, backup_manifest=args.backup_manifest)
    print("PASS: applied " + (", ".join(f"{db}: {len(n)}" for db, n in applied.items())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (HostedDbError, hp.HostedProfileError) as exc:
        sys.exit(f"HOSTED DB STOP: {exc}")
