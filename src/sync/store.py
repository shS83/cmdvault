"""Transactional local records, tag relationships and sync eligibility.

Every mutation and its latest outbox event commit together. Public operations
are offline and default to local-only; no history file is ingested here.
"""
import hashlib
import json
from pathlib import Path
from typing import Iterable, List, Optional, Union
from ..database import connect, transaction, utc_now
from ..models import Record, SyncState, new_uuid, validate_uuid
from ..security.secrets import SecretScanner
from .devices import SyncStateStore
from .queue import Outbox


def text_value(value: str) -> str:
    if not isinstance(value,str) or not value.strip() or '\0' in value:
        raise ValueError('Expected nonempty text without NUL characters')
    return value


def natural_key(kind: str, value: str) -> str:
    return hashlib.sha256((kind+'\0'+value).encode('utf-8')).hexdigest()


class LocalStore:
    def __init__(self, path: Union[str, Path]):
        self.connection = connect(path)
        self.outbox = Outbox(self.connection)
        self.state = SyncStateStore(self.connection)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, record_uuid: str) -> Record:
        validate_uuid(record_uuid)
        row = self.connection.execute('SELECT * FROM cv_records WHERE uuid=?',(record_uuid,)).fetchone()
        if row is None:
            raise KeyError(record_uuid)
        return Record(row['uuid'],row['kind'],json.loads(row['data']) if row['data'] else None,
                      SyncState(row['sync_state']),row['revision'],row['deleted_at'],
                      row['created_at'],row['updated_at'])

    def records(self, kind: Optional[str] = None, *, include_deleted: bool = False) -> List[Record]:
        sql,params = 'SELECT uuid FROM cv_records WHERE 1=1',[]
        if kind is not None:
            sql += ' AND kind=?'
            params.append(kind)
        if not include_deleted:
            sql += ' AND deleted_at IS NULL'
        return [self.get(row[0]) for row in self.connection.execute(sql+' ORDER BY id',params)]

    def _active(self, record_uuid: str, kind: Optional[str] = None) -> Record:
        record = self.get(record_uuid)
        if record.deleted or (kind is not None and record.kind != kind):
            raise ValueError('Expected an active record of the requested kind')
        return record

    def _queue_change(self, record_uuid: str) -> None:
        record = self.get(record_uuid)
        queued = self.connection.execute('SELECT 1 FROM cv_outbox WHERE record_uuid=?',(record_uuid,)).fetchone()
        if record.sync_state == SyncState.ELIGIBLE or queued:
            operation = 'delete' if record.deleted or record.sync_state == SyncState.LOCAL_ONLY else 'upsert'
            self.outbox.enqueue(record_uuid,record.revision,operation)

    def _put(self, kind: str, key: str, data: dict) -> Record:
        row = self.connection.execute('SELECT uuid FROM cv_records WHERE natural_key=?',(key,)).fetchone()
        payload = json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        now = utc_now()
        if row:
            record = self._active(row[0],kind)
            if record.data == data:
                return record
            self.connection.execute('''UPDATE cv_records SET data=?,revision=revision+1,
                updated_at=? WHERE uuid=?''',(payload,now,record.uuid))
            self._queue_change(record.uuid)
            return self.get(record.uuid)
        record_uuid = new_uuid()
        self.connection.execute('''INSERT INTO cv_records
            (kind,natural_key,data,created_at,updated_at,uuid) VALUES (?,?,?,?,?,?)''',
            (kind,key,payload,now,now,record_uuid))
        return self.get(record_uuid)

    def put_command(self, command: str, *, favorite: bool = True) -> Record:
        text_value(command)
        if type(favorite) is not bool:
            raise ValueError('favorite must be boolean')
        with transaction(self.connection):
            return self._put('command',natural_key('command',command),{'command':command,'favorite':favorite})

    def edit_command(self, record_uuid: str, command: str) -> Record:
        text_value(command)
        with transaction(self.connection):
            record = self._active(record_uuid,'command')
            if record.data['command'] == command:
                return record
            data = dict(record.data,command=command)
            self.connection.execute('''UPDATE cv_records SET natural_key=?,data=?,
                revision=revision+1,updated_at=? WHERE uuid=?''',
                (natural_key('command',command),json.dumps(data,ensure_ascii=False),utc_now(),record_uuid))
            self._queue_change(record_uuid)
            return self.get(record_uuid)

    def put_tag(self, name: str) -> Record:
        text_value(name)
        with transaction(self.connection):
            return self._put('tag',natural_key('tag',name),{'name':name})

    def link_tag(self, command_uuid: str, tag_uuid: str) -> Record:
        with transaction(self.connection):
            self._active(command_uuid,'command')
            self._active(tag_uuid,'tag')
            record = self._put('command_tag',natural_key('command_tag',command_uuid+':'+tag_uuid),
                               {'command_uuid':command_uuid,'tag_uuid':tag_uuid})
            self.connection.execute('''INSERT OR IGNORE INTO cv_links
                (uuid,command_uuid,tag_uuid) VALUES (?,?,?)''',(record.uuid,command_uuid,tag_uuid))
            return record

    def _withdraw_links(self, record_uuid: str) -> None:
        rows = self.connection.execute('''SELECT r.uuid FROM cv_links l JOIN cv_records r ON l.uuid=r.uuid
            WHERE (l.command_uuid=? OR l.tag_uuid=?) AND r.deleted_at IS NULL
            AND r.sync_state='sync_eligible' ''',(record_uuid,record_uuid)).fetchall()
        for row in rows:
            self._change_state(row[0],SyncState.LOCAL_ONLY)

    def _change_state(self, record_uuid: str, state: SyncState) -> Record:
        record = self._active(record_uuid)
        if record.sync_state == state:
            return record
        if state == SyncState.LOCAL_ONLY:
            self._withdraw_links(record_uuid)
        self.connection.execute('''UPDATE cv_records SET sync_state=?,revision=revision+1,
            updated_at=? WHERE uuid=?''',(state.value,utc_now(),record_uuid))
        # Withdrawal also needs a delete when the previous upsert was already acknowledged.
        if state == SyncState.LOCAL_ONLY:
            self.outbox.enqueue(record_uuid,self.get(record_uuid).revision,'delete')
        else:
            self._queue_change(record_uuid)
        return self.get(record_uuid)

    def set_sync_state(self, record_uuid: str, state: SyncState,
                       *, scanner: Optional[SecretScanner] = None) -> Record:
        state = SyncState(state)
        with transaction(self.connection):
            record = self._active(record_uuid)
            if state == SyncState.ELIGIBLE:
                if scanner is None:
                    raise ValueError('An explicit secret scanner is required for sync eligibility')
                result = scanner.scan(record.data)
                if result.allowed is not True or result.findings:
                    raise ValueError('Record was not approved for synchronization')
                if record.kind == 'command_tag':
                    for key in ('command_uuid','tag_uuid'):
                        if self._active(record.data[key]).sync_state != SyncState.ELIGIBLE:
                            raise ValueError('Both tag-link endpoints must be sync eligible')
            return self._change_state(record_uuid,state)

    def _delete(self, record_uuid: str) -> Record:
        record = self.get(record_uuid)
        if record.deleted:
            return record
        # Links are tombstoned first so a future client can send child deletes
        # before their parent. UUIDs remain; command/tag payloads do not.
        rows = self.connection.execute('''SELECT l.uuid FROM cv_links l JOIN cv_records r ON r.uuid=l.uuid
            WHERE (l.command_uuid=? OR l.tag_uuid=?) AND r.deleted_at IS NULL''',
            (record_uuid,record_uuid)).fetchall()
        for row in rows:
            self._delete(row[0])
        now = utc_now()
        self.connection.execute('''UPDATE cv_records SET data=NULL,natural_key=NULL,
            deleted_at=?,updated_at=?,revision=revision+1 WHERE uuid=?''',(now,now,record_uuid))
        self._queue_change(record_uuid)
        return self.get(record_uuid)

    def delete(self, record_uuid: str) -> Record:
        with transaction(self.connection):
            return self._delete(record_uuid)

    def _reconcile_favorites(self, commands: Iterable[str]) -> None:
        commands = list(dict.fromkeys(text_value(command) for command in commands))
        wanted = set(commands)
        for record in self.records('command'):
            if record.data['favorite'] and record.data['command'] not in wanted:
                linked = self.connection.execute('''SELECT 1 FROM cv_links l JOIN cv_records r ON r.uuid=l.uuid
                    WHERE l.command_uuid=? AND r.deleted_at IS NULL''',(record.uuid,)).fetchone()
                if linked:
                    self._put('command',natural_key('command',record.data['command']),
                              dict(record.data,favorite=False))
                else:
                    self._delete(record.uuid)
        for command in commands:
            self._put('command',natural_key('command',command),{'command':command,'favorite':True})

    def reconcile_favorites(self, commands: Iterable[str]) -> None:
        with transaction(self.connection):
            self._reconcile_favorites(commands)
