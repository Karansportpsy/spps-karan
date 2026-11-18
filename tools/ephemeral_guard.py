"""Fail-closed identity checks for the disposable `spps-test-ephemeral` database.

Automated tests may only write to a PostgreSQL target that passes every check
here. The five protected local environments are refused unconditionally.

Local mode verifies the real Docker container (Compose project label, purpose
label, loopback port binding, tmpfs storage, no volume mounts, health).
CI mode is accepted ONLY inside GitHub Actions for this repository AND when the
workflow file itself declares SPPS_EPHEMERAL_CI_SERVICE=1 for a fresh service
container. Never set that variable locally.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url

# Slot "" (default): spps-test-ephemeral on 127.0.0.1:55436 (also the physical-validation backend's DB).
# Slot "2": an INDEPENDENT disposable cluster spps-test-ephemeral-2 on 127.0.0.1:55437, so DB-backed tests
# can run while the default slot holds validation records. Identical checks apply to both slots.
SLOT = os.environ.get("SPPS_EPHEMERAL_SLOT", "").strip()
if SLOT not in ("", "2"):
    raise RuntimeError("STOP: SPPS_EPHEMERAL_SLOT must be unset or 2")
PROJECT = "spps-test-ephemeral" + (f"-{SLOT}" if SLOT else "")
CONTAINER = f"{PROJECT}-database-1"
PORT = 55437 if SLOT else 55436
HOSTS = frozenset({"127.0.0.1", "localhost"})
DATA_PATH = "/var/lib/postgresql/data"
REPOSITORY = "karansportpsy-spec/spps-beta15-pilot"
CLINICAL_DATABASE = "spps_dev"
IDENTITY_DATABASE = "spps_dev_identity"
ALLOWED_DATABASES = frozenset({CLINICAL_DATABASE, IDENTITY_DATABASE})

# Owner-designated protected environments: never a test target.
PROTECTED_PORTS = frozenset({55432, 55433, 55434, 60528, 54322})
PROTECTED_CONTAINERS = (
    "spps-canonical-database-1",
    "spps-milestone-1-database-1",
    "spps-identity-review-database-1",
    "spps-m12-physical-20261110095258",
    "supabase_db_SPSP_MVP",
)


class EphemeralGuardError(RuntimeError):
    pass


def protected_live_ports() -> set[int]:
    """Host ports currently assigned to protected containers.

    spps-m12-physical-* publishes `127.0.0.1::5432` (Docker picks a new host
    port on every start), so the fixed PROTECTED_PORTS list is not enough.
    Reads both the configured bindings and the live NetworkSettings mapping.
    """
    ports: set[int] = set()
    for name in PROTECTED_CONTAINERS:
        try:
            raw = subprocess.check_output(["docker", "inspect", name], text=True,
                                          stderr=subprocess.DEVNULL, timeout=20)
            data = json.loads(raw)[0]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, ValueError, IndexError):
            continue
        maps = [(data.get("HostConfig", {}) or {}).get("PortBindings") or {},
                (data.get("NetworkSettings", {}) or {}).get("Ports") or {}]
        for mapping in maps:
            for bindings in mapping.values():
                for b in bindings or []:
                    if str(b.get("HostPort", "")).isdigit():
                        ports.add(int(b["HostPort"]))
    return ports


def in_ci_service() -> bool:
    return (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_REPOSITORY") == REPOSITORY
        and os.getenv("SPPS_EPHEMERAL_CI_SERVICE") == "1"
    )


def require_ephemeral_url(raw_url: str, *, database: str | None = None) -> URL:
    """Validate a URL points to the ephemeral target. Never connects."""
    try:
        target = make_url(raw_url)
    except Exception:  # noqa: BLE001 - any parse error is a refusal
        raise EphemeralGuardError("STOP: unparseable test database URL") from None
    if target.port in PROTECTED_PORTS:
        raise EphemeralGuardError("STOP: test target is a PROTECTED database port")
    if (
        target.get_backend_name() != "postgresql"
        or target.host not in HOSTS
        or target.port != PORT
        or target.database not in ALLOWED_DATABASES
        or (database is not None and target.database != database)
        or bool(target.query)
    ):
        raise EphemeralGuardError(
            f"STOP: automated tests require the loopback {PROJECT} target (127.0.0.1:{PORT})"
        )
    return target


@dataclass(frozen=True)
class ContainerFacts:
    project: str | None
    purpose: str | None
    running: bool
    health: str | None
    bindings: list
    mounts: list
    tmpfs: dict


def inspect_container(name: str = CONTAINER) -> ContainerFacts | None:
    try:
        raw = subprocess.check_output(
            ["docker", "inspect", name], text=True, stderr=subprocess.DEVNULL, timeout=20
        )
        data = json.loads(raw)[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, ValueError, IndexError):
        return None
    labels = data.get("Config", {}).get("Labels", {}) or {}
    host = data.get("HostConfig", {}) or {}
    return ContainerFacts(
        project=labels.get("com.docker.compose.project"),
        purpose=labels.get("org.spps.purpose"),
        running=data.get("State", {}).get("Running") is True,
        health=(data.get("State", {}).get("Health") or {}).get("Status"),
        bindings=(host.get("PortBindings", {}) or {}).get("5432/tcp") or [],
        mounts=data.get("Mounts", []) or [],
        tmpfs=host.get("Tmpfs", {}) or {},
    )


def container_problems(facts: ContainerFacts | None) -> list[str]:
    if facts is None:
        return ["ephemeral container not found"]
    problems = []
    if facts.project != PROJECT:
        problems.append("wrong Compose project label")
    if facts.purpose != "ephemeral-test-only":
        problems.append("missing ephemeral purpose label")
    if not facts.running or facts.health != "healthy":
        problems.append("container not running/healthy")
    if not (len(facts.bindings) == 1 and facts.bindings[0].get("HostIp") == "127.0.0.1"
            and facts.bindings[0].get("HostPort") == str(PORT)):
        problems.append(f"port binding is not exactly 127.0.0.1:{PORT}")
    if facts.mounts:
        problems.append("container has volume/bind mounts; storage must be tmpfs only")
    if DATA_PATH not in facts.tmpfs:
        problems.append("data directory is not tmpfs")
    return problems


def verify_ephemeral_target(raw_url: str, *, database: str | None = None) -> URL:
    """URL check + live container identity check (or approved CI service)."""
    target = require_ephemeral_url(raw_url, database=database)
    if in_ci_service():
        return target
    if target.port in protected_live_ports():
        raise EphemeralGuardError("STOP: test port is currently published by a PROTECTED container")
    problems = container_problems(inspect_container())
    if problems:
        raise EphemeralGuardError("STOP: ephemeral container verification failed: " + "; ".join(problems))
    return target
