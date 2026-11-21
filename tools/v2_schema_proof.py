"""Phase 9 proof on the throwaway local PostgreSQL (tools/v2_pg_harness.py must be up).

1. Database A, brand new and empty: every file of supabase/sql/ applied in order, exactly as the owner would run
   them in the Supabase SQL editor.
2. Database B, brand new and empty: the unchanged v1 serial steps 00-33, then every v2 file of migrations/
   (0033 onwards) applied raw, in number order.
3. The two `pg_dump --schema-only` outputs must be identical (owners, grants, policies, triggers and functions
   included); the migration history must list the same names; the read-only v2 verification must pass on A.
4. Both proof databases are dropped. Nothing here can reach a hosted database: psql connects only to the local
   harness port.

Usage: python tools/v2_schema_proof.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_pg_harness as h  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
A, B = "spps_proof_serial", "spps_proof_migrations"
h.DATABASES = tuple(h.DATABASES) + (A, B)


def dump(db: str) -> str:
    if h.DOCKER:
        out = h.docker_command("pg_dump", db, "--schema-only")
    else:
        out = subprocess.run([f"{h.BIN}/pg_dump" if h.BIN else "pg_dump", "--schema-only", "-h", h.HOST, "-p", str(h.PORT),
                          "-U", "postgres", "-d", db], capture_output=True, text=True, check=True).stdout
    keep = [ln for ln in out.splitlines() if not ln.startswith(("--", "\\restrict", "\\unrestrict")) and ln.strip()]
    return "\n".join(keep)


def main() -> int:
    for db in (A, B):
        h.psql("postgres", sql=f"DROP DATABASE IF EXISTS {db}")
        h.psql("postgres", sql=f"CREATE DATABASE {db}")
    try:
        serial = h.serial_files()
        for f in serial:
            h.psql(A, sql_file=f)
        v2_steps = [f for f in serial if re.match(r"\d\d_00(3[3-9]|[4-9]\d)_", f.name)]
        for f in serial:
            if f not in v2_steps:
                h.psql(B, sql_file=f)
        raw = sorted(p for p in (ROOT / "migrations").glob("00*.sql") if int(p.name[:4]) >= 33)
        assert [f.name.split("_", 1)[1] for f in v2_steps] == [p.name for p in raw], "serial and migrations differ"
        import hashlib
        for p in raw:
            h.psql(B, sql_file=p)
            h.psql(B, sql=f"INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('{p.name}',"
                          f"'{hashlib.sha256(p.read_bytes()).hexdigest()}') ON CONFLICT (name) DO NOTHING")
        da, db_ = dump(A), dump(B)
        if da != db_:
            import difflib
            sys.stdout.writelines(difflib.unified_diff(da.splitlines(True), db_.splitlines(True), A, B, n=1))
            print("\nFAIL: schemas differ")
            return 1
        hist = "SELECT string_agg(name||':'||sha256, ',' ORDER BY name) FROM spps_ops.migration_history"
        if h.psql(A, "-t", "-A", sql=hist) != h.psql(B, "-t", "-A", sql=hist):
            print("FAIL: migration history differs")
            return 1
        verify = h.psql(A, "-t", "-A", "-F", "=", sql_file=ROOT / "supabase" / "sql" / "v2_verify.sql")
        print(verify.strip())
        values = dict(line.split("=", 1) for line in verify.strip().splitlines())
        bad = [k for k, v in values.items() if k.startswith("FAIL")] + [k for k in ("force_rls_missing",
               "api_roles_with_access", "anon_execute_on_definer") if values.get(k) != "0"]
        if bad:
            print("FAIL:", bad)
            return 1
        print(f"OK: {len(serial)} serial files and migrations/ (v1 steps + {len(raw)} raw v2 files) give identical "
              f"schemas ({len(da.splitlines())} lines of pg_dump), identical history, verification passed")
        return 0
    finally:
        for db in (A, B):
            h.psql("postgres", sql=f"DROP DATABASE IF EXISTS {db}")


if __name__ == "__main__":
    raise SystemExit(main())
