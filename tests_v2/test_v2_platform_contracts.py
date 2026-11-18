"""Contracts that hold for every v2 phase: default-deny allowlist, flags off by default, preflight refusals,
migration file discipline and the v1 metadata shape."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
V2_PREFIXES = ("/api/public/form-token", "/api/public/practitioner-applications", "/api/admin/registrations",
               "/api/admin/email-deliveries", "/api/public/practitioner-application/promote",
               "/api/public/practitioner-application/photo", "/api/v2/", "/r/")


def _v2_routes(app):
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith(V2_PREFIXES):
            for method in sorted(getattr(route, "methods", []) or []):
                if method != "HEAD":
                    yield method, path


SAMPLES = {"client_draft_id": "draft-0001", "token": "a" * 43}


def _sample(path: str) -> str:
    return re.sub(r"\{([^}]+)\}", lambda m: SAMPLES.get(m.group(1), "1"), path)


def test_every_registered_v2_route_is_on_the_allowlist(app, monkeypatch):
    from server.v2 import flags
    from server.v2.allowlist import v2_allowed
    for name in flags.FLAGS:  # flag-gated routes are listed too; with their flag off they are refused
        monkeypatch.setenv(name, "true")
    routes = list(_v2_routes(app))
    assert routes, "no v2 routes registered"
    for method, path in routes:
        sample = _sample(path)
        if "{token}" in path:
            sample = path.replace("{token}", "a" * 43)
        assert v2_allowed(method, sample) is True, f"{method} {path} is not allowlisted"


def test_unknown_v2_paths_are_refused_by_default():
    from server.v2.allowlist import v2_allowed
    assert v2_allowed("GET", "/api/v2/anything-new") is False
    assert v2_allowed("DELETE", "/api/admin/registrations/summary") is False
    assert v2_allowed("GET", "/api/athletes") is None  # v1 rules decide


def test_every_client_facing_flag_defaults_to_off(monkeypatch):
    from server.v2 import flags
    for name in flags.FLAGS:
        monkeypatch.delenv(name, raising=False)
    assert not any(flags.snapshot().values())


def test_dependent_flags_cannot_be_enabled_alone(monkeypatch):
    from server.v2 import flags
    monkeypatch.setenv("SPPS_ATHLETE_LOOP_ENABLED", "true")
    monkeypatch.delenv("SPPS_PORTAL_ENABLED", raising=False)
    assert not flags.athlete_loop_enabled()


def _env(**extra):
    return {"SPPS_FORM_TOKEN_SECRET": "x" * 40, "SPPS_FIELD_KEYS": "k1:" + "A" * 43 + "=",
            "SPPS_FIELD_KEY_CURRENT": "k1", **extra}


def test_preflight_refuses_missing_secret_bad_flag_values_and_dependency_gaps():
    from server.v2.preflight import PreflightError, validate_env
    validate_env(_env())
    with pytest.raises(PreflightError, match="SPPS_FORM_TOKEN_SECRET"):
        validate_env({})
    with pytest.raises(PreflightError, match="SPPS_FIELD_KEYS"):
        validate_env({"SPPS_FORM_TOKEN_SECRET": "x" * 40})
    with pytest.raises(PreflightError, match="SPPS_FIELD_KEY_CURRENT"):
        validate_env(_env(SPPS_FIELD_KEY_CURRENT="k9"))
    with pytest.raises(PreflightError, match="true or false"):
        validate_env(_env(SPPS_DIRECTORY_ENABLED="yes"))
    with pytest.raises(PreflightError, match="SPPS_PORTAL_ENABLED"):
        validate_env(_env(SPPS_ATHLETE_LOOP_ENABLED="true"))


def test_serial_sql_files_match_their_migrations_and_were_never_rewritten():
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "v2_serial_sql.py"), "--check"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_migrations_md_lists_every_file_in_run_order():
    text = (ROOT / "MIGRATIONS.md").read_text(encoding="utf-8")
    serial = sorted(p.name for p in (ROOT / "supabase" / "sql").glob("[0-9][0-9]_*.sql"))
    positions = [text.find(name) for name in serial]
    assert all(p >= 0 for p in positions), [n for n, p in zip(serial, positions) if p < 0]
    assert positions == sorted(positions)
    for m in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        assert m.name in text, m.name


def test_v1_files_of_the_reference_were_not_edited():
    """Never edit an existing migration: v1 files must be byte-identical to the v1 ledger checksums."""
    import hashlib
    for serial in sorted((ROOT / "supabase" / "sql").glob("[0-3][0-9]_0*.sql")):
        m = re.search(r"VALUES \('([^']+)','([0-9a-f]{64})'\)", serial.read_text(encoding="utf-8"))
        if not m:
            continue
        source = ROOT / "migrations" / m.group(1)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == m.group(2), source.name


def test_v1_metadata_shape_is_unchanged_and_v2_tables_are_separate():
    from server.db import Base
    from server.v2.models import V2Base
    assert len(Base.metadata.tables) == 32
    assert not set(Base.metadata.tables) & set(V2Base.metadata.tables)
    for name in V2Base.metadata.tables:
        assert not name.startswith("spps_identity_dev"), "identity tables never go in the clinical models"


def test_no_env_files_or_secrets_in_the_tree():
    for p in ROOT.glob(".env*"):
        assert p.name.endswith(".example"), p.name


def test_flag_gated_routes_are_refused_while_their_flag_is_off(monkeypatch):
    from server.v2 import flags
    from server.v2.allowlist import ROUTES, v2_allowed
    for name in flags.FLAGS:
        monkeypatch.delenv(name, raising=False)
    gated = [(sorted(m)[0], p.pattern.strip("^$")) for m, p, f in ROUTES if f]
    assert gated
    for method, pattern in gated:
        sample = pattern.replace(r"\d+", "1").replace("(?:", "").replace(")", "").split("|")[0]
        assert v2_allowed(method, sample) is False, (method, sample)
