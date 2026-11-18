"""Repository-wide test hook: every test starts with empty rate-limit windows.

The v2 per-IP ceilings (server/v2/guards.py) are process-wide, and every TestClient uses the same address, so
without a reset one test's sign-ins would count against the next test's. Production behaviour is unchanged.
"""
import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limit_windows():
    try:
        from server.v2 import guards
    except Exception:  # pragma: no cover - tools that run without the server package
        yield
        return
    guards.reset()
    yield
    guards.reset()
