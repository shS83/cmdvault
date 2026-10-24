"""Structural regression checks that need no real Kitty terminal."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KittyPulseIntegrationTests(unittest.TestCase):
    def test_visual_state_api_and_cleanup(self):
        source = (ROOT / 'src/visuals/kitty.py').read_text()
        self.assertIn('def set_sync_state(self, state):', source)
        self.assertIn('self.sync_pulse.clear()', source)
        self.assertIn('self.sync_pulse.packet(self.width, self.height,', source)


if __name__ == '__main__':
    unittest.main()
