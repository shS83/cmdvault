import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from src.models import SyncState
from src.security.secrets import CommandSecretScanner
from src.sync.store import LocalStore
from src.sync.two_way import TwoWaySync


class FakeAPI:
    def __init__(self):
        self.records = {}
        self.entries = []
        self.receipts = {}

    def status(self):
        return {'sync_enabled': True}

    def push(self, mutations):
        receipts = []
        for item in mutations:
            old = self.receipts.get(item['change_id'])
            if old:
                receipts.append(old)
                continue
            current = self.records.get(item['record_uuid'], 0)
            if current != item['base_revision']:
                raise ValueError('revision conflict')
            rev, seq = current + 1, len(self.entries) + 1
            self.records[item['record_uuid']] = rev
            self.entries.append(dict(item, revision=rev, sequence=seq, timestamp='2026-10-24T12:00:00Z'))
            receipt = {'change_id':item['change_id'], 'record_uuid':item['record_uuid'], 'revision':rev, 'sequence':seq}
            self.receipts[item['change_id']] = receipt
            receipts.append(receipt)
        return receipts

    def pull(self, cursor=None, limit=100):
        pos = int(cursor or 0)
        changes = self.entries[pos:pos+limit]
        return {'schema_version': 1, 'changes': changes, 'cursor': str(pos + len(changes)), 'has_more': pos + len(changes) < len(self.entries)}


class TwoWayTests(unittest.TestCase):
    def test_approved_encrypted_round_trip_and_no_automatic_import(self):
        with tempfile.TemporaryDirectory() as td:
            api, key = FakeAPI(), os.urandom(32)
            with LocalStore(Path(td)/'a.db') as a, LocalStore(Path(td)/'b.db') as b:
                record = a.put_command('echo healthy', favorite=True)
                a.set_sync_state(record.uuid, SyncState.ELIGIBLE, scanner=CommandSecretScanner())
                sender = TwoWaySync(a, api, key)
                self.assertEqual(sender.push(), 1)
                self.assertNotIn('echo healthy', str(api.entries))
                receiver = TwoWaySync(b, api, key)
                self.assertEqual(receiver.pull(), 1)
                row = b.connection.execute('SELECT plaintext_json FROM cv_remote_inbox').fetchone()
                self.assertIn('echo healthy', row[0])
                self.assertFalse(b.records())  # inbox is not silently promoted
                self.assertEqual(receiver.pull(), 0)
                self.assertEqual(sender.push(), 0)

    def test_no_unapproved_upload(self):
        with tempfile.TemporaryDirectory() as td:
            api, key = FakeAPI(), os.urandom(32)
            with LocalStore(Path(td)/'a.db') as a:
                a.put_command('echo locally private')
                self.assertEqual(TwoWaySync(a, api, key).push(), 0)
                self.assertEqual(api.entries, [])

    def test_resume_after_response_loss_uses_same_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            api, key = FakeAPI(), os.urandom(32)
            with LocalStore(Path(td)/'a.db') as a:
                record = a.put_command('echo hello')
                a.set_sync_state(record.uuid, SyncState.ELIGIBLE, scanner=CommandSecretScanner())
                class LostResponse:
                    def __init__(self): self.once=True
                    def push(self, changes):
                        result = api.push(changes)
                        if self.once:
                            self.once=False
                            raise OSError('lost response')
                        return result
                lost = LostResponse()
                sync = TwoWaySync(a, lost, key)
                with self.assertRaises(OSError): sync.push()
                staged = a.connection.execute('SELECT request_json FROM cv_sync_requests').fetchone()[0]
                self.assertEqual(sync.push(), 1)
                self.assertEqual(len(api.entries), 1)
                self.assertIn('ciphertext', staged)

if __name__ == '__main__': unittest.main()
