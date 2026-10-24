import ast
import importlib.util
from pathlib import Path
import unittest

root = Path(__file__).parent
spec = importlib.util.spec_from_file_location('cvpatch', root / 'apply.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class TestPatch(unittest.TestCase):
    def test_source_transforms(self):
        fixture = Path(__file__).parent / 'launcher-fixture.txt'
        source = fixture.read_text()
        output = patch.transform(source)
        ast.parse(output)
        self.assertIn('"--multi", "--bind=space:toggle"', output)
        self.assertIn('selected_rows = lines[2:]', output)
        self.assertIn('for selected_index in indices:', output)

    def test_selection_indices(self):
        fixture = (root / 'launcher-fixture.txt').read_text()
        tree = ast.parse(patch.transform(fixture))
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_cv_selection_indices')
        namespace = {}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), '<helper>', 'exec'), namespace)
        self.assertEqual(namespace['_cv_selection_indices'](['2\trow', '0\trow', '2\trow'], [1,2,3]), [2,0])
        with self.assertRaises(ValueError):
            namespace['_cv_selection_indices'](['100\trow'], [1,2,3])


if __name__ == '__main__':
    unittest.main()
