"""Backup / restore drill and migration ledger for the DISPOSABLE ephemeral cluster only.

    python -m tools.db_drill drill        # backup both databases, restore into fresh drill DBs, verify

Backups use pg_dump custom format from the PostgreSQL 17 client inside the verified
spps-test-ephemeral container (locally) or a PostgreSQL 17 client on PATH (CI).
Every target is checked by tools.ephemeral_guard first; protected environments are
never contacted. The production rollback procedure is "restore the last verified
backup" (docs/OPERATIONS_RUNBOOK.md); this drill proves that path works end to end.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from tools import ephemeral_guard as guard

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "output" / "backups"
MIGRATIONS = ROOT / "migrations"
LEDGER_SCHEMA = "spps_meta"


class DrillError(RuntimeError):
    pass


def admin_password() -> str:
    pw = os.getenv("SPPS_EPHEMERAL_PASSWORD") or dotenv_values(ROOT / "output" / ("test-ephemeral" + (f"-{guard.SLOT}" if guard.SLOT else "")) / "admin.env").get(
        "SPPS_EPHEMERAL_PASSWORD")
    if not pw:
        raise DrillError("ephemeral admin password unavailable (start tools.ephemeral_db up)")
    return pw


def admin_url(database: str) -> URL:
    url = URL.create("postgresql+psycopg", username="postgres", password=admin_password(), host="127.0.0.1",
                     port=guard.PORT, database=database)
    if database in guard.ALLOWED_DATABASES:
        guard.verify_ephemeral_target(url.render_as_string(hide_password=False), database=database)
    else:  # drill databases live in the same verified cluster
        guard.verify_ephemeral_target(url.set(database=guard.CLINICAL_DATABASE).render_as_string(hide_password=False))
    return url


def _client(tool: str) -> list[str]:
    """pg_dump/pg_restore v17+: local binary if new enough, else exec inside the verified container."""
    local = shutil.which(tool)
    if local:
        out = subprocess.run([local, "--version"], capture_output=True, text=True).stdout
        if any(f" {v}." in out for v in range(17, 30)):
            return [local, "-h", "127.0.0.1", "-p", str(guard.PORT)]
    if guard.in_ci_service():
        raise DrillError(f"{tool} 17+ is required on PATH in CI")
    problems = guard.container_problems(guard.inspect_container())
    if problems:
        raise DrillError("ephemeral container not verified: " + "; ".join(problems))
    return ["docker", "exec", "-i", "-e", "PGPASSWORD", guard.CONTAINER, tool]


def backup(database: str, dest: Path) -> dict:
    admin_url(database)
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PGPASSWORD": admin_password()}
    with dest.open("wb") as out:
        proc = subprocess.run([*_client("pg_dump"), "-U", "postgres", "-Fc", "-d", database], stdout=out, stderr=subprocess.PIPE, env=env, timeout=600)
    if proc.returncode or dest.stat().st_size < 1000:
        raise DrillError(f"pg_dump failed: {proc.stderr.decode(errors='replace')[-500:]}")
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    (dest.with_suffix(dest.suffix + ".sha256")).write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
    return {"file": str(dest), "bytes": dest.stat().st_size, "sha256": digest}


def restore(source: Path, database: str) -> None:
    expected = source.with_suffix(source.suffix + ".sha256").read_text(encoding="utf-8").split()[0]
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise DrillError("backup checksum mismatch; refusing to restore")
    if not database.startswith("spps_restore_drill"):
        raise DrillError("restore targets must be fresh spps_restore_drill* databases")
    engine = create_engine(admin_url(guard.CLINICAL_DATABASE), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT count(*) FROM pg_database WHERE datname=:d"), {"d": database}):
                raise DrillError("drill database already exists; restore never overwrites")
            conn.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        engine.dispose()
    env = {**os.environ, "PGPASSWORD": admin_password()}
    with source.open("rb") as data:
        proc = subprocess.run([*_client("pg_restore"), "-U", "postgres", "--exit-on-error", "-d", database],
                              stdin=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=600)
    if proc.returncode:
        raise DrillError(f"pg_restore failed: {proc.stderr.decode(errors='replace')[-800:]}")


def fingerprint(database: str) -> dict:
    """Row counts, RLS flags, policies and SECURITY DEFINER functions per application schema."""
    engine = create_engine(admin_url(database))
    try:
        with engine.connect() as conn:
            tables = conn.execute(text(
                "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' "
                "AND n.nspname IN ('spps_dev','spps_identity_dev') ORDER BY 1,2")).all()
            counts = {f"{s}.{t}": conn.scalar(text(f'SELECT count(*) FROM "{s}"."{t}"')) for s, t, *_ in tables}
            return {
                "tables": {f"{s}.{t}": [bool(r), bool(f)] for s, t, r, f in tables},
                "rows": counts,
                "policies": conn.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname IN ('spps_dev','spps_identity_dev')")),
                "definer_functions": conn.scalar(text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname IN ('spps_dev','spps_identity_dev') AND p.prosecdef")),
                "triggers": conn.scalar(text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n "
                    "ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname IN ('spps_dev','spps_identity_dev')")),
                "exclusion_constraints": conn.scalar(text(
                    "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace "
                    "WHERE c.contype='x' AND n.nspname='spps_identity_dev'")),
            }
    finally:
        engine.dispose()


def drill() -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {"run": stamp, "databases": {}}
    for database in (guard.CLINICAL_DATABASE, guard.IDENTITY_DATABASE):
        before = fingerprint(database)
        dump = backup(database, BACKUPS / f"{database}-{stamp}.dump")
        target = f"spps_restore_drill_{database.replace('spps_dev', 'clin').replace('clin_identity', 'ident')}_{stamp.lower()}"
        restore(Path(dump["file"]), target)
        after = fingerprint(target)
        if before != after:
            diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
            raise DrillError("restored database differs: " + json.dumps(diff, default=str)[:1500])
        report["databases"][database] = {"backup": dump, "restored_into": target, "tables": len(before["tables"]),
                                         "rows": sum(before["rows"].values()), "policies": before["policies"],
                                         "definer_functions": before["definer_functions"], "identical": True}
    (BACKUPS / f"drill-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------- migration ledger
def migration_digest(raw: bytes) -> str:
    """Normalise CRLF/LF before hashing migration content."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def migration_files(names) -> list[tuple[str, str]]:
    return [(n, migration_digest((MIGRATIONS / n).read_bytes())) for n in names]


def _accepted_digests(name: str) -> set[str]:
    """Accept normalised and historical raw line-ending digests."""
    raw = (MIGRATIONS / name).read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {
        hashlib.sha256(lf).hexdigest(),
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(crlf).hexdigest(),
    }


def record_ledger(conn, names) -> None:
    """Called inside the provisioning transaction after migrations apply."""
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {LEDGER_SCHEMA}"))
    conn.execute(text(f"CREATE TABLE IF NOT EXISTS {LEDGER_SCHEMA}.schema_migrations("
                      "version text PRIMARY KEY, sha256 char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"))
    conn.execute(text(f"REVOKE ALL ON SCHEMA {LEDGER_SCHEMA} FROM PUBLIC"))
    for version, digest in migration_files(names):
        conn.execute(text(f"INSERT INTO {LEDGER_SCHEMA}.schema_migrations(version, sha256) VALUES (:v,:s)"),
                     {"v": version, "s": digest})


def verify_ledger(conn, names) -> list[str]:
    """Problems if any applied migration file was edited, removed or never applied."""
    applied = dict(conn.execute(text(f"SELECT version, sha256 FROM {LEDGER_SCHEMA}.schema_migrations")).all())
    problems = []
    for version, _digest in migration_files(names):
        if version not in applied:
            problems.append(f"not applied: {version}")
        elif applied[version].strip() not in _accepted_digests(version):
            problems.append(f"file changed after apply: {version}")
    for version in set(applied) - set(names):
        problems.append(f"applied but missing from source: {version}")
    return problems


if __name__ == "__main__":
    if sys.argv[1:] != ["drill"]:
        raise SystemExit("Usage: python -m tools.db_drill drill")
    try:
        print(json.dumps(drill(), indent=2))
    except (DrillError, guard.EphemeralGuardError) as exc:
        raise SystemExit(f"DRILL FAILED: {exc}")
