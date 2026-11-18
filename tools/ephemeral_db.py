"""Lifecycle for the disposable `spps-test-ephemeral` PostgreSQL (127.0.0.1:55436).

    python -m tools.ephemeral_db up       # fresh tmpfs cluster + clinical + identity fixtures
    python -m tools.ephemeral_db up-empty # fresh tmpfs cluster, TLS on, NO roles/fixtures (hosted-profile tests)
    python -m tools.ephemeral_db down     # discard it (only after verifying it is ours)
    python -m tools.ephemeral_db status

All generated secrets live in ignored output/test-ephemeral/. Every action
re-verifies the container identity; protected environments are never touched.
In GitHub Actions the workflow supplies a fresh service container instead
(SPPS_EPHEMERAL_CI_SERVICE=1, declared in the workflow file).
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from tools import ephemeral_guard as guard

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.test-ephemeral.yml"
STATE = ROOT / "output" / ("test-ephemeral" + (f"-{guard.SLOT}" if guard.SLOT else ""))
ADMIN_ENV = STATE / "admin.env"
INTEGRATION_ENV = STATE / "integration.env"


def _compose(*args: str, password: str = "unused") -> None:
    env = {**os.environ, "SPPS_EPHEMERAL_PASSWORD": password, "SPPS_EPHEMERAL_PORT": str(guard.PORT)}
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "-p", guard.PROJECT, *args],
                   check=True, env=env, cwd=ROOT, timeout=300)


def _existing_is_ours() -> bool | None:
    facts = guard.inspect_container()
    if facts is None:
        return None
    return facts.project == guard.PROJECT and facts.purpose == "ephemeral-test-only"


def admin_url(database: str, password: str) -> URL:
    return URL.create("postgresql+psycopg", username="postgres", password=password,
                      host="127.0.0.1", port=guard.PORT, database=database)


def _write_secret_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("".join(f"{k}={v}\n" for k, v in values.items()))


def validation_backend_listening() -> bool:
    """True while the physical-validation API (tools.device_backend, 127.0.0.1:8010) is serving."""
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 8010)) == 0


def down() -> None:
    if guard.in_ci_service():
        return
    if not guard.SLOT and validation_backend_listening():
        raise RuntimeError("STOP: the physical-validation API on 127.0.0.1:8010 uses the default disposable "
                           "database; refusing to discard it (use SPPS_EPHEMERAL_SLOT=2 for tests)")
    ours = _existing_is_ours()
    if ours is False:
        raise RuntimeError("STOP: a container named like the ephemeral DB is not ours; refusing")
    if ours:
        # No -v: tmpfs storage has no volumes; never remove volumes by command.
        _compose("down", "--remove-orphans")
    if STATE.exists():
        shutil.rmtree(STATE)


def enable_tls(conn) -> None:
    """Turn on server TLS with the image's snakeoil certificate (tests the hosted sslmode=require path)."""
    conn.execute(text("ALTER SYSTEM SET ssl_cert_file='/etc/ssl/certs/ssl-cert-snakeoil.pem'"))
    conn.execute(text("ALTER SYSTEM SET ssl_key_file='/etc/ssl/private/ssl-cert-snakeoil.key'"))
    conn.execute(text("ALTER SYSTEM SET ssl='on'"))
    conn.execute(text("SELECT pg_reload_conf()"))


def up_empty() -> dict[str, str]:
    """Fresh cluster for hosted-profile provisioning tests: TLS enabled, no SPPS roles or schemas."""
    if not guard.in_ci_service():
        down()
        password = secrets.token_urlsafe(24)
        _compose("up", "-d", "--wait", password=password)
    else:
        password = os.environ.get("SPPS_EPHEMERAL_PASSWORD", "")
        if not password:
            raise RuntimeError("STOP: CI service password missing")
        if STATE.exists():
            shutil.rmtree(STATE)
    _write_secret_file(ADMIN_ENV, {"SPPS_EPHEMERAL_PASSWORD": password})
    clinical = guard.verify_ephemeral_target(
        admin_url(guard.CLINICAL_DATABASE, password).render_as_string(hide_password=False),
        database=guard.CLINICAL_DATABASE)
    engine = create_engine(clinical, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT inet_server_port()")) != 5432:
                raise RuntimeError("STOP: unexpected server port inside container")
            if conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'spps!_%' ESCAPE '!'")):
                raise RuntimeError("STOP: cluster is not empty (SPPS roles exist); hosted tests need a fresh cluster")
            enable_tls(conn)
    finally:
        engine.dispose()
    return {"admin_env": str(ADMIN_ENV)}


def up() -> dict[str, str]:
    if not guard.in_ci_service():
        down()
        password = secrets.token_urlsafe(24)
        _compose("up", "-d", "--wait", password=password)
    else:
        password = os.environ.get("SPPS_EPHEMERAL_PASSWORD", "")
        if not password:
            raise RuntimeError("STOP: CI service password missing")
        if STATE.exists():
            shutil.rmtree(STATE)
    _write_secret_file(ADMIN_ENV, {"SPPS_EPHEMERAL_PASSWORD": password})
    clinical = guard.verify_ephemeral_target(
        admin_url(guard.CLINICAL_DATABASE, password).render_as_string(hide_password=False),
        database=guard.CLINICAL_DATABASE)
    engine = create_engine(clinical, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT inet_server_port()")) != 5432:
                raise RuntimeError("STOP: unexpected server port inside container")
            exists = conn.scalar(text("SELECT count(*) FROM pg_database WHERE datname=:d"),
                                 {"d": guard.IDENTITY_DATABASE})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{guard.IDENTITY_DATABASE}"'))
    finally:
        engine.dispose()
    # Clinical fixture (33 RLS tables, two fictional practitioners).
    from tools.development_db import provision
    values = provision(clinical.render_as_string(hide_password=False))
    # Identity fixture (FORCE RLS tables + auth functions) via the ephemeral installer target.
    os.environ["SPPS_EPHEMERAL_PASSWORD"] = password
    from dotenv import dotenv_values
    from tools.identity_review_db import AUTH, EPHEMERAL_TARGET, PORTAL, install
    install(EPHEMERAL_TARGET)
    identity = dotenv_values(EPHEMERAL_TARGET.access_file)

    def identity_url(role, key):
        return URL.create("postgresql+psycopg", username=role, password=identity[key], host="127.0.0.1",
                          port=guard.PORT, database=guard.IDENTITY_DATABASE).render_as_string(hide_password=False)

    values.update({
        "SPPS_PORTAL_ENABLED": "true",
        "SPPS_IDENTITY_AUTH_DATABASE_URL": identity_url(AUTH, "SPPS_IDENTITY_AUTH_PASSWORD"),
        "SPPS_IDENTITY_PORTAL_DATABASE_URL": identity_url(PORTAL, "SPPS_IDENTITY_PORTAL_PASSWORD"),
        "SPPS_PORTAL_DEV_OUTBOX": str(STATE / "portal-outbox.jsonl"),
        "SPPS_PAYMENT_PROVIDER": "sandbox",
        "SPPS_SANDBOX_PAYMENT_SECRET": secrets.token_urlsafe(32),
    })
    _write_secret_file(INTEGRATION_ENV, values)
    return {"integration_env": str(INTEGRATION_ENV),
            "identity_access": str(EPHEMERAL_TARGET.access_file)}


def status() -> int:
    facts = guard.inspect_container()
    problems = guard.container_problems(facts)
    print("ephemeral container:", "absent" if facts is None else ("OK" if not problems else "; ".join(problems)))
    return 0 if not problems else 1


def main(argv: list[str]) -> int:
    if argv == ["up"]:
        paths = up()
        print(f"PASS: disposable {guard.PROJECT} ready on 127.0.0.1:{guard.PORT} (fictional fixtures only)")
        for key, value in paths.items():
            print(f"{key}: {value}")
        return 0
    if argv == ["up-empty"]:
        paths = up_empty()
        print(f"PASS: empty TLS-enabled {guard.PROJECT} ready on 127.0.0.1:{guard.PORT}")
        return 0
    if argv == ["down"]:
        down()
        print("Ephemeral test database discarded.")
        return 0
    if argv == ["status"]:
        return status()
    print(__doc__)
    return 64


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"EPHEMERAL STOP: {exc}")
