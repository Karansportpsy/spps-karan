"""Stage 13A: offline checks only; no PostgreSQL or Docker side effects."""
from pathlib import Path

import pytest

from tools.identity_review_preflight import (
    COMPOSE_PROJECT, REVIEW_DATABASE, REVIEW_PORT,
    check_review_compose, require_review_admin_url,
)

ROOT = Path(__file__).resolve().parents[1]


def test_dedicated_identity_review_compose_is_independent():
    assert not check_review_compose(ROOT / "compose.identity-review.yml")
    assert COMPOSE_PROJECT == "spps-identity-review"
    assert REVIEW_DATABASE == "spps_dev_identity"
    assert REVIEW_PORT == 55434


def test_review_target_accepts_only_its_separate_local_database():
    url = require_review_admin_url(
        "postgresql+psycopg://postgres:review-only@127.0.0.1:55434/spps_dev_identity"
    )
    assert url.database == REVIEW_DATABASE
    assert url.port == REVIEW_PORT


@pytest.mark.parametrize("unsafe", [
    "postgresql+psycopg://postgres:test@127.0.0.1:55432/spps_dev",
    "postgresql+psycopg://postgres:test@127.0.0.1:55433/spps_dev_identity",
    "postgresql+psycopg://postgres:test@127.0.0.1:55434/spps_dev",
    "postgresql+psycopg://postgres:test@db.example.com:55434/spps_dev_identity",
    "postgresql+psycopg://postgres:test@127.0.0.1:5432/spps_dev_identity",
    "postgresql+psycopg://spps_dev_app:test@127.0.0.1:55434/spps_dev_identity",
    "postgresql+psycopg://postgres:test@127.0.0.1:55434/spps_dev_identity?options=-csearch_path=public",
    "sqlite:///./spps_dev_identity.db",
    "postgresql+psycopg://postgres@127.0.0.1:55434/spps_dev_identity",
])
def test_review_target_rejects_canonical_remote_or_ambiguous_urls(unsafe):
    with pytest.raises(RuntimeError, match="identity review requires"):
        require_review_admin_url(unsafe)


def test_no_database_commands_hidden_in_preflight():
    source = (ROOT / "tools/identity_review_preflight.py").read_text(encoding="utf-8")
    assert "create_engine(" not in source
    assert "connect(" not in source
    assert "subprocess" not in source
    assert "os.system" not in source


def test_sandbox_compose_never_uses_existing_database_volume():
    compose = (ROOT / "compose.identity-review.yml").read_text(encoding="utf-8")
    assert "spps_dev_identity" in compose
    assert "spps_development" not in compose
    assert "55432" not in compose
    assert "55433" not in compose
    assert "spps-canonical-database-1" not in compose
