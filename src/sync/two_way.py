"""Explicit encrypted Push/Pull for approved records, with durable staging.

Pulled changes stay in a local inbox until a reviewed merge is implemented.
No automatic shell-history or favorites import occurs.
"""
import json
from ..database import transaction, utc_now
from ..models import SyncState
from ..security.crypto import seal_record, open_record
from ..security.secrets import CommandSecretScanner


class SyncConflict(Exception):
    pass


def setup(connection):
    with transaction(connection):
        connection.execute('''CREATE TABLE IF NOT EXISTS cv_remote_revisions (
            record_uuid TEXT PRIMARY KEY,
            remote_revision INTEGER NOT NULL,
            sequence INTEGER NOT NULL)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS cv_sync_requests (
            event_id TEXT PRIMARY KEY, request_json TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS cv_remote_inbox (
            record_uuid TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            sequence INTEGER NOT NULL, operation TEXT NOT NULL,
            plaintext_json TEXT, received_at TEXT NOT NULL)''')


class TwoWaySync:
    def __init__(self, store, api, key):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError('Expected a 32-byte E2EE key')
        self.store, self.api, self.key = store, api, key
        setup(store.connection)

    def revision(self, uuid):
        row = self.store.connection.execute(
            'SELECT remote_revision FROM cv_remote_revisions WHERE record_uuid=?',
            (uuid,)).fetchone()
        return row[0] if row else 0

    def _revision_update(self, uuid, revision, sequence):
        self.store.connection.execute('''INSERT INTO cv_remote_revisions VALUES (?,?,?)
            ON CONFLICT(record_uuid) DO UPDATE SET remote_revision=excluded.remote_revision,
            sequence=excluded.sequence WHERE excluded.remote_revision > cv_remote_revisions.remote_revision''',
            (uuid, revision, sequence))

    def pull(self, limit=100):
        cursor = self.store.state.cursor()
        received = 0
        while True:
            page = self.api.pull(cursor, limit=limit)
            decoded = []
            for entry in page['changes']:
                raw = None
                if entry['operation'] == 'upsert':
                    raw = open_record(self.key, entry['envelope'], entry['record_uuid'],
                                      entry['change_id'], entry['revision'])
                    obj = json.loads(raw)
                    if obj == {'kind': 'synthetic', 'command': 'echo CMDVAULT_SYNC_TEST'}:
                        decoded.append((entry, None, True))
                        continue
                    if not isinstance(obj, dict) or set(obj) != {'kind', 'data'} or obj['kind'] not in ('command', 'tag', 'command_tag') or not isinstance(obj['data'], dict):
                        raise ValueError('Invalid decrypted payload')
                    raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
                decoded.append((entry, raw, False))
            with transaction(self.store.connection):
                for entry, raw, synthetic in decoded:
                    if entry['revision'] <= self.revision(entry['record_uuid']):
                        continue
                    if synthetic:
                        self._revision_update(entry['record_uuid'], entry['revision'], entry['sequence'])
                        continue
                    self.store.connection.execute('''INSERT INTO cv_remote_inbox VALUES (?,?,?,?,?,?)
                        ON CONFLICT(record_uuid) DO UPDATE SET revision=excluded.revision,
                        sequence=excluded.sequence,operation=excluded.operation,
                        plaintext_json=excluded.plaintext_json,received_at=excluded.received_at''',
                        (entry['record_uuid'],entry['revision'],entry['sequence'],entry['operation'],raw,utc_now()))
                    self._revision_update(entry['record_uuid'],entry['revision'],entry['sequence'])
                    received += 1
                self.store.connection.execute('''INSERT INTO cv_sync_state(key,value)
                    VALUES ('cursor:default',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
                    (page['cursor'],))
            cursor = page['cursor']
            if not page['has_more']:
                break
            if not page['changes']:
                raise ValueError('Empty continuing change page')
        return received

    def push(self, limit=20):
        if not 1 <= limit <= 20:
            raise ValueError('Invalid batch size')
        sent = 0
        for event in self.store.outbox.pending(limit):
            record = self.store.get(event.record_uuid)
            if record.revision != event.revision:
                continue
            if event.operation == 'upsert' and (record.deleted or record.sync_state != SyncState.ELIGIBLE):
                continue
            existing = self.store.connection.execute(
                'SELECT request_json FROM cv_sync_requests WHERE event_id=?',
                (event.event_id,)).fetchone()
            base_revision = self.revision(record.uuid)
            if existing:
                mutation = json.loads(existing[0])
                if mutation['base_revision'] != base_revision:
                    raise SyncConflict('remote_revision_changed')
            else:
                if event.operation == 'delete' and base_revision == 0:
                    # No remote record was ever created: no tombstone needed.
                    self.store.outbox.acknowledge(event.event_id)
                    continue
                envelope = None
                if event.operation == 'upsert':
                    scan = CommandSecretScanner().scan(record.data)
                    if scan.allowed is not True or scan.findings:
                        raise ValueError('Unapproved record in upload queue')
                    raw = json.dumps({'kind': record.kind, 'data': record.data},
                        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                    envelope = seal_record(self.key, raw, record.uuid, event.event_id, base_revision)
                mutation = {'change_id':event.event_id, 'record_uuid':record.uuid,
                    'base_revision':base_revision, 'operation':event.operation,
                    'envelope':envelope}
                with transaction(self.store.connection):
                    self.store.connection.execute('INSERT INTO cv_sync_requests VALUES (?,?)',
                        (event.event_id, json.dumps(mutation, sort_keys=True, separators=(',', ':'))))
            # Network happens outside SQLite transaction. The staged envelope
            # survives timeouts so server idempotency remains valid.
            receipt = self.api.push([mutation])[0]
            with transaction(self.store.connection):
                self._revision_update(record.uuid, receipt['revision'], receipt['sequence'])
                self.store.connection.execute('DELETE FROM cv_outbox WHERE event_id=?', (event.event_id,))
                self.store.connection.execute('DELETE FROM cv_sync_requests WHERE event_id=?', (event.event_id,))
            sent += 1
        return sent

    def run(self):
        if not self.api.status()['sync_enabled']:
            raise ValueError('Server synchronization disabled')
        received = self.pull()
        sent = self.push()
        return {'pushed': sent, 'received': received, 'pending_review': self.store.connection.execute(
            'SELECT count(*) FROM cv_remote_inbox').fetchone()[0]}
