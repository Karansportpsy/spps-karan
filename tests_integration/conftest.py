"""Opt-in tests against the DISPOSABLE spps-test-ephemeral PostgreSQL only.

These tests create fictional records through the API. They previously defaulted
to the ignored `.env.integration`, which targets the protected canonical
database on 55432. That default is removed: SPPS_INTEGRATION_ENV must name an
environment file whose database URLs pass the ephemeral guard (loopback 55436,
verified tmpfs container or the declared CI service). Use:

    scripts/spps-check.ps1 -Tier db
"""
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tools.ephemeral_guard import EphemeralGuardError, verify_ephemeral_target


@pytest.fixture(scope='session')
def _verified_integration_values():
    raw = os.getenv('SPPS_INTEGRATION_ENV', '')
    if not raw:
        pytest.skip('SPPS_INTEGRATION_ENV not set; run through scripts/spps-check.ps1 (ephemeral DB)')
    path = Path(raw)
    if path.resolve() == (Path(__file__).resolve().parents[1] / '.env.integration').resolve():
        pytest.exit('STOP: refusing the protected canonical .env.integration for automated tests', 2)
    if not path.is_file():
        pytest.skip('Ephemeral integration environment file missing')
    values = dotenv_values(path)
    try:
        for key in ('DATABASE_URL', 'SPPS_AUTH_DATABASE_URL'):
            verify_ephemeral_target(values.get(key) or '', database='spps_dev')
        for key in ('SPPS_IDENTITY_AUTH_DATABASE_URL', 'SPPS_IDENTITY_PORTAL_DATABASE_URL'):
            if values.get(key):
                verify_ephemeral_target(values[key], database='spps_dev_identity')
    except EphemeralGuardError as exc:
        pytest.exit(str(exc), 2)
    assert values.get('MODEL_C_INTEGRATION_DEV') == 'true'
    return values


@pytest.fixture
def integration_env(_verified_integration_values, monkeypatch):
    for key, value in _verified_integration_values.items():
        monkeypatch.setenv(key, value)
    return _verified_integration_values


@pytest.fixture
def app(integration_env):
    from test_support.app import create_app
    application = create_app(init_database=False, seed_demo=False)
    yield application
    application.state.engine.dispose()
    application.state.auth_engine.dispose()
    for name in ('portal_auth_engine', 'portal_engine'):
        engine = getattr(application.state, name, None)
        if engine is not None:
            engine.dispose()
