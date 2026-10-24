"""Read-only GLOBAL projection of explicitly approved sync records.

Old server records without source metadata came from ALPHATAURI.
Never writes local Bash history or favorites files.
"""
import json
from pathlib import Path
import sqlite3

LEGACY_HOST = "ALPHATAURI"


def remote_metadata(db_path):
    path = Path(db_path)
    if not path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
            inbox = db.execute("SELECT record_uuid,operation,plaintext_json,sequence FROM cv_remote_inbox ORDER BY sequence").fetchall()
            local = db.execute("SELECT uuid,data FROM cv_records WHERE kind='command' AND deleted_at IS NULL AND sync_state='sync_eligible'").fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError(f"GLOBAL SQLite read failed: {error}") from error

    latest = {}
    for uuid, raw in local:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                latest[uuid] = data
        except (ValueError, TypeError):
            pass
    for uuid, operation, raw, _ in inbox:
        if operation == 'delete':
            latest.pop(uuid, None)
        elif operation == 'upsert' and raw:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and obj.get('kind') == 'command' and isinstance(obj.get('data'), dict):
                    latest[uuid] = obj['data']
            except (ValueError, TypeError):
                pass

    result = {}
    for data in latest.values():
        command = data.get('command')
        if not isinstance(command, str) or not command.strip() or '\0' in command:
            continue
        host = data.get('hostname') or LEGACY_HOST
        if not isinstance(host, str) or len(host) > 80 or not host.strip() or any(ch in host for ch in '\r\n\t\x1b'):
            host = 'UNKNOWN'
        host = host.upper()
        item = result.setdefault(command, {'favorite': False, 'priority': None, 'count': 0, 'hosts': set(), '_counts': {}})
        item['hosts'].add(host)
        item['favorite'] |= data.get('favorite') is True
        priority = data.get('priority')
        if type(priority) is int and priority >= 0:
            item['priority'] = priority if item['priority'] is None else min(item['priority'], priority)
            item['favorite'] = True
        n = data.get('count')
        n = n if type(n) is int and 0 < n <= 100000000 else 1
        item['_counts'][host] = max(item['_counts'].get(host, 0), n)
    for item in result.values():
        item['count'] = sum(item.pop('_counts').values())
        item['hosts'] = tuple(sorted(item['hosts']))
    return result


def remote_commands(db_path):
    return list(remote_metadata(db_path))
