"""The v2 production preflight against the real serial schema: passes on the full set, names a missing object."""
import psycopg
import pytest
from sqlalchemy import create_engine

from .conftest import dsn


def test_preflight_passes_on_the_full_serial_set(pgenv):
    from server.v2.preflight import verify_database
    engine = create_engine(pgenv["SPPS_V2_PG_ADMIN_URL"])
    try:
        with engine.connect() as conn:
            verify_database(conn, {"SPPS_ATHLETE_LOOP_ENABLED": "true", "SPPS_REPORT_LINKS_ENABLED": "true",
                                   "SPPS_PRACTITIONER_CLOSURE_ENABLED": "true"})
    finally:
        engine.dispose()


def test_preflight_names_a_missing_migration(pgenv):
    from server.v2.preflight import PreflightError, verify_database
    engine = create_engine(pgenv["SPPS_V2_PG_ADMIN_URL"])
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            conn.exec_driver_sql("DROP FUNCTION spps_dev.v2_open_report_link(text)")
            with pytest.raises(PreflightError, match="0037_v2_reports.sql"):
                verify_database(conn, {})
            trans.rollback()
    finally:
        engine.dispose()
