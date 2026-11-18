"""Create a verified online snapshot before opening an existing local SPPS database.

This utility NEVER edits, deletes, truncates, or VACUUMs the source database.
It operates only on a user-selected local SQLite file, not on PostgreSQL.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3


def backup_existing(source: Path, backups: Path) -> Path | None:
    source = Path(source)
    if not source.exists():
        return None
    if not source.is_file():
        raise ValueError('Database path is not a regular file')
    backups = Path(backups)
    backups.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = backups / f'{source.stem}-{timestamp}-{secrets.token_hex(4)}.sqlite3'
    temp = dest.with_suffix('.sqlite3.tmp')
    # Read-only open prevents accidental writes to original; sqlite backup API
    # takes a transaction-consistent snapshot even when the source uses WAL.
    try:
        src_uri = source.resolve().as_uri() + '?mode=ro'
        # `with sqlite3.connect(...)` manages a transaction, but does NOT close
        # the connection. On Windows, an open destination connection can lock
        # the .tmp file and cause WinError 32 when os.replace is called below.
        # closing(...) explicitly releases both source and destination handles.
        with closing(sqlite3.connect(src_uri, uri=True, timeout=10)) as src:
            with closing(sqlite3.connect(temp, timeout=10)) as dst:
                src.backup(dst, pages=100, sleep=0.1)
                status = dst.execute('PRAGMA integrity_check').fetchone()
                if not status or status[0] != 'ok':
                    raise RuntimeError('Backup SQLite integrity check failed')
                dst.commit()
        os.replace(temp, dest)
    except Exception:
        # A second process (for example, antivirus) may temporarily lock the
        # incomplete backup. Never let cleanup hide the actual backup error.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest


def main():
    parser = argparse.ArgumentParser(description='Safely back up an existing local SPPS SQLite DB')
    parser.add_argument('database', type=Path)
    parser.add_argument('backup_directory', type=Path)
    args = parser.parse_args()
    try:
        target = backup_existing(args.database, args.backup_directory)
        print('Verified pre-start SQLite backup: ' + str(target) if target else
              'No existing SQLite file; initial creation will use fictional demo seed data.')
    except Exception as exc:
        print('Database backup failed. Startup aborted without modifying source: ' + str(exc))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
