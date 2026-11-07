"""Bounded terminal recovery, including interrupted graphics control strings."""
import fcntl
import os
import select
import termios
import time

# CAN + ST abandon a partial SIXEL/Kitty packet before sending text controls.
RESET_TERMINAL = ('\x18\x1b\\\x1b[?2026l\x1b[?1000l\x1b[?1002l\x1b[?1003l'
                  '\x1b[?1004l\x1b[?1006l\x1b[?1015l\x1b[?2004l'
                  '\x1b[>4;0m\x1b[<u\x1b[?1l\x1b>\x1b[?7h\x1b[0m'
                  '\x1b[?25h\x1b[?1049l')


def restore_terminal(fd, original, graphics_cleanup=''):
    # Restore keyboard handling first, even if child/thread teardown fails.
    termios.tcsetattr(fd, termios.TCSANOW, original)
    termios.tcflow(fd, termios.TCOON)
    # Drop queued graphics so recovery controls cannot sit behind stale frames.
    termios.tcflush(fd, termios.TCOFLUSH)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        # Reset the parser before optional image deletion packets, not after.
        data = memoryview((RESET_TERMINAL + graphics_cleanup).encode())
        deadline = time.monotonic() + .25
        while data and time.monotonic() < deadline:
            try:
                data = data[os.write(fd, data):]
            except BlockingIOError:
                select.select([], [fd], [], max(0, deadline-time.monotonic()))
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
