"""M0: automated tests may only write to the disposable spps-test-ephemeral database."""
import pytest

from tools import ephemeral_guard as guard
from tools.ephemeral_guard import ContainerFacts, EphemeralGuardError


@pytest.mark.parametrize("port", sorted(guard.PROTECTED_PORTS))
def test_every_protected_port_is_refused(port):
    with pytest.raises(EphemeralGuardError, match="PROTECTED"):
        guard.require_ephemeral_url(f"postgresql+psycopg://u:p@127.0.0.1:{port}/spps_dev")


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:p@db.example.com:55436/spps_dev",
    "postgresql+psycopg://u:p@127.0.0.1:5432/spps_dev",
    "postgresql+psycopg://u:p@127.0.0.1:55436/postgres",
    "postgresql+psycopg://u:p@127.0.0.1:55436/spps_dev?options=-csearch_path%3Dpublic",
    "sqlite:///spps_dev.db",
    "not a url",
])
def test_non_ephemeral_targets_are_refused(url):
    with pytest.raises(EphemeralGuardError):
        guard.require_ephemeral_url(url)


def test_ephemeral_url_accepted_and_database_pinned():
    url = "postgresql+psycopg://u:p@127.0.0.1:55436/spps_dev"
    assert guard.require_ephemeral_url(url).port == 55436
    with pytest.raises(EphemeralGuardError):
        guard.require_ephemeral_url(url, database="spps_dev_identity")


def good_facts(**overrides):
    base = dict(project="spps-test-ephemeral", purpose="ephemeral-test-only", running=True,
                health="healthy", bindings=[{"HostIp": "127.0.0.1", "HostPort": "55436"}],
                mounts=[], tmpfs={"/var/lib/postgresql/data": "rw"})
    base.update(overrides)
    return ContainerFacts(**base)


def test_container_checks_accept_only_tmpfs_loopback_labelled_container():
    assert guard.container_problems(good_facts()) == []
    assert guard.container_problems(None)
    for bad in (dict(project="spps-canonical"), dict(purpose=None), dict(health="starting"),
                dict(bindings=[{"HostIp": "0.0.0.0", "HostPort": "55436"}]),
                dict(mounts=[{"Type": "volume", "Name": "spps-canonical_spps_development"}]),
                dict(tmpfs={})):
        assert guard.container_problems(good_facts(**bad)), bad


def test_ci_mode_requires_repository_and_workflow_declaration(monkeypatch):
    for key in ("GITHUB_ACTIONS", "GITHUB_REPOSITORY", "SPPS_EPHEMERAL_CI_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    assert not guard.in_ci_service()
    monkeypatch.setenv("SPPS_EPHEMERAL_CI_SERVICE", "1")
    assert not guard.in_ci_service()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone-else/fork")
    assert not guard.in_ci_service()


def test_ci_mode_still_refuses_protected_ports(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", guard.REPOSITORY)
    monkeypatch.setenv("SPPS_EPHEMERAL_CI_SERVICE", "1")
    with pytest.raises(EphemeralGuardError):
        guard.verify_ephemeral_target("postgresql+psycopg://u:p@127.0.0.1:55432/spps_dev")


def test_integration_fixture_never_defaults_to_canonical_env():
    source = open("tests_integration/conftest.py", encoding="utf-8").read()
    assert "os.getenv('SPPS_INTEGRATION_ENV', '.env.integration')" not in source
    assert "verify_ephemeral_target" in source


def test_identity_tests_default_to_ephemeral_target(monkeypatch):
    from tools.identity_review_db import selected_target
    monkeypatch.delenv("SPPS_IDENTITY_TARGET", raising=False)
    assert selected_target().port == 55436
    monkeypatch.setenv("SPPS_IDENTITY_TARGET", "bogus")
    with pytest.raises(RuntimeError):
        selected_target()


def test_compose_file_is_tmpfs_loopback_and_volume_free():
    text = open("compose.test-ephemeral.yml", encoding="utf-8").read()
    assert "name: spps-test-ephemeral" in text
    assert '"127.0.0.1:${SPPS_EPHEMERAL_PORT:-55436}:5432"' in text  # default slot stays 55436
    assert "tmpfs:" in text and "volumes:" not in text
    for protected in ("55432", "55433", "55434", "60528", "54322"):
        assert f"{protected}:5432" not in text


def test_dynamic_port_of_protected_container_is_refused(monkeypatch):
    # spps-m12-physical-* binds 127.0.0.1::5432: its host port changes on every start.
    monkeypatch.setattr(guard, "in_ci_service", lambda: False)
    monkeypatch.setattr(guard, "protected_live_ports", lambda: {32768, 55436})
    with pytest.raises(EphemeralGuardError, match="PROTECTED container"):
        guard.verify_ephemeral_target("postgresql+psycopg://u:p@127.0.0.1:55436/spps_dev")


def test_live_ports_read_from_docker_inspect(monkeypatch):
    import json as _json
    fake = _json.dumps([{"HostConfig": {"PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]}},
                         "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}}}])
    monkeypatch.setattr(guard.subprocess, "check_output", lambda *a, **k: fake)
    assert guard.protected_live_ports() == {32768}
