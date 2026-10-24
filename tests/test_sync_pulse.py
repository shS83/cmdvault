import unittest
from src.visuals.sync_pulse import SyncPulse, SyncState, PulseStyle, intensity


class PulseTests(unittest.TestCase):
    def test_labels(self):
        pulse = SyncPulse()
        for state in SyncState:
            pulse.set_state(state)
            self.assertEqual(pulse.label, state.value.upper())

    def test_ranges(self):
        for state in SyncState:
            for t in [0, .1, .5, 1, 10]:
                self.assertLessEqual(intensity(state, t), 1)
                self.assertGreaterEqual(intensity(state, t), 0)

    def test_settings(self):
        with self.assertRaises(ValueError):
            PulseStyle(glow=1.1)

    def test_image(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("graphics extra not installed")
        pulse = SyncPulse(PulseStyle(size=64), SyncState.SYNCING)
        img = pulse.frame()
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, "RGBA")
        self.assertTrue(pulse.frame_png().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
