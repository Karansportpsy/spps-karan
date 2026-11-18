"""0030/0031 add covering indexes only. They must stay index-only (no grant, policy, function or data change),
be listed for the release runner under the right schema, and the serial Supabase copy must carry the same
statements plus a ledger row whose checksum matches the migration file."""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = [("0030_clinical_foreign_key_indexes", 31, "spps_dev", 11, "clinical"),
         ("0031_identity_foreign_key_indexes", 32, "spps_identity_dev", 4, "identity")]


@pytest.mark.parametrize("name,step,schema,count,kind", CASES)
def test_migration_is_index_only_and_matches_the_serial_copy(name, step, schema, count, kind):
    raw = (ROOT / "migrations" / f"{name}.sql").read_text(encoding="utf-8")
    statements = [s.strip() for s in re.sub(r"(?m)^--.*$", "", raw).split(";") if s.strip()]
    assert statements[0] == "BEGIN" and statements[-1] == "COMMIT"
    body = statements[1:-1]
    assert len(body) == count
    pattern = re.compile(rf"CREATE INDEX IF NOT EXISTS ix_(\w+) ON {schema}\.(\w+) \((\w+)\)")
    for statement in body:
        match = pattern.fullmatch(statement)
        assert match, statement
        assert match.group(1) == f"{match.group(2)}_{match.group(3)}"
    serial = (ROOT / "supabase" / "sql" / f"{step}_{name}.sql").read_text(encoding="utf-8")
    for statement in body:
        assert statement + ";" in serial
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert f"VALUES ('{name}.sql','{checksum}')" in serial
    # Identity tables belong to spps_identity_owner: the hosted "postgres" role must switch to it (as steps 07-19 do)
    # and switch back before writing the ledger. Clinical tables need no switch.
    wrapped = "BEGIN;\nSET LOCAL ROLE spps_identity_owner;\n" in serial and "RESET ROLE;\nINSERT INTO spps_ops.migration_history" in serial
    assert wrapped is (kind == "identity")
    from server.release_migrations import MIGRATIONS, sql_payload
    assert MIGRATIONS[kind][-1] == f"{name}.sql"
    payload, payload_checksum = sql_payload(ROOT / "migrations" / f"{name}.sql")
    assert payload_checksum == checksum and "BEGIN" not in payload and "COMMIT" not in payload
