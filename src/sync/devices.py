"""Persistent local device identity and opaque server cursors; no networking."""
import sqlite3
from typing import Optional
from ..database import transaction
from ..models import validate_uuid


class SyncStateStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    @property
    def device_id(self) -> str:
        row = self.connection.execute("SELECT value FROM cv_sync_state WHERE key='device_id'").fetchone()
        if row is None:
            raise ValueError('Missing local device identity')
        return validate_uuid(row[0])

    def cursor(self, scope: str = 'default') -> Optional[str]:
        row = self.connection.execute('SELECT value FROM cv_sync_state WHERE key=?',('cursor:'+scope,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, value: Optional[str], scope: str = 'default') -> None:
        if not isinstance(scope,str) or not scope or (value is not None and not isinstance(value,str)):
            raise ValueError('Expected an opaque string cursor and scope')
        key = 'cursor:'+scope
        with transaction(self.connection):
            self.connection.execute('DELETE FROM cv_sync_state WHERE key=?',(key,))
            if value is not None:
                self.connection.execute('INSERT INTO cv_sync_state VALUES (?,?)',(key,value))
