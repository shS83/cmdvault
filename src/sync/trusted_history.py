"""Import current shell history for an opt-in trusted-device sync.

Never touches .bash_history. Fail-closed for recognizable secrets.
"""
import json
import re
import socket
from collections import Counter
from pathlib import Path
from ..database import transaction
from ..models import SyncState
from ..security.secrets import CommandSecretScanner
from .store import LocalStore, natural_key
from .approved_metadata import stage_approved_metadata
from .favorites import parse_favorites


def commands(text):
    pending = None
    for line in text.splitlines():
        if re.fullmatch(r'#[0-9]+', line):
            if pending is not None and '\n'.join(pending).strip():
                yield '\n'.join(pending)
            pending = []
        elif pending is not None:
            pending.append(line)
        elif line.strip():
            yield line
    if pending is not None and '\n'.join(pending).strip():
        yield '\n'.join(pending)


def prepare(db_path, history_path, favorites_path, priority_path):
    """Stage safe current commands; return counts (unique, excluded, changed)."""
    history_path = Path(history_path)
    if not history_path.is_file():
        raise FileNotFoundError(f'History not found: {history_path}')
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError('Initialize sync SQLite before trusted history import')
    raw = history_path.read_text(encoding='utf-8', errors='replace')
    occurrences = Counter(commands(raw))
    favorites_path = Path(favorites_path)
    favorite_list = parse_favorites(favorites_path.read_bytes()) if favorites_path.exists() else []
    priority_path = Path(priority_path)
    priorities = json.loads(priority_path.read_text(encoding='utf-8')) if priority_path.exists() else []
    if not isinstance(priorities, list) or any(not isinstance(x,str) for x in priorities):
        raise ValueError('Invalid priority file')
    scanner = CommandSecretScanner()
    hostname = socket.gethostname().split('.')[0].upper()
    excluded = changed = 0
    with LocalStore(db_path) as store:
        for command, count in occurrences.items():
            if '\0' in command or not scanner.scan({'command':command}).allowed:
                excluded += 1
                continue
            key = natural_key('command',command)
            row = store.connection.execute('SELECT uuid,deleted_at FROM cv_records WHERE natural_key=?', (key,)).fetchone()
            if row is not None and row['deleted_at'] is not None:
                continue
            if row:
                old = store.get(row['uuid'])
                data = dict(old.data)
                data['count'] = count
                data.setdefault('hostname',hostname)
                # Preserve local metadata until the dedicated staging pass.
                with transaction(store.connection):
                    record = store._put('command',key,data)
            else:
                with transaction(store.connection):
                    record = store._put('command',key,{'command':command,'favorite':False,'count':count,'hostname':hostname})
            if record.sync_state != SyncState.ELIGIBLE:
                store.set_sync_state(record.uuid,SyncState.ELIGIBLE,scanner=scanner)
                changed += 1
        # Don't needlessly enqueue unchanged records; staging diffs favorites/priority.
    metadata_changed = stage_approved_metadata(db_path,favorite_list,priorities)
    return {'unique':len(occurrences),'excluded':excluded,'newly_approved':changed,'metadata_changed':metadata_changed}
