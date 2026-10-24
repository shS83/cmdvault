"""Regression checks for bottom-right reserved Kitty Sync Pulse region."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class KittyPulseLayoutTest(unittest.TestCase):
    def test_reserves_bottom_rows_and_matches_panel_color(self):
        source = (ROOT / "src/visuals/kitty.py").read_text()
        self.assertIn("reserved_rows = 6", source)
        self.assertIn("content_end = self.height - reserved_rows", source)
        self.assertIn("(11, 8, 18, 255)", source)
        self.assertIn("image.alpha_composite(orb)", source)

    def test_pixel_offset_and_no_fixed_5x3_stretch(self):
        source = (ROOT / "src/visuals/kitty.py").read_text()
        self.assertIn("margin = 32", source)
        self.assertIn("X={ox},Y={oy}", source)
        self.assertNotIn("p=1,c=5,r=3", source)
