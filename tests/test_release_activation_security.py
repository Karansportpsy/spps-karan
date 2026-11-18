"""Security-release contract: no live DB needed; synthetic activation settings only."""
from pathlib import Path
import pytest

from server.release_migrations import (
    MIGRATIONS, MigrationRefused, sql_payload, validate_connection,
)
from server.hosted_profile import HostedProfileError, validate_startup

ROOT = Path(__file__).resolve().parents[1]


def test_all_pending_sql_migrations_have_atomic_transaction_boundaries():
    expected = [*MIGRATIONS["clinical"], *MIGRATIONS["identity"]]
    assert "0021_pending_practitioner_registration.sql" in expected
    assert "0022_verified_owner_claim.sql" in expected
    assert "0026_pending_approval_free_launch.sql" in expected
    for name in expected:
        payload, checksum = sql_payload(ROOT / "migrations" / name)
        assert len(checksum) == 64
        assert "BEGIN;" not in payload[:32]
        assert not payload.rstrip().endswith("COMMIT;")
        assert payload.strip()


def test_migration_runner_rejects_app_role_and_insecure_url():
    valid = {
        "SPPS_CLINICAL_MIGRATION_URL":
            "postgresql+psycopg://spps_schema_owner:fictional@db.example.org/spps_dev?sslmode=verify-full",
        "SPPS_CLINICAL_MIGRATION_ROLE": "spps_schema_owner",
        "SPPS_CLINICAL_DATABASE_NAME": "spps_dev",
    }
    assert validate_connection("clinical", valid).startswith("postgresql://")
    with pytest.raises(MigrationRefused):
        validate_connection("clinical", {**valid,
            "SPPS_CLINICAL_MIGRATION_URL": valid["SPPS_CLINICAL_MIGRATION_URL"].replace(
                "spps_schema_owner:", "spps_dev_app:"),
            "SPPS_CLINICAL_MIGRATION_ROLE": "spps_dev_app"})
    with pytest.raises(MigrationRefused):
        validate_connection("clinical", {**valid,
            "SPPS_CLINICAL_MIGRATION_URL": valid["SPPS_CLINICAL_MIGRATION_URL"].replace(
                "sslmode=verify-full","sslmode=disable")})
    with pytest.raises(MigrationRefused):
        validate_connection("identity", valid)


def test_prod_cannot_be_unlocked_by_a_single_environment_toggle():
    with pytest.raises(HostedProfileError,match="Production release prerequisites"):
        validate_startup({
            "MODEL_C_ENV":"production",
            "MODEL_C_HOSTED_PILOT_UNLOCKED":"true",
            "SPPS_STAGING_DATA_CLASS":"real",
        })


def test_verified_registration_is_pending_only_and_owner_claim_does_not_verify_email():
    sql=(ROOT/"migrations/0026_pending_approval_free_launch.sql").read_text()
    assert "'pending_approval'" in sql and "'free'" in sql
    assert "FALSE,'free',FALSE" in sql
    assert "public_approved" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    owner=(ROOT/"migrations/0022_verified_owner_claim.sql").read_text()
    assert "p_email <> 'karansportpsy@gmail.com'" in owner
    assert "REVOKE ALL ON FUNCTION" in owner
    assert "auth.users" not in owner
