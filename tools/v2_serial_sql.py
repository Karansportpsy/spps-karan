"""Generate supabase/sql/NN_XXXX_name.sql for every v2 migration (0033 onwards).

The serial file is the migration body with the same three-line header the v1 set uses and a migration_history
row (name + sha256 of the migration file) inserted before the final COMMIT, exactly like supabase/sql/33_0032.
NN = migration number + 1 (00 is the bootstrap). An existing serial file is never rewritten: if it differs from what
would be generated, the tool fails, because a file that may already have been run must not change.

    python tools/v2_serial_sql.py            # write missing files, verify existing ones
    python tools/v2_serial_sql.py --check    # verify only (used by the tests)
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST_V2 = 33


def v2_migrations() -> list[Path]:
    out = []
    for p in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if int(p.name[:4]) >= FIRST_V2:
            out.append(p)
    return out


def serial_name(migration: Path) -> str:
    return f"{int(migration.name[:4]) + 1:02d}_{migration.name}"


def render(migration: Path) -> str:
    raw = migration.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    step = int(migration.name[:4]) + 1
    header = (f"-- SPPS production database, step {step} of the serial set: {migration.stem}\n"
              "-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.\n"
              f"-- Source: migrations/{migration.name}\n")
    body, n = re.subn(r"\nCOMMIT;\s*$", "\nINSERT INTO spps_ops.migration_history(name,sha256) VALUES "
                      f"('{migration.name}','{sha}') ON CONFLICT (name) DO NOTHING;\nCOMMIT;\n", raw)
    if n != 1 or not raw.startswith("BEGIN;"):
        raise SystemExit(f"{migration.name}: must start with BEGIN; and end with COMMIT;")
    return header + body


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    problems = []
    for m in v2_migrations():
        target = ROOT / "supabase" / "sql" / serial_name(m)
        wanted = render(m)
        if target.exists():
            if target.read_text(encoding="utf-8") != wanted:
                problems.append(f"{target.name} differs from {m.name} (never edit a file that may have been run)")
        elif check_only:
            problems.append(f"{target.name} missing")
        else:
            target.write_text(wanted, encoding="utf-8")
            print("wrote", target.relative_to(ROOT))
    for p in problems:
        print("ERROR:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
