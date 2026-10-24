import json
import tempfile
import unittest
from pathlib import Path
from src.sync.trusted_history import prepare
from src.sync.store import LocalStore
from src.models import SyncState

class TrustedHistoryTests(unittest.TestCase):
    def test_stages_and_is_idempotent_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            db=root/'records.sqlite3'
            with LocalStore(db): pass
            history=root/'history'
            content='#1720000000\necho safe\n#1720000001\necho safe\n#1720000002\necho other\n'
            history.write_text(content)
            favorites=root/'favorites.json';favorites.write_text(json.dumps(['echo safe']))
            priority=root/'priority.json';priority.write_text(json.dumps(['echo safe']))
            first=prepare(db,history,favorites,priority)
            second=prepare(db,history,favorites,priority)
            with LocalStore(db) as store:
                records=store.records('command')
                self.assertEqual(len(records),2)
                safe=next(r for r in records if r.data['command']=='echo safe')
                self.assertEqual(safe.data['count'],2)
                self.assertEqual(safe.data['priority'],0)
                self.assertTrue(safe.data['favorite'])
                self.assertEqual(safe.sync_state,SyncState.ELIGIBLE)
                n=store.connection.execute('SELECT COUNT(*) FROM cv_outbox').fetchone()[0]
                self.assertEqual(n,2)
            self.assertEqual(first['newly_approved'],2)
            self.assertEqual(second['newly_approved'],0)
            self.assertEqual(history.read_text(),content)

    def test_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'db.sqlite3'
            with LocalStore(db):pass
            history=root/'hist';history.write_text('echo good\ncurl -H "Authorization: Bearer abcdefghijklmnop" example.org\n')
            favorites=root/'favorites.json';priority=root/'priority.json'
            result=prepare(db,history,favorites,priority)
            self.assertEqual(result['excluded'],1)
            with LocalStore(db) as store:
                self.assertEqual(len(store.records('command')),1)

if __name__=='__main__':unittest.main()
