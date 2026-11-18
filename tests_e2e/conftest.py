import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: needs a throwaway PostgreSQL (see tests_e2e/README.md)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SPPS_E2E_ADMIN_URL"):
        return
    skip = pytest.mark.skip(reason="opt-in: set SPPS_E2E_ADMIN_URL and SPPS_E2E_DESTROY_SCHEMAS=yes (see tests_e2e/README.md)")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def world():
    """A freshly built database, the production app wired to it, and a fake Supabase Auth."""
    import harness as h
    mp = pytest.MonkeyPatch()
    if "server.main" in sys.modules:
        pytest.exit("Run tests_e2e in its own pytest process; server.main was already imported with another environment.")
    owner_email = h.prod_env(mp)
    steps = h.reset_db()
    gotrue = h.FakeGoTrue()
    gotrue.install(mp)
    from test_support.app import create_app
    app = create_app(init_database=False, seed_demo=False)
    w = type("World", (), {})()
    w.h, w.app, w.g, w.owner_email, w.sql_steps = h, app, gotrue, owner_email, steps
    w.visitor = h.Browser(app, gotrue)
    yield w
    mp.undo()
