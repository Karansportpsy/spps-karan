"""Phase F: backup -> restore -> verify drill and migration-ledger integrity (ephemeral cluster only)."""
from sqlalchemy import create_engine

from tools import db_drill
from tools.development_db import CLINICAL_MIGRATIONS
from tools.identity_review_db import IDENTITY_MIGRATIONS


def test_backup_restore_reproduces_data_rls_policies_and_functions(integration_env):
    report = db_drill.drill()
    clinical, identity = report["databases"]["spps_dev"], report["databases"]["spps_dev_identity"]
    assert clinical["identical"] and identity["identical"]
    assert clinical["tables"] == 32 and identity["tables"] == 22
    assert clinical["rows"] > 0 and identity["rows"] > 0
    assert identity["definer_functions"] >= 60 and identity["policies"] >= 30


def test_migration_ledger_matches_source_and_detects_tampering(integration_env, monkeypatch, tmp_path):
    for database, names in (("spps_dev", CLINICAL_MIGRATIONS), ("spps_dev_identity", IDENTITY_MIGRATIONS)):
        engine = create_engine(db_drill.admin_url(database))
        try:
            with engine.connect() as conn:
                assert db_drill.verify_ledger(conn, names) == []
                # Exercise actual source tampering, not an implementation-specific
                # digest mock. Never edit tracked SQL or the database ledger.
                source = tmp_path / database
                source.mkdir()
                for name in names:
                    (source / name).write_bytes((db_drill.MIGRATIONS / name).read_bytes())
                with monkeypatch.context() as scoped:
                    scoped.setattr(db_drill, "MIGRATIONS", source)
                    assert db_drill.verify_ledger(conn, names) == []
                    changed = source / names[0]
                    original = changed.read_bytes()
                    changed.write_bytes(original + b"\n-- unexpected content after apply\n")
                    assert db_drill.verify_ledger(conn, names) == [
                        f"file changed after apply: {names[0]}"
                    ]
                    changed.write_bytes(original)
                    assert db_drill.verify_ledger(conn, names) == []
                assert any("applied but missing from source" in p for p in db_drill.verify_ledger(conn, names[:-1]))
        finally:
            engine.dispose()
