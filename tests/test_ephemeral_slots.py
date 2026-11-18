"""Independent disposable slot 2 (55437) and protection of the validation database (default slot)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def guard_facts(slot):
    env = {k: v for k, v in os.environ.items() if k != "SPPS_EPHEMERAL_SLOT"}
    if slot is not None:
        env["SPPS_EPHEMERAL_SLOT"] = slot
    code = ("from tools import ephemeral_guard as g, ephemeral_db as d;"
            "print(g.PROJECT, g.CONTAINER, g.PORT, d.STATE.name, sep='|')")
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)


def test_default_slot_is_unchanged():
    assert guard_facts(None).stdout.strip() == "spps-test-ephemeral|spps-test-ephemeral-database-1|55436|test-ephemeral"


def test_slot_two_is_fully_separate():
    out = guard_facts("2").stdout.strip()
    assert out == "spps-test-ephemeral-2|spps-test-ephemeral-2-database-1|55437|test-ephemeral-2"


def test_unknown_slot_refuses():
    r = guard_facts("55432")
    assert r.returncode != 0 and "SPPS_EPHEMERAL_SLOT" in r.stderr


def test_slot_two_still_refuses_protected_and_other_ports(monkeypatch):
    from tools import ephemeral_guard as guard
    monkeypatch.setattr(guard, "PORT", 55437)
    for port in (55432, 55433, 55434, 60528, 54322, 55436):
        with pytest.raises(guard.EphemeralGuardError):
            guard.require_ephemeral_url(f"postgresql+psycopg://u:p@127.0.0.1:{port}/spps_dev")
    assert guard.require_ephemeral_url("postgresql+psycopg://u:p@127.0.0.1:55437/spps_dev").port == 55437


def test_default_slot_is_never_discarded_while_validation_api_serves(monkeypatch):
    from tools import ephemeral_db, ephemeral_guard as guard
    monkeypatch.setattr(guard, "SLOT", "")
    monkeypatch.setattr(guard, "in_ci_service", lambda: False)
    monkeypatch.setattr(ephemeral_db, "validation_backend_listening", lambda: True)
    monkeypatch.setattr(ephemeral_db, "_compose", lambda *a, **k: pytest.fail("must not run compose"))
    with pytest.raises(RuntimeError, match="8010"):
        ephemeral_db.down()
    with pytest.raises(RuntimeError, match="8010"):
        ephemeral_db.up()  # up() discards first, so it is refused too
