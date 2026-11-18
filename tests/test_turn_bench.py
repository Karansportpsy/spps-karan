"""SPPS-VIDEO-001 TURN bench + device backend safety (no Docker, no database, no network)."""
from pathlib import Path

import pytest
import yaml

from tools import device_backend, turn_bench
from tools.turn_bench import Facts

ROOT = Path(__file__).resolve().parents[1]


def facts(**kw):
    base = dict(project="spps-test-turn", purpose="turn-test-bench-only", running=True,
                bindings={"3478/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3478"}]}, mounts=[],
                image="coturn/coturn:4.6.3")
    return Facts(**{**base, **kw})


def test_verified_bench_has_no_problems():
    assert turn_bench.problems(facts()) == []


@pytest.mark.parametrize("bad", [
    {"project": "spps-canonical"}, {"purpose": None}, {"running": False},
    {"bindings": {"3478/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3478"}]}},
    {"bindings": {"3478/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3478"}], "3478/udp": []}},
    {"bindings": {"3478/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3478"}],
                  "49160/udp": [{"HostIp": "127.0.0.1", "HostPort": "49160"}]}},
    {"mounts": [{"Type": "volume"}]},
])
def test_any_deviation_is_reported(bad):
    assert turn_bench.problems(facts(**bad))


def test_compose_is_loopback_tcp_only_relay_to_self_and_secret_required():
    doc = yaml.safe_load((ROOT / "compose.test-turn.yml").read_text(encoding="utf-8"))
    assert doc["name"] == "spps-test-turn"
    svc = doc["services"]["turn"]
    assert svc["ports"] == ["127.0.0.1:3478:3478/tcp"]
    assert "volumes" not in svc and svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert svc["cap_add"] == ["NET_BIND_SERVICE"]
    assert svc["labels"]["org.spps.purpose"] == "turn-test-bench-only"
    cmd = svc["command"]
    relay = svc["networks"]["turnnet"]["ipv4_address"]
    assert f"--relay-ip={relay}" in cmd and f"--allowed-peer-ip={relay}" in cmd
    assert "--denied-peer-ip=0.0.0.0-255.255.255.255" in cmd
    assert {"--no-udp", "--no-tls", "--no-dtls", "--use-auth-secret", "--no-cli"} <= set(cmd)
    secret = [c for c in cmd if c.startswith("--static-auth-secret=")]
    assert secret == ["--static-auth-secret=${SPPS_TURN_SECRET:?Generated per run by tools.turn_bench}"]
    assert svc["image"].count(":") == 1 and not svc["image"].endswith(":latest")


def test_secret_files_are_git_ignored():
    import subprocess, pytest
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("not a git work tree (run `git init` to enable this check)")
    for rel in ("output/device-validation/turn.env", "output/device-validation/backend.pid"):
        assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT).returncode == 0, rel


def test_start_refuses_to_replace_a_running_disposable_database(monkeypatch):
    from tools import ephemeral_db, ephemeral_guard
    called = []
    monkeypatch.setattr(device_backend, "port_free", lambda port: True)
    monkeypatch.setattr(ephemeral_guard, "inspect_container", lambda *a, **k: object())
    monkeypatch.setattr(ephemeral_db, "up", lambda: called.append("up"))
    with pytest.raises(SystemExit, match="--restart-api"):
        device_backend.start(fresh=False, turn=False)
    assert called == []  # the database was never touched


def test_restart_api_stops_before_touching_anything_without_env(monkeypatch, tmp_path):
    monkeypatch.setattr(device_backend, "INTEGRATION_ENV", tmp_path / "missing.env")
    monkeypatch.setattr(device_backend, "database_snapshot", lambda: pytest.fail("must not snapshot"))
    with pytest.raises(SystemExit, match="nothing restarted"):
        device_backend.restart_api(turn=False)


def test_restart_api_refuses_a_non_checkout_code_root(monkeypatch, tmp_path):
    env = tmp_path / "integration.env"
    env.write_text("X=1\n", encoding="utf-8")
    monkeypatch.setattr(device_backend, "INTEGRATION_ENV", env)
    monkeypatch.setattr(device_backend, "database_snapshot", lambda: pytest.fail("must not snapshot"))
    with pytest.raises(SystemExit, match="not an SPPS checkout"):
        device_backend.restart_api(turn=False, code_root=tmp_path)


def test_restart_path_never_calls_database_lifecycle():
    import ast
    import inspect
    for fn in (device_backend.restart_api, device_backend.database_snapshot, device_backend.backup):
        calls = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(ast.parse(inspect.getsource(fn).strip()))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
        assert not calls & {"ephemeral_db.up", "ephemeral_db.down", "turn_bench.down"}, (fn.__name__, calls)
