#!/usr/bin/env python3
"""Direct Kitty Sync Pulse diagnostic, run from cmdvault project root.

Usage: PYTHONPATH=. uv run --extra graphics python probe_pulse.py
"""
import os
import shutil
import sys
import time

from src.visuals.kitty import KittyPulseOverlay
from src.visuals.config import VisualConfig


def main():
    if not sys.stdout.isatty():
        raise SystemExit('Run directly in your Kitty terminal (stdout must be a TTY).')
    columns, rows = shutil.get_terminal_size()
    pulse = KittyPulseOverlay(VisualConfig(), 1900631471)
    screen = '\x1b[?1049h\x1b[?25l\x1b[2J'
    sys.stdout.write(screen)
    sys.stdout.flush()
    try:
        for name in ('ready', 'syncing', 'synced', 'error'):
            pulse.set_state(name)
            for _ in range(12):
                # Repaint text before the picture; exercise the exact same codepath.
                sys.stdout.write(f'\x1b[2;3HKitty Sync Pulse direct probe: {name.upper():<9}')
                sys.stdout.write(pulse.packet(columns, rows, force=True))
                sys.stdout.flush()
                time.sleep(0.1)
    finally:
        sys.stdout.write(pulse.clear() + '\x1b[0m\x1b[?25h\x1b[?1049l')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
