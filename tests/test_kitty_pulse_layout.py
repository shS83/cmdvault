"""Regression checks for bottom-right reserved Kitty Sync Pulse region."""
import pathlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

from src.visuals.config import VisualConfig
from src.visuals.detector import Capabilities
from src.visuals.kitty import KittyPulseOverlay, KittyRenderer

ROOT = pathlib.Path(__file__).resolve().parents[1]

class KittyPulseLayoutTest(unittest.TestCase):
    def test_reserves_bottom_rows_and_matches_panel_color(self):
        source = (ROOT / "src/visuals/kitty.py").read_text()
        self.assertIn("reserved_rows = 6", source)
        self.assertIn("content_end = self.height - reserved_rows", source)
        self.assertIn("(11, 8, 18, 255)", source)
        self.assertIn("image.alpha_composite(orb)", source)

    def test_orb_moves_fifteen_pixels_left_and_up(self):
        overlay = KittyPulseOverlay(VisualConfig(animation=False), 123)
        with patch.object(overlay.pulse, "frame", return_value=Image.new("RGBA", (104, 104))), \
             patch("src.visuals.kitty.encode_rgba", return_value=""):
            overlay.packet(80, 24, now=100, force=True, pixel_width=640, pixel_height=384)
        col, row, ox, oy = overlay.placed_at
        self.assertEqual(((col - 1) * 8 + ox, (row - 1) * 16 + oy), (457, 201))

    def test_label_moves_to_nearest_terminal_cells(self):
        output = io.StringIO()
        renderer = KittyRenderer(output, VisualConfig(animation=False), Capabilities(True, "xterm-kitty"))
        with patch.object(renderer.decoration, "upload", return_value=""), \
             patch.object(renderer.decoration, "place", return_value=""), \
             patch.object(renderer.sync_pulse, "packet", return_value=""):
            renderer.draw([])
        self.assertIn("\x1b[18;55H", output.getvalue())

    def test_no_fixed_5x3_stretch(self):
        source = (ROOT / "src/visuals/kitty.py").read_text()
        self.assertIn("X={ox},Y={oy}", source)
        self.assertNotIn("p=1,c=5,r=3", source)
