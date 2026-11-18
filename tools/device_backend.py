"""Backend for PHYSICAL Android validation of the RC build (fictional data only).

    python -m tools.device_backend                    # fresh disposable DB + API on 127.0.0.1:8010 + adb reverse
    python -m tools.device_backend --restart-api      # restart ONLY the API; the database is kept and verified
    python -m tools.device_backend --restart-api --turn   # same, with the TURN test bench (relay-only video)
    python -m tools.device_backend --stop             # stop API (+ TURN bench) and discard the disposable DB

* Starting fresh REPLACES the disposable database. It is refused while a disposable database is
  running unless `--fresh-database` is given, so physical-validation records are never lost by accident.
* `--restart-api` never calls ephemeral_db.up()/down(): it snapshots the container id/start time
  and per-table row counts (read-only transaction, counts only), restarts uvicorn with the same
  environment, and proves the snapshot is identical afterwards.

* Database: the verified `spps-test-ephemeral` cluster (tmpfs, 55436) — never a protected one.
* API: uvicorn on 127.0.0.1:8010 (not 8000, so an existing dev server is never disturbed),
  integration profile + portal + sandbox payments; WebRTC signaling for platform video.
* Phone: `adb reverse tcp:8010 tcp:8010` so the RC app's http://localhost:8010 reaches this
  PC over USB. Nothing is installed or removed on the phone by this tool.
* Fictional credentials are written to output/device-validation/credentials.txt (ignored).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "device-validation"
PORT = 8010
# Overridable with --validation-root (the checkout that owns the RUNNING validation database files)
INTEGRATION_ENV = ROOT / "output" / "test-ephemeral" / "integration.env"
ADMIN_ENV = ROOT / "output" / "test-ephemeral" / "admin.env"


def use_validation_root(root: Path) -> None:
    global STATE, INTEGRATION_ENV, ADMIN_ENV
    root = Path(root).resolve()
    STATE = root / "output" / "device-validation"
    INTEGRATION_ENV = root / "output" / "test-ephemeral" / "integration.env"
    ADMIN_ENV = root / "output" / "test-ephemeral" / "admin.env"


def code_commit(code_root: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=code_root, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=code_root,
                           capture_output=True, text=True).stdout.strip()
    return (r.stdout.strip() or "unknown") + ("+uncommitted" if dirty else "")


def adb() -> str:
    found = shutil.which("adb") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe")
    if not Path(found).exists() and not shutil.which("adb"):
        raise SystemExit("adb not found (Android SDK platform-tools)")
    return found


def port_free(port: int) -> bool:
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


ICE_POLICY = "relay"


def _turn_env(turn: bool) -> dict[str, str]:
    if not turn:
        return {}
    from tools import turn_bench
    if turn_bench.problems(turn_bench.inspect()):
        turn_bench.up()
    return turn_bench.backend_env(ICE_POLICY)


def _reverse(port: int) -> str:
    r = subprocess.run([adb(), "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True, text=True)
    return "OK" if r.returncode == 0 else "FAILED " + r.stderr.strip()


def _launch(env_values: dict, extra: dict[str, str], code_root: Path = ROOT) -> subprocess.Popen:
    if not (Path(code_root) / "server" / "main.py").is_file():
        raise SystemExit(f"STOP: {code_root} is not an SPPS checkout")
    STATE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **env_values, "SPPS_PUBLIC_BASE_URL": f"http://localhost:{PORT}", **extra}
    log = (STATE / "backend.log").open("a", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
                             "--port", str(PORT), "--no-access-log"], cwd=code_root, env=env, stdout=log, stderr=log)
    (STATE / "backend.pid").write_text(str(proc.pid), encoding="utf-8")
    (STATE / "backend-code.txt").write_text(f"{Path(code_root).resolve()}\n{code_commit(Path(code_root))}\n", encoding="utf-8")
    for _ in range(150):
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health/ready", timeout=1).status_code == 200:
                return proc
        except httpx.TransportError:
            pass
        if proc.poll() is not None:
            raise SystemExit("Backend exited; see output/device-validation/backend.log")
        time.sleep(.2)
    raise SystemExit("Backend did not become ready; see output/device-validation/backend.log")


def database_snapshot() -> dict:
    """Container identity + per-table row counts of both disposable databases (read-only; counts only)."""
    from sqlalchemy import create_engine, text
    from tools import ephemeral_db
    from tools import ephemeral_guard as guard
    facts = guard.inspect_container()
    issues = guard.container_problems(facts)
    if issues:
        raise SystemExit("STOP: disposable database not verified: " + "; ".join(issues))
    raw = json.loads(subprocess.check_output(["docker", "inspect", guard.CONTAINER], text=True, timeout=20))[0]
    password = dotenv_values(ADMIN_ENV).get("SPPS_EPHEMERAL_PASSWORD", "")
    if not password:
        raise SystemExit("STOP: disposable database credentials missing; nothing restarted")
    counts = {}
    for database, schema in ((guard.CLINICAL_DATABASE, "spps_dev"), (guard.IDENTITY_DATABASE, "spps_identity_dev")):
        url = guard.verify_ephemeral_target(
            ephemeral_db.admin_url(database, password).render_as_string(hide_password=False), database=database)
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                tables = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema=:s "
                                           "AND table_type='BASE TABLE' ORDER BY 1"), {"s": schema}).scalars().all()
                for table in tables:
                    counts[f"{schema}.{table}"] = conn.scalar(text(f'SELECT count(*) FROM "{schema}"."{table}"'))
        finally:
            engine.dispose()
    return {"container_id": raw["Id"], "started_at": raw["State"]["StartedAt"], "counts": counts}


def backup() -> Path:
    """Logical backup of the whole disposable cluster (roles + both databases) to an ignored private file.
    pg_dumpall only reads. Restore (manual, owner decision): psql -U postgres -f <file> on a NEW disposable cluster."""
    import hashlib
    from datetime import datetime, timezone
    from tools import ephemeral_guard as guard
    issues = guard.container_problems(guard.inspect_container())
    if issues:
        raise SystemExit("STOP: disposable database not verified: " + "; ".join(issues))
    target = STATE / "backups"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"validation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sql"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        r = subprocess.run(["docker", "exec", guard.CONTAINER, "pg_dumpall", "-U", "postgres", "--no-password"],
                           stdout=out, stderr=subprocess.PIPE, timeout=300)
    if r.returncode != 0 or path.stat().st_size < 1000:
        raise SystemExit("STOP: backup failed; nothing restarted (" + r.stderr.decode(errors="replace")[-300:] + ")")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (path.with_suffix(".sha256")).write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"Backup: {path} ({path.stat().st_size} bytes, sha256 {digest[:16]}…)")
    return path


def restart_api(turn: bool, code_root: Path = ROOT) -> None:
    pid_file = STATE / "backend.pid"
    if not INTEGRATION_ENV.exists():
        raise SystemExit("STOP: no integration.env for the running disposable database; nothing restarted")
    if not (Path(code_root) / "server" / "main.py").is_file():
        raise SystemExit(f"STOP: {code_root} is not an SPPS checkout; nothing restarted")
    before = database_snapshot()
    backup()
    print(f"Previous API code: {(STATE / 'backend-code.txt').read_text(encoding='utf-8').split()[-1] if (STATE / 'backend-code.txt').exists() else 'unrecorded'}"
          f" -> new: {Path(code_root).resolve()} @ {code_commit(Path(code_root))}")
    extra = _turn_env(turn)
    if pid_file.exists():
        pid = int(pid_file.read_text())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"] if os.name == "nt" else ["kill", str(pid)],
                       capture_output=True)
    for _ in range(100):
        if port_free(PORT):
            break
        time.sleep(.1)
    else:
        raise SystemExit(f"STOP: port {PORT} still in use by another process; database untouched")
    proc = _launch(dotenv_values(INTEGRATION_ENV), extra, code_root)
    after = database_snapshot()
    same_container = (before["container_id"], before["started_at"]) == (after["container_id"], after["started_at"])
    changed = sorted(t for t in set(before["counts"]) | set(after["counts"])
                     if before["counts"].get(t) != after["counts"].get(t))
    print(f"PASS: API restarted on http://127.0.0.1:{PORT} (pid {proc.pid}); adb reverse {PORT}: {_reverse(PORT)}"
          + (f"; adb reverse 3478: {_reverse(3478)}; TURN bench, ICE policy {ICE_POLICY}" if turn else ""))
    print(f"Database container {'UNCHANGED' if same_container else 'CHANGED'} (id and start time); "
          f"tables={len(after['counts'])}, rows before={sum(before['counts'].values())}, "
          f"after={sum(after['counts'].values())}")
    if changed:  # names only; e.g. session housekeeping or the phone used the app meanwhile
        print("Row counts changed in:", ", ".join(changed))
    if not same_container:
        raise SystemExit("FAIL: the disposable database container changed during the API restart")


def start(fresh: bool, turn: bool) -> None:
    if not port_free(PORT):
        raise SystemExit(f"STOP: port {PORT} already in use; use --restart-api to keep the database, or --stop")
    from tools import ephemeral_db
    from tools import ephemeral_guard as guard
    if guard.inspect_container() is not None and not fresh:
        raise SystemExit("STOP: a disposable database already exists and may hold physical-validation records. "
                         "Use --restart-api to keep it, or --fresh-database to replace it deliberately.")
    use_validation_root(ROOT)  # a fresh start always owns its own files
    paths = ephemeral_db.up()
    env_values = dotenv_values(paths["integration_env"])
    (STATE / "backend.log").unlink(missing_ok=True)
    proc = _launch(env_values, _turn_env(turn))
    reverse = subprocess.run([adb(), "reverse", f"tcp:{PORT}", f"tcp:{PORT}"], capture_output=True, text=True)
    if turn:
        print("adb reverse 3478 (TURN):", _reverse(3478))
    creds = (
        "SPPS RC physical validation — FICTIONAL accounts on a disposable database (destroyed by --stop)\n"
        f"Practitioner (clinical): alice@spps.test / {env_values['SPPS_DEV_ALICE_PASSWORD']}\n"
        f"Practitioner (clinical): bob@spps.test / {env_values['SPPS_DEV_BOB_PASSWORD']}\n"
        f"Administrator (reviews): carol@spps.test / {env_values['SPPS_DEV_CAROL_PASSWORD']}\n"
        "Athlete/guardian: register in the app (/portal); verification codes appear in\n"
        f"  {env_values['SPPS_PORTAL_DEV_OUTBOX']}\n"
        f"Reviewer role for a portal account: python -m tools.identity_ops grant-reviewer --email <email>\n"
    )
    (STATE / "credentials.txt").write_text(creds, encoding="utf-8")
    print(f"PASS: device backend on http://127.0.0.1:{PORT} (pid {proc.pid}); adb reverse: "
          f"{'OK' if reverse.returncode == 0 else 'FAILED ' + reverse.stderr.strip()}")
    print(f"Fictional credentials: {STATE / 'credentials.txt'}")


def stop() -> None:
    pid_file = STATE / "backend.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text())
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            subprocess.run(["kill", str(pid)], capture_output=True)
        pid_file.unlink()
    subprocess.run([adb(), "reverse", "--remove", f"tcp:{PORT}"], capture_output=True)
    from tools import turn_bench
    if turn_bench.inspect() is not None:
        subprocess.run([adb(), "reverse", "--remove", "tcp:3478"], capture_output=True)
        turn_bench.down()
    from tools import ephemeral_db
    ephemeral_db.down()
    print("Device backend stopped; disposable database discarded.")


if __name__ == "__main__":
    if os.environ.get("SPPS_EPHEMERAL_SLOT"):
        raise SystemExit("STOP: the device backend only uses the default disposable slot; unset SPPS_EPHEMERAL_SLOT")
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--restart-api", action="store_true", help="restart only the API; keep and verify the database")
    mode.add_argument("--snapshot", action="store_true", help="read-only database identity + row counts")
    mode.add_argument("--backup", action="store_true", help="read-only pg_dumpall of the disposable cluster")
    ap.add_argument("--code-root", default=str(ROOT), help="checkout whose server/web code the API runs (rollback: the old one)")
    ap.add_argument("--validation-root", default=str(ROOT), help="checkout holding the running validation DB files")
    ap.add_argument("--fresh-database", action="store_true", help="allow start to REPLACE an existing disposable DB")
    ap.add_argument("--turn", action="store_true", help="use the TURN test bench for video")
    ap.add_argument("--ice-policy", choices=["relay", "all"], default="relay", help="with --turn: relay-only or all")
    args = ap.parse_args()
    use_validation_root(Path(args.validation_root))
    ICE_POLICY = args.ice_policy
    if args.snapshot:
        snap = database_snapshot()
        print(f"container {snap['container_id'][:12]} started {snap['started_at']}; tables={len(snap['counts'])}; "
              f"rows={sum(snap['counts'].values())}")
        for table, count in sorted(snap["counts"].items()):
            print(f"  {table}: {count}")
    elif args.backup:
        backup()
    elif args.stop:
        stop()
    elif args.restart_api:
        restart_api(args.turn, Path(args.code_root))
    else:
        start(args.fresh_database, args.turn)
