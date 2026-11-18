r"""Regression test: source and temp SQLite connections MUST close before rename.

Run from SPPS project root after applying the hotfix:
    .\.venv\Scripts\python.exe -m pytest tests/test_backup_handle_release.py -q
"""
import os
import sqlite3

from tools import backup_sqlite


def test_backup_closes_both_handles_before_rename(tmp_path, monkeypatch):
    source = tmp_path / "fictional.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        db.execute("INSERT INTO sample VALUES ('fictional')")
        db.commit()
    original_bytes = source.read_bytes()

    tracked_connections = []
    real_connect = sqlite3.connect
    real_replace = os.replace

    class TrackedConnection(sqlite3.Connection):
        closed_for_test = False

        def close(self):
            self.closed_for_test = True
            return super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        conn = real_connect(*args, **kwargs)
        tracked_connections.append(conn)
        return conn

    def replace_only_if_closed(source_path, target_path):
        assert len(tracked_connections) == 2
        assert all(conn.closed_for_test for conn in tracked_connections)
        return real_replace(source_path, target_path)

    monkeypatch.setattr(backup_sqlite.sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(backup_sqlite.os, "replace", replace_only_if_closed)
    result = backup_sqlite.backup_existing(source, tmp_path / "backups")

    assert result and result.exists()
    assert source.read_bytes() == original_bytes
    with real_connect(result) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "fictional"
