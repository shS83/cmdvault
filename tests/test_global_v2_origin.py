import json
from pathlib import Path
import tempfile
import unittest
from src.sync.global_history import remote_metadata
from src.sync.store import LocalStore
from src.sync.two_way import setup

class OriginTests(unittest.TestCase):
    def test_legacy_origin_and_multihost_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'db.sqlite'
            with LocalStore(db) as store:
                setup(store.connection)
                for uid, seq, fields in [
                    ('a', 1, {'command': 'echo shared', 'favorite': False, 'count': 4}),
                    ('b', 2, {'command': 'echo shared', 'favorite': True, 'count': 6, 'hostname': 'EPSILONTAURI'}),
                    ('c', 3, {'command': 'echo shared', 'count': 2, 'hostname': 'EPSILONTAURI'}),
                ]:
                    raw = json.dumps({'kind':'command','data':fields})
                    store.connection.execute('INSERT INTO cv_remote_inbox VALUES (?,?,?,?,?,?)', (uid,1,seq,'upsert',raw,'now'))
            record=remote_metadata(db)['echo shared']
            self.assertEqual(record['hosts'],('ALPHATAURI','EPSILONTAURI'))
            self.assertEqual(record['count'],10)
            self.assertTrue(record['favorite'])

    def test_delete_does_not_resurrect(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db.sqlite'
            with LocalStore(db) as store:
                setup(store.connection)
                raw=json.dumps({'kind':'command','data':{'command':'echo gone'}})
                store.connection.execute('INSERT INTO cv_remote_inbox VALUES (?,?,?,?,?,?)', ('x',1,1,'delete',None,'now'))
            self.assertNotIn('echo gone', remote_metadata(db))
