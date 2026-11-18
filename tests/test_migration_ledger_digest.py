"""Ledger identity must not depend on git line-ending conversion (found in the RC provenance audit)."""
import hashlib
import pytest
from tools import db_drill


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_a, **_k):
        rows = self.rows

        class R:
            def all(self):
                return list(rows.items())
        return R()


def test_digest_ignores_crlf():
    assert db_drill.migration_digest(b"SELECT 1;\r\nSELECT 2;\r\n") == db_drill.migration_digest(b"SELECT 1;\nSELECT 2;\n")
    assert db_drill.migration_digest(b"SELECT 1;\n") != db_drill.migration_digest(b"SELECT 2;\n")


def test_verify_accepts_normalised_and_legacy_raw_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(db_drill, "MIGRATIONS", tmp_path)
    (tmp_path / "0001_a.sql").write_bytes(b"CREATE TABLE a();\r\n")   # checked out with CRLF
    (tmp_path / "0002_b.sql").write_bytes(b"CREATE TABLE b();\r\n")
    (tmp_path / "0003_c.sql").write_bytes(b"CREATE TABLE c();\n")
    ledger = {"0001_a.sql": db_drill.migration_digest(b"CREATE TABLE a();\n"),        # new normalised form
              "0002_b.sql": hashlib.sha256(b"CREATE TABLE b();\n").hexdigest(),       # legacy: recorded from an LF checkout
              "0003_c.sql": hashlib.sha256(b"CREATE TABLE c();\r\n").hexdigest()}     # legacy: recorded from a CRLF checkout
    assert db_drill.verify_ledger(FakeConn(ledger), ["0001_a.sql", "0002_b.sql", "0003_c.sql"]) == []


def test_verify_still_detects_real_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(db_drill, "MIGRATIONS", tmp_path)
    (tmp_path / "0001_a.sql").write_bytes(b"CREATE TABLE a(x int);\n")
    ledger = {"0001_a.sql": db_drill.migration_digest(b"CREATE TABLE a();\n")}
    assert db_drill.verify_ledger(FakeConn(ledger), ["0001_a.sql"]) == ["file changed after apply: 0001_a.sql"]


@pytest.mark.parametrize("recorded_eol", [b"\n", b"\r\n"])
def test_legacy_ledger_rejects_content_tampering_after_checkout_conversion(tmp_path, monkeypatch, recorded_eol):
    monkeypatch.setattr(db_drill, "MIGRATIONS", tmp_path)
    name = "0001_a.sql"
    original = b"CREATE TABLE a();" + recorded_eol
    ledger = FakeConn({name: hashlib.sha256(original).hexdigest()})
    changed = tmp_path / name
    changed.write_bytes(b"CREATE TABLE a();\n")
    assert db_drill.verify_ledger(ledger, [name]) == []
    changed.write_bytes(b"CREATE TABLE a();\n-- unexpected content after apply\n")
    assert db_drill.verify_ledger(ledger, [name]) == [f"file changed after apply: {name}"]
    changed.write_bytes(b"CREATE TABLE a();\n")
    assert db_drill.verify_ledger(ledger, [name]) == []
