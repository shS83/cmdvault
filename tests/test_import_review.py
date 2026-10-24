import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

from src.models import SyncState
from src.sync.store import LocalStore

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/import-history-review.py'
spec = importlib.util.spec_from_file_location('import_history_review', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ImportReviewTests(unittest.TestCase):
    def test_multiline_and_repeated_commands(self):
        self.assertEqual(list(module.history_commands('#123\ngit status\n#124\ngit status\n#125\necho test\ncontinued')),
                         ['git status','git status','echo test\ncontinued'])

    def test_rejects_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'hist'
            p.write_text('git status\nexport API_KEY=abcdefghijklmnop\n')
            safe, excluded = module.collect([p])
            self.assertEqual(safe, {'git status':1})
            self.assertEqual(excluded, 1)

    def test_preview_creates_no_database(self):
        with tempfile.TemporaryDirectory() as directory:
            p, db = Path(directory)/'hist', Path(directory)/'test.sqlite3'
            p.write_text('git status\n')
            out = io.StringIO()
            answers = iter([''])
            module.main(['--history',str(p),'--db',str(db)], input_fn=lambda prompt: next(answers), output=out)
            self.assertFalse(db.exists())

    def test_only_approved_items_eligible_and_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'test.sqlite3'
            with LocalStore(db) as store:
                store.put_command('git status', favorite=True)
                self.assertEqual(module.approve(store,['git status','ls']),2)
                self.assertEqual(module.approve(store,['git status','ls']),0)
                records = store.records('command')
                self.assertEqual(len(records),2)
                self.assertTrue(all(r.sync_state == SyncState.ELIGIBLE for r in records))
                self.assertTrue(next(r for r in records if r.data['command']=='git status').data['favorite'])

    def test_selection_parser(self):
        self.assertEqual(module.selections('3,1,3',3),[0,2])
        with self.assertRaises(ValueError):
            module.selections('4',3)

if __name__ == '__main__':
    unittest.main()
