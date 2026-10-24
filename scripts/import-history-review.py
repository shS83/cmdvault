#!/usr/bin/env python3
"""Opt-in local history review; never performs network operations."""
import argparse
import os
from pathlib import Path
import re
import sys

from src.models import SyncState
from src.security.secrets import CommandSecretScanner
from src.sync.store import LocalStore, natural_key


def history_commands(text):
    """Match cmdvault's timestamp-aware Bash history parser."""
    pending = None
    for line in text.splitlines():
        if re.fullmatch(r'#[0-9]+', line):
            if pending is not None:
                command = '\n'.join(pending)
                if command.strip():
                    yield command
            pending = []
        elif pending is not None:
            pending.append(line)
        elif line.strip():
            yield line
    if pending is not None:
        command = '\n'.join(pending)
        if command.strip():
            yield command


def collect(paths):
    scanner = CommandSecretScanner()
    allowed, excluded = {}, 0
    for path in paths:
        raw = path.read_text(encoding='utf-8', errors='replace')
        for command in history_commands(raw):
            if '\0' in command or not command.strip():
                excluded += 1
                continue
            if not scanner.scan({'command': command}).allowed:
                excluded += 1
                continue
            allowed[command] = allowed.get(command, 0) + 1
    return allowed, excluded


def selections(value, n):
    value = value.strip().lower()
    if not value:
        return []
    if value == 'all':
        return list(range(n))
    result = set()
    for part in value.split(','):
        part = part.strip()
        if not part.isdecimal() or not 1 <= int(part) <= n:
            raise ValueError('Choose comma-separated numbers shown above, or all')
        result.add(int(part) - 1)
    return sorted(result)


def approve(store, commands):
    scanner = CommandSecretScanner()
    approved = 0
    for command in commands:
        # Protect existing favorites and never resurrect a local tombstone.
        key = natural_key('command', command)
        row = store.connection.execute('SELECT uuid, deleted_at FROM cv_records WHERE natural_key=?', (key,)).fetchone()
        if row is not None and row['deleted_at'] is not None:
            continue
        if row is None:
            record = store.put_command(command, favorite=False)
        else:
            record = store.get(row['uuid'])
        # Recheck exactly what we are about to approve.
        if not scanner.scan(record.data).allowed:
            continue
        if record.sync_state != SyncState.ELIGIBLE:
            store.set_sync_state(record.uuid, SyncState.ELIGIBLE, scanner=scanner)
            approved += 1
    return approved


def main(argv=None, *, input_fn=input, output=sys.stdout):
    parser = argparse.ArgumentParser(description='Review local Bash commands before E2EE sync eligibility')
    parser.add_argument('--history', type=Path, action='append', help='Bash history file (repeatable)')
    parser.add_argument('--db', type=Path, help='Local SQLite sidecar; defaults to favorites.sync.sqlite3')
    parser.add_argument('--review', action='store_true', help='Review locally; default action')
    args = parser.parse_args(argv)
    root = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    db = args.db or root / 'cmdvault/favorites.sync.sqlite3'
    paths = args.history or [Path(os.environ.get('HISTFILE') or Path.home() / '.bash_history')]
    for path in paths:
        if not path.is_file():
            parser.error(f'History file does not exist: {path}')
    candidates, excluded = collect(paths)
    print(f'Unique eligible candidates: {len(candidates)}; excluded occurrences: {excluded}', file=output)
    print('Local-only review. No network operation; no Bash history or favorites.json writes.', file=output)
    if not candidates:
        return 0
    items = sorted(candidates)
    for i, command in enumerate(items, 1):
        display = command.replace('\n', '\\n')
        print(f'{i:>4}: ({candidates[command]} uses) {display}', file=output)
    if not sys.stdin.isatty() and input_fn is input:
        print('Interactive terminal required for approval; no changes made.', file=output)
        return 0
    selected = selections(input_fn('Select indices (comma-separated), all, or Enter to cancel: '), len(items))
    if not selected:
        print('Cancelled; no changes made.', file=output)
        return 0
    print(f'Selected {len(selected)} commands. This marks them eligible for a FUTURE encrypted push.', file=output)
    if input_fn('Type APPROVE to commit locally: ') != 'APPROVE':
        print('Cancelled; no changes made.', file=output)
        return 0
    # Database is touched only after explicit approval.
    with LocalStore(db) as store:
        count = approve(store, [items[i] for i in selected])
    print(f'Marked {count} records sync_eligible in {db}. No network operation was performed.', file=output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
