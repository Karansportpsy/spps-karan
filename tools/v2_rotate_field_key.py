"""Re-encrypt every encrypted field with the current key (key rotation) or encrypt legacy plain-text rows.

Key rotation, step by step (DEPLOYMENT.md "Field encryption"):
  1. Generate a new key:            python tools/v2_rotate_field_key.py new-key k2
  2. In Render, ADD it to SPPS_FIELD_KEYS (keep the old one) and set SPPS_FIELD_KEY_CURRENT=k2. Redeploy.
     New writes now use k2; old rows still decrypt with k1.
  3. Re-encrypt old rows with a privileged connection (the Supabase "postgres" role, run from your own computer,
     never from the app):
        SPPS_ROTATION_DATABASE_URL=postgresql://... SPPS_FIELD_KEYS=... SPPS_FIELD_KEY_CURRENT=k2 \\
        python tools/v2_rotate_field_key.py rotate
     It prints how many rows were re-encrypted per column and how many remain on other keys (must be 0).
  4. Remove k1 from SPPS_FIELD_KEYS in Render and redeploy. Destroy every copy of k1.

The same ``rotate`` also encrypts rows written before encryption was switched on (no ``enc:v1:`` prefix).
The tool touches only the columns listed in ENCRYPTED_COLUMNS, one transaction per column, in batches.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from server.v2 import fieldcrypt  # noqa: E402

# table, primary key, column, associated-data name (must match the EncryptedText(...) declaration)
ENCRYPTED_COLUMNS = [
    ("notes", "id", "content", "notes.content"),
    ("note_amendments", "id", "content", "note_amendments.content"),
    ("case_formulations", "id", "body", "case_formulations.body"),
    ("note_drafts", "id", "content", "note_drafts.content"),
    ("athlete_profiles", "athlete_id", "referral_notes", "athlete_profiles.referral_notes"),
    ("athlete_imports", "id", "payload", "athlete_imports.payload"),
    ("shared_reports", "id", "values", "shared_reports.values"),
    ("shared_reports", "id", "snapshot", "shared_reports.snapshot"),
    # identity schema (phase 8): an approved-for-sharing copy of a signed coach-level report
    ("loop_share_requests", "id", "snapshot", "loop_share_requests.snapshot", "spps_identity_dev."),
]


def rotate(url: str, schema_prefix: str = "") -> dict:
    current, _ = fieldcrypt._keys()
    if not current:
        raise SystemExit("SPPS_FIELD_KEY_CURRENT is not set")
    engine = create_engine(url)
    report = {}
    try:
        for table, pk, col, ad, *schema in ENCRYPTED_COLUMNS:
            name = ((schema[0] if schema and schema_prefix else "") or schema_prefix) + table
            col = f'"{col}"'  # quoted: shared_reports has a column named "values"
            with engine.begin() as conn:
                if not _exists(conn, name):
                    continue
                rows = conn.execute(text(f"SELECT {pk}, {col} FROM {name}")).all()
                changed = 0
                for rid, value in rows:
                    if value is None or fieldcrypt.key_id(value) == current:
                        continue
                    plain = fieldcrypt.decrypt(value, ad)
                    conn.execute(text(f"UPDATE {name} SET {col} = :v WHERE {pk} = :id"),
                                 {"v": fieldcrypt.encrypt(plain, ad), "id": rid})
                    changed += 1
                left = sum(1 for _, v in conn.execute(text(f"SELECT {pk}, {col} FROM {name}")).all()
                           if v is not None and fieldcrypt.key_id(v) != current)
                report[table] = {"re_encrypted": changed, "not_on_current_key": left}
    finally:
        engine.dispose()
    return report


def _exists(conn, name: str) -> bool:
    try:
        with conn.begin_nested():
            conn.execute(text(f"SELECT 1 FROM {name} WHERE 1=0"))
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if sys.argv[1:2] == ["new-key"]:
        print(fieldcrypt.new_key_entry(sys.argv[2]))
    elif sys.argv[1:2] == ["rotate"]:
        url = os.environ["SPPS_ROTATION_DATABASE_URL"]
        prefix = "spps_dev." if url.startswith("postgres") else ""
        for table, result in rotate(url, prefix).items():
            print(table, result)
    else:
        raise SystemExit(__doc__)
