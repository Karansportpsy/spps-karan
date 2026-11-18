"""Throwaway local PostgreSQL for the v2 RLS tests and the serial-SQL proof. Never touches a hosted database.

    python tools/v2_pg_harness.py up        # initdb in /tmp, start on 127.0.0.1:55437, apply supabase/sql in order
    python tools/v2_pg_harness.py env       # print the environment for tests_v2_pg (loopback URLs only)
    python tools/v2_pg_harness.py down      # stop the server and delete its data directory

Guards: the only port is 55437, the only host is 127.0.0.1, the only databases are spps_dev_v2test and
spps_dev_v2mig, and the data directory lives under /tmp/spps-v2-pg (tmpfs-friendly, deleted by ``down``).
Role passwords are random per run and written to /tmp/spps-v2-pg/env (mode 600), never into the repository.
Needs the PostgreSQL 16 server binaries (initdb, pg_ctl). As root, the server runs as the ``postgres`` OS user.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = Path("/tmp/spps-v2-pg")
DATA = BASE / "data"
PORT = 55437
HOST = "127.0.0.1"
DATABASES = ("spps_dev_v2test", "spps_dev_v2mig", "spps_dev_identity")
LOGIN_ROLES = ("spps_dev_app", "spps_dev_auth", "spps_identity_auth", "spps_identity_portal")
BIN = next((p for p in ("/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin") if Path(p).is_dir()), "")


def _as_pg(cmd: str) -> None:
    full = f"export PATH={BIN}:$PATH; {cmd}"
    if os.geteuid() == 0:
        subprocess.run(["su", "postgres", "-c", full], check=True)
    else:
        subprocess.run(["bash", "-c", full], check=True)


def psql(database: str, *args: str, sql_file: Path | None = None, sql: str | None = None, quiet=True) -> str:
    if database not in DATABASES and database != "postgres":
        raise SystemExit("refusing an unknown database")
    cmd = ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", HOST, "-p", str(PORT), "-U", "postgres", "-d", database]
    if quiet:
        cmd.append("-q")
    if sql_file:
        cmd += ["-f", str(sql_file)]
    if sql:
        cmd += ["-c", sql]
    cmd += list(args)
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"psql failed ({sql_file.name if sql_file else 'inline'}): {done.stderr.strip()}")
    return done.stdout


def serial_files() -> list[Path]:
    return sorted(p for p in (ROOT / "supabase" / "sql").glob("[0-9][0-9]_*.sql")
                  if p.name[:2] not in ("98", "99") and not p.name.startswith("9"))


def up() -> None:
    if not BIN:
        raise SystemExit("PostgreSQL server binaries not found")
    down(quiet=True)
    BASE.mkdir(parents=True)
    if os.geteuid() == 0:
        shutil.chown(BASE, "postgres")
    _as_pg(f"initdb -D {DATA} -U postgres --auth=trust -E UTF8 >/dev/null")
    _as_pg(f"pg_ctl -D {DATA} -o '-p {PORT} -k {BASE} -c listen_addresses={HOST}' -l {BASE}/log.txt start >/dev/null")
    for _ in range(50):
        if subprocess.run(["pg_isready", "-h", HOST, "-p", str(PORT)], capture_output=True).returncode == 0:
            break
        time.sleep(0.2)
    for db in DATABASES:
        psql("postgres", sql=f"CREATE DATABASE {db}")
    # Two databases, like the v1 local layout: clinical (spps_dev_v2test) and identity (spps_dev_identity).
    # Both receive the full serial set; the app uses the clinical schema in one and the identity schema in the other.
    for db in ("spps_dev_v2test", "spps_dev_identity"):
        for f in serial_files():
            psql(db, sql_file=f)
    passwords = {r: secrets.token_urlsafe(18) for r in LOGIN_ROLES}
    for role, pw in passwords.items():
        psql("spps_dev_v2test", sql=f"ALTER ROLE {role} PASSWORD '{pw}'")
    env = {"SPPS_V2_PG_ADMIN_URL": f"postgresql+psycopg://postgres@{HOST}:{PORT}/spps_dev_v2test"}
    for role, pw in passwords.items():
        db = "spps_dev_identity" if role.startswith("spps_identity") else "spps_dev_v2test"
        env[f"SPPS_V2_PG_{role.upper()}_URL"] = f"postgresql+psycopg://{role}:{pw}@{HOST}:{PORT}/{db}"
    env["SPPS_V2_PG_IDENTITY_ADMIN_URL"] = f"postgresql+psycopg://postgres@{HOST}:{PORT}/spps_dev_identity"
    path = BASE / "env"
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    path.chmod(0o600)
    print(f"ready: {len(serial_files())} serial files applied to spps_dev_v2test; environment in {path}")


def down(quiet=False) -> None:
    if (DATA / "postmaster.pid").exists():
        try:
            _as_pg(f"pg_ctl -D {DATA} -m fast stop >/dev/null")
        except subprocess.CalledProcessError:
            pass  # server already gone (e.g. the workspace restarted); a stale pid file is removed with the folder
    if BASE.exists():
        shutil.rmtree(BASE)
    if not quiet:
        print("throwaway database stopped and deleted")


def env() -> None:
    print((BASE / "env").read_text(), end="")


if __name__ == "__main__":
    {"up": up, "down": down, "env": env}[sys.argv[1]]()
