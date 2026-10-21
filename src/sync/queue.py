"""Durable coalescing outbox; contains identities, never command payloads."""
import re
import sqlite3
from typing import List
from ..database import transaction, utc_now
from ..models import OutboxEvent, new_uuid, validate_uuid


class Outbox:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def enqueue(self, record_uuid: str, revision: int, operation: str) -> None:
        """Call inside the same transaction as the record change.

        Replacing an event generates a new event ID. An acknowledgement for an
        older in-flight revision therefore cannot erase a newer queued change.
        """
        validate_uuid(record_uuid)
        if not self.connection.in_transaction:
            raise ValueError('Outbox writes require a record transaction')
        if operation not in ('upsert','delete') or type(revision) is not int or revision < 1:
            raise ValueError('Invalid outbox change')
        record = self.connection.execute('SELECT revision,sync_state,deleted_at FROM cv_records WHERE uuid=?',
                                         (record_uuid,)).fetchone()
        if record is None or record['revision'] != revision:
            raise ValueError('Outbox revision must match the stored record')
        expected = 'delete' if record['deleted_at'] or record['sync_state'] == 'local_only' else 'upsert'
        if operation != expected:
            raise ValueError('Outbox operation does not match record eligibility')
        self.connection.execute('DELETE FROM cv_outbox WHERE record_uuid=?',(record_uuid,))
        self.connection.execute('''INSERT INTO cv_outbox
            (event_id,record_uuid,revision,operation,created_at) VALUES (?,?,?,?,?)''',
            (new_uuid(),record_uuid,revision,operation,utc_now()))

    def pending(self, limit: int = 100) -> List[OutboxEvent]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Outbox limit must be 1..10000')
        rows = self.connection.execute('SELECT * FROM cv_outbox ORDER BY sequence LIMIT ?',(limit,))
        return [OutboxEvent(**dict(row)) for row in rows]

    def acknowledge(self, event_id: str) -> bool:
        validate_uuid(event_id)
        with transaction(self.connection):
            return self.connection.execute('DELETE FROM cv_outbox WHERE event_id=?',(event_id,)).rowcount == 1

    def fail(self, event_id: str, error_code: str) -> bool:
        validate_uuid(event_id)
        # Store machine codes, not raw exceptions which may contain credentials.
        if not re.fullmatch('[a-z_]{1,48}',error_code):
            raise ValueError('Expected a short error code')
        with transaction(self.connection):
            return self.connection.execute('''UPDATE cv_outbox SET attempts=attempts+1,
                error_code=? WHERE event_id=?''',(error_code,event_id)).rowcount == 1
