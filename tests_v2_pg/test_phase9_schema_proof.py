"""Phase 9: a brand-new empty database built from supabase/sql/ in order has exactly the schema that migrations/
produces (v1 serial steps then the raw v2 files), and the read-only v2 verification passes."""
import sys
from pathlib import Path


def test_serial_set_and_migrations_give_identical_schemas(pgenv):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import v2_schema_proof
    assert v2_schema_proof.main() == 0
