"""Additive, transactional schema for the local sync sidecar.

The original application has no SQLite schema: Bash history and favorites.json
remain in place. Schema 1 establishes local records; schema 2 adds sync metadata.
Only cmdvault-prefixed tables are owned by these migrations; user_version and
unrelated tables are never changed.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Union
from .models import new_uuid

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if connection.in_transaction:
        raise ValueError('Nested transactions are not supported')
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _schema_one(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE cv_records (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('command','tag','command_tag')),
        natural_key TEXT UNIQUE,
        data TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')


def _schema_two(connection: sqlite3.Connection) -> None:
    for definition in (
        'uuid TEXT',
        "sync_state TEXT NOT NULL DEFAULT 'local_only' CHECK(sync_state IN ('local_only','sync_eligible'))",
        'revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0)',
        'deleted_at TEXT',
    ):
        connection.execute('ALTER TABLE cv_records ADD COLUMN ' + definition)
    for row in connection.execute('SELECT id FROM cv_records').fetchall():
        connection.execute('UPDATE cv_records SET uuid=? WHERE id=?', (new_uuid(),row[0]))
    connection.execute('CREATE UNIQUE INDEX cv_records_uuid ON cv_records(uuid)')
    # ALTER TABLE cannot add a generated UUID default to existing rows. Backfill
    # above and triggers below enforce non-null, immutable identities afterwards.
    connection.execute('''CREATE TRIGGER cv_uuid_insert BEFORE INSERT ON cv_records
        WHEN NEW.uuid IS NULL OR length(NEW.uuid) != 36
          OR substr(NEW.uuid,9,1) != '-' OR substr(NEW.uuid,14,1) != '-'
          OR substr(NEW.uuid,19,1) != '-' OR substr(NEW.uuid,24,1) != '-'
          OR length(replace(NEW.uuid,'-','')) != 32
          OR replace(NEW.uuid,'-','') GLOB '*[^0-9a-f]*'
        BEGIN SELECT RAISE(ABORT, 'missing record UUID'); END''')
    connection.execute('''CREATE TRIGGER cv_uuid_update BEFORE UPDATE OF uuid ON cv_records
        WHEN NEW.uuid IS NOT OLD.uuid
        BEGIN SELECT RAISE(ABORT, 'immutable record UUID'); END''')
    connection.execute('''CREATE TABLE cv_links (
        uuid TEXT PRIMARY KEY REFERENCES cv_records(uuid),
        command_uuid TEXT NOT NULL REFERENCES cv_records(uuid),
        tag_uuid TEXT NOT NULL REFERENCES cv_records(uuid)
    )''')
    connection.execute('CREATE INDEX cv_links_command ON cv_links(command_uuid)')
    connection.execute('CREATE INDEX cv_links_tag ON cv_links(tag_uuid)')
    connection.execute('''CREATE TABLE cv_outbox (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        record_uuid TEXT NOT NULL UNIQUE REFERENCES cv_records(uuid),
        revision INTEGER NOT NULL CHECK(revision > 0),
        operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
        created_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        error_code TEXT
    )''')
    connection.execute('''CREATE TABLE cv_sync_state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )''')
    connection.execute('INSERT INTO cv_sync_state VALUES (?,?)',('device_id',new_uuid()))
    connection.execute('''CREATE TABLE cv_favorites_intent (
        source TEXT PRIMARY KEY,
        before_hash TEXT NOT NULL,
        target_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')


def migrate(connection: sqlite3.Connection, target: int = SCHEMA_VERSION) -> None:
    if not 1 <= target <= SCHEMA_VERSION:
        raise ValueError('Unsupported schema version')
    with transaction(connection):
        connection.execute('''CREATE TABLE IF NOT EXISTS cv_schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        )''')
        versions = [row[0] for row in connection.execute(
            'SELECT version FROM cv_schema_migrations ORDER BY version')]
        if versions != list(range(1,len(versions)+1)) or (versions and versions[-1] > SCHEMA_VERSION):
            raise ValueError('Unsupported or incomplete cmdvault schema; database left unchanged')
        if len(versions) > target:
            raise ValueError('Database downgrades are not supported')
        migrations = (_schema_one,_schema_two)
        for version in range(len(versions)+1,target+1):
            migrations[version-1](connection)
            connection.execute('INSERT INTO cv_schema_migrations VALUES (?,?)',(version,utc_now()))


def connect(path: Union[str, Path]) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Create privately before SQLite opens its rollback journal. Do not alter the
    # permissions of an existing user's file or use a global umask.
    fd = os.open(path,os.O_CREAT | os.O_RDWR,0o600)
    os.close(fd)
    connection = sqlite3.connect(str(path),timeout=5,isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection
