"""Fictional database backup safeguards."""
import sqlite3
from tools.backup_sqlite import backup_existing


def test_backup_preserves_source(tmp_path):
    source = tmp_path / 'fictional.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE test_records (label TEXT NOT NULL)')
        db.execute('INSERT INTO test_records VALUES (?)', ('Fictional example',))
        db.commit()
    before = source.read_bytes()
    backup = backup_existing(source, tmp_path/'backups')
    assert source.read_bytes() == before
    assert backup is not None and backup.is_file()
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT label FROM test_records').fetchone()[0] == 'Fictional example'


def test_no_file_creates_no_database(tmp_path):
    source = tmp_path / 'missing.db'
    assert backup_existing(source, tmp_path/'backups') is None
    assert not source.exists()


def test_corrupt_file_never_deleted(tmp_path):
    source = tmp_path / 'bad.db'
    source.write_bytes(b'not a SQLite database; do not destroy the original')
    before = source.read_bytes()
    try:
        backup_existing(source, tmp_path/'backups')
    except (sqlite3.DatabaseError, RuntimeError):
        pass
    else:
        raise AssertionError('An invalid SQLite input must abort startup')
    assert source.read_bytes() == before
    assert not list((tmp_path/'backups').glob('*.sqlite3'))
