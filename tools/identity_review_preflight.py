"""Offline fail-closed URL guard for a future isolated identity review DB.

No network connections, SQL statements, environment secrets or Docker actions.
This guard is NOT a substitute for checking the actual Docker container ID,
RLS grants, rollback process or database owner before running migrations.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import URL, make_url

REVIEW_DATABASE = "spps_dev_identity"
REVIEW_HOSTS = frozenset({"127.0.0.1", "localhost"})
REVIEW_PORT = 55434
REVIEW_USER = "postgres"
REVIEW_SCHEMA = "spps_identity_dev"
COMPOSE_PROJECT = "spps-identity-review"


def require_review_admin_url(raw_url: str) -> URL:
    """Validate only the dedicated sandbox target; never connect to it."""
    try:
        target = make_url(raw_url)
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("Invalid isolated identity review URL") from None
    if (
        target.get_backend_name() != "postgresql"
        or target.host not in REVIEW_HOSTS
        or target.port != REVIEW_PORT
        or target.database != REVIEW_DATABASE
        or target.username != REVIEW_USER
        or not target.password
        or bool(target.query)
    ):
        raise RuntimeError("STOP: identity review requires a dedicated loopback sandbox target")
    return target


def check_review_compose(compose_path: Path) -> list[str]:
    """Check pinned independent project, host port and volume names offline.

    These checks deliberately recognize a conservative, pinned file format;
    they are NOT a YAML or Docker Compose deployment validator.
    """
    try:
        content = compose_path.read_text(encoding="utf-8")
    except OSError:
        return ["Isolated identity review Compose file not available"]
    required_lines = (
        f"name: {COMPOSE_PROJECT}",
        '      - "127.0.0.1:55434:5432"',
        f"      POSTGRES_DB: {REVIEW_DATABASE}",
        "      POSTGRES_USER: postgres",
        "      - identity_review_data:/var/lib/postgresql/data",
        "  identity_review_data:",
    )
    issues = [f"Missing sandbox invariant: {line.strip()}"
              for line in required_lines if line not in content.splitlines()]
    forbidden = ("spps-canonical-database-1", "55432:5432", "55433:5432", "spps_development:")
    for value in forbidden:
        if value in content:
            issues.append("STOP: Review Compose references a canonical database resource")
            break
    return issues


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    problems = check_review_compose(root / "compose.identity-review.yml")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("PASS: isolated identity Compose project, loopback port and volume are distinct")
    print("OFFLINE ONLY: no Docker containers started and no database/schema accessed")
    print("NOTE: RLS and migration validation have not yet been performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
