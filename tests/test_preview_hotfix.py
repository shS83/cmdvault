import json
import tempfile
import unittest
from pathlib import Path
from src.visuals.command_preview import preview

class PreviewTests(unittest.TestCase):
    def test_row_with_tabs_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'commands.json'
            path.write_text(json.dumps(['short', 'printf "%s" "a\tb"', 'äää'], ensure_ascii=False), encoding='utf-8')
            self.assertEqual(preview(str(path), '1\t★  2x  printf'), 'printf "%s" "a\tb"')
            self.assertEqual(preview(str(path), '2\täää'), 'äää')
    def test_invalid_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'commands.json'
            path.write_text('[]', encoding='utf-8')
            with self.assertRaises(ValueError):
                preview(str(path), 'no-tab')
