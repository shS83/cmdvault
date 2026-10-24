"""Update favorite and priority snapshots only for already-approved commands.

Never approves new history and never changes the underlying Bash history.
"""
from pathlib import Path
import json
from ..database import transaction
from ..models import SyncState
from ..security.secrets import CommandSecretScanner
from .store import LocalStore, natural_key


def stage_approved_metadata(db_path, favorites, priorities):
    db_path = Path(db_path)
    if not db_path.is_file():
        return 0
    favorite_set = set(favorites)
    priority_index = {command: index for index, command in enumerate(priorities)}
    scanner = CommandSecretScanner()
    changed = 0
    with LocalStore(db_path) as store, transaction(store.connection):
        for record in store.records('command'):
            if record.sync_state != SyncState.ELIGIBLE:
                continue
            command = record.data['command']
            data = dict(record.data)
            data['favorite'] = command in favorite_set or command in priority_index
            if command in priority_index:
                data['priority'] = priority_index[command]
            else:
                data.pop('priority', None)
            if data != record.data:
                scan = scanner.scan(data)
                if not scan.allowed or scan.findings:
                    raise ValueError('Secret or invalid approved metadata; nothing staged')
                store._put('command', natural_key('command', command), data)
                changed += 1
    return changed
