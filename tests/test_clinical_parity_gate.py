"""Fail-closed clinical parity route and environment-contract tests."""
import pytest

from server.development_profile import allowed
from test_support.app import create_app


@pytest.mark.parametrize("path", [
    "/api/notes", "/api/notes/9/finalize", "/api/assignments",
    "/api/reports", "/api/injury/episodes",
    "/api/injury/episodes/9/checkins",
])
def test_clinical_routes_opt_in(monkeypatch, path):
    monkeypatch.delenv("SPPS_DEV_CLINICAL_PARITY", raising=False)
    assert not allowed("POST", path)
    monkeypatch.setenv("SPPS_DEV_CLINICAL_PARITY", "true")
    assert allowed("POST", path)


@pytest.mark.parametrize("path", [
    "/api/public/practitioner-registration", "/api/payment/razorpay/webhook",
    "/api/slots", "/api/bookings/1/room-invite", "/api/practitioner/profile",
])
def test_commercial_and_media_mutations_always_closed(monkeypatch, path):
    monkeypatch.setenv("SPPS_DEV_CLINICAL_PARITY", "true")
    assert not allowed("POST", path)


def test_hosted_mode_cannot_enable_local_clinical_parity(monkeypatch):
    monkeypatch.setenv("SPPS_DEV_CLINICAL_PARITY", "true")
    monkeypatch.delenv("MODEL_C_INTEGRATION_DEV", raising=False)
    monkeypatch.setenv("MODEL_C_ENV", "staging")
    with pytest.raises(RuntimeError, match="Clinical parity requires isolated local PostgreSQL"):
        create_app(init_database=False, seed_demo=False)
