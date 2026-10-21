"""Recoverable bridge to the existing favorites.json format and lock.

SQLite stores metadata alongside JSON, never replaces it. A committed intent
allows a crash between the SQLite commit and atomic JSON replacement to recover
on the next read/write. A conflicting external edit is reported, not overwritten.
Bash history is never imported or rewritten by this module.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator, List, Optional
from ..database import transaction, utc_now
from .store import LocalStore, natural_key, text_value


def parse_favorites(raw: Optional[bytes]) -> List[str]:
    if raw is None:
        return []
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data,list) or any(not isinstance(item,str) or not item.strip() or '\0' in item for item in data):
        raise ValueError('Invalid favorites file; original data was left untouched')
    return list(dict.fromkeys(data))


def snapshot_hash(raw: Optional[bytes]) -> str:
    return hashlib.sha256(b'missing' if raw is None else b'present:'+raw).hexdigest()


class FavoritesBridge:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().absolute()
        self.database_path = self.path.with_name(self.path.stem+'.sync.sqlite3')
        self.source = str(self.path)

    def _raw(self) -> Optional[bytes]:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    @contextmanager
    def _lock(self) -> Iterator[None]:
        import fcntl
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd = os.open(str(self.path)+'.lock',os.O_CREAT | os.O_RDWR,0o600)
        with os.fdopen(fd,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            yield

    @contextmanager
    def _store(self) -> Iterator[LocalStore]:
        try:
            with LocalStore(self.database_path) as store:
                yield store
        except sqlite3.Error as error:
            raise ValueError('Local sync metadata unavailable; retry to recover pending favorites changes') from error

    def _write_json(self, raw: bytes) -> None:
        fd,name = tempfile.mkstemp(prefix='.favorites-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'wb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name,self.path)
            directory = os.open(self.path.parent,os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _recover(self, store: LocalStore) -> None:
        row = store.connection.execute('SELECT * FROM cv_favorites_intent WHERE source=?',(self.source,)).fetchone()
        if row is None:
            return
        target = row['target_json'].encode('utf-8')
        parse_favorites(target)
        current = snapshot_hash(self._raw())
        if current == row['before_hash']:
            self._write_json(target)
        elif current != snapshot_hash(target):
            raise ValueError('Favorites changed during an interrupted save; pending metadata and JSON were preserved for recovery')
        with transaction(store.connection):
            store.connection.execute('DELETE FROM cv_favorites_intent WHERE source=?',(self.source,))

    def read(self) -> List[str]:
        # Merely browsing history with no favorites must not create any files.
        if not self.path.exists() and not self.database_path.exists():
            return []
        with self._lock(), self._store() as store:
            self._recover(store)
            commands = parse_favorites(self._raw())
            store.reconcile_favorites(commands)
            return commands

    def update(self, command: str, *, remove: bool = False, delete_record: bool = False) -> None:
        text_value(command)
        if delete_record and not self.path.exists() and not self.database_path.exists():
            return
        with self._lock():
            # Validate JSON before creating or migrating the sidecar.
            raw = self._raw()
            if not self.database_path.exists():
                parse_favorites(raw)
            with self._store() as store:
                self._recover(store)
                raw = self._raw()
                commands = parse_favorites(raw)
                target = [item for item in commands if item != command] if remove else list(dict.fromkeys([*commands,command]))
                target_json = json.dumps(target,ensure_ascii=False,indent=2)+'\n'
                with transaction(store.connection):
                    store._reconcile_favorites(commands)
                    store._reconcile_favorites(target)
                    if delete_record:
                        row = store.connection.execute('SELECT uuid FROM cv_records WHERE natural_key=?',
                            (natural_key('command',command),)).fetchone()
                        if row:
                            store._delete(row[0])
                    if delete_record and raw is None and not target:
                        return
                    store.connection.execute('''INSERT INTO cv_favorites_intent
                        (source,before_hash,target_json,created_at) VALUES (?,?,?,?)''',
                        (self.source,snapshot_hash(raw),target_json,utc_now()))
                self._recover(store)
