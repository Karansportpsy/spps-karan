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
DOCKER = os.environ.get("SPPS_V2_PG_DOCKER") == "1"
if DOCKER:
    sys.path.insert(0, str(ROOT))  # support both python -m tools.v2_pg_harness and direct script use
    from tools import ephemeral_db, ephemeral_guard
    BASE = ephemeral_db.STATE
    PORT = ephemeral_guard.PORT
    DATABASES = (ephemeral_guard.CLINICAL_DATABASE, ephemeral_guard.IDENTITY_DATABASE)


def docker_command(program: str, database: str, *args: str, source: str | None = None) -> str:
    """Execute only in the labelled, loopback-only tmpfs test container; never a protected cluster."""
    problems = ephemeral_guard.container_problems(ephemeral_guard.inspect_container())
    if problems or database not in (*DATABASES, "postgres"):
        raise RuntimeError("STOP: invalid disposable PostgreSQL target")
    cmd = ["docker", "exec", "-i", ephemeral_guard.CONTAINER, program, "-U", "postgres", "-d", database, *args]
    result = subprocess.run(cmd, input=source, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        # SQL errors can include a generated password; never echo a server reply here.
        raise RuntimeError(f"Disposable PostgreSQL {program} failed (exit {result.returncode})")
    return result.stdout


def _as_pg(cmd: str) -> None:
    full = f"export PATH={BIN}:$PATH; {cmd}"
    if os.geteuid() == 0:
        subprocess.run(["su", "postgres", "-c", full], check=True)
    else:
        subprocess.run(["bash", "-c", full], check=True)


def psql(database: str, *args: str, sql_file: Path | None = None, sql: str | None = None, quiet=True) -> str:
    if database not in DATABASES and database != "postgres":
        raise SystemExit("refusing an unknown database")
    if DOCKER:
        source = sql_file.read_text(encoding="utf-8") if sql_file else sql
        return docker_command("psql", database, "-X", "-v", "ON_ERROR_STOP=1", "-q", *args, source=source)
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
    if DOCKER:
        return up_docker()
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
    if DOCKER:
        ephemeral_db.down()
        return
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
    if DOCKER:
        raise SystemExit("Disposable credentials are not displayed; set SPPS_V2_PG_ENV to the harness v2.env file.")
    print((BASE / "env").read_text(), end="")


def up_docker() -> None:
    """Build the v2 serial schema through the sanctioned ephemeral lifecycle, including on Windows."""
    from dotenv import dotenv_values
    from sqlalchemy.engine import URL
    ephemeral_db.up_empty()
    password = dotenv_values(ephemeral_db.ADMIN_ENV)["SPPS_EPHEMERAL_PASSWORD"]
    clinical, identity = DATABASES
    psql("postgres", sql=f'CREATE DATABASE "{identity}"')
    for db in DATABASES:
        for f in serial_files():
            psql(db, sql_file=f)
    passwords = {r: secrets.token_urlsafe(24) for r in LOGIN_ROLES}
    for role, pw in passwords.items():
        psql(clinical, sql=f"ALTER ROLE {role} PASSWORD '{pw}'")

    def url(role, pw, db):
        return URL.create("postgresql+psycopg", username=role, password=pw, host=HOST,
                          port=PORT, database=db).render_as_string(hide_password=False)

    values = {"SPPS_V2_PG_ADMIN_URL": url("postgres", password, clinical),
              "SPPS_V2_PG_IDENTITY_ADMIN_URL": url("postgres", password, identity)}
    for role, pw in passwords.items():
        values[f"SPPS_V2_PG_{role.upper()}_URL"] = url(role, pw, identity if role.startswith("spps_identity") else clinical)
    ephemeral_db._write_secret_file(BASE / "v2.env", values)
    print(f"PASS: {len(serial_files())} serial files applied to each guarded disposable database; credentials not displayed")


if __name__ == "__main__":
    {"up": up, "down": down, "env": env}[sys.argv[1]]()
