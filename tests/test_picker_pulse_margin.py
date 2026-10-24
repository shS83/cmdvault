"""Test pure picker layout functions without needing the installed visual backend."""
import ast
import pathlib
import unittest
from typing import Sequence

PICKER = pathlib.Path(__file__).resolve().parents[1] / 'src/visuals/picker.py'
source = ast.parse(PICKER.read_text())
nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in (
    'with_pulse_margin', 'pulse_label_color', 'pulse_label_packet', 'pulse_background_packet')]
code = compile(ast.Module(body=nodes, type_ignores=[]), str(PICKER), 'exec')
ns = {'Sequence': Sequence, 'PULSE_RESERVED_ROWS': 8, 'PULSE_BG': '\x1b[48;2;11;8;18m',
      'PULSE_LABEL_COLORS': {'ready': (242, 240, 246)}}
exec(code, ns)

class PickerPulseLayoutTests(unittest.TestCase):
    def test_adds_reserved_area(self):
        self.assertIn('--margin=0,0,8,0', ns['with_pulse_margin'](['fzf','--ansi'],30,100))
    def test_preserves_existing_margins(self):
        self.assertIn('--margin=1,2,8,2', ns['with_pulse_margin'](['fzf','--margin=1,2,1,2'],30,100))
    def test_expands_shorthand_without_overriding_top_or_side(self):
        self.assertEqual(['fzf', '--margin=5,1,8,1'],
                         ns['with_pulse_margin'](['fzf', '--margin=5,1'],30,100))
    def test_small_terminal_unchanged(self):
        self.assertEqual(['fzf'], ns['with_pulse_margin'](['fzf'],12,70))
    def test_label_position(self):
        self.assertIn('READY',ns['pulse_label_packet'](100,30,'READY'))
    def test_background_panel(self):
        packet = ns['pulse_background_packet'](100,30)
        self.assertIn('\x1b[23;3H',packet)
        self.assertNotIn('\x1b[30;3H',packet)
