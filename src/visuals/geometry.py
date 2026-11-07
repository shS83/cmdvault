"""Read terminal pixel reports without passing their bytes to fzf search."""
import re
import time

REPORT = re.compile(rb'\x1b\[(4|6);([0-9]+);([0-9]+)t')
PARTIAL = re.compile(rb'\x1b(?:\[(?:[46](?:;[0-9]*(?:;[0-9]*)?)?)?)?$')
PIXEL_QUERY = '\x1b[14t\x1b[16t'


class PixelReports:
    def __init__(self):
        self.cell = None
        self.window = None
        self.pending = b''
        self.since = 0

    def feed(self, data):
        data = self.pending + data
        self.pending = b''
        def report(match):
            height, width = int(match[2]), int(match[3])
            if 0 < height <= 32768 and 0 < width <= 32768:
                if match[1] == b'6':
                    self.cell = (width, height)
                else:
                    self.window = (width, height)
            return b''
        data = REPORT.sub(report, data)
        partial = PARTIAL.search(data)
        if partial:
            self.pending = partial[0]
            self.since = time.monotonic()
            data = data[:partial.start()]
        return data

    def expired(self):
        return bool(self.pending) and time.monotonic() - self.since >= .15

    def flush(self):
        data, self.pending = self.pending, b''
        return data

    def pixels(self, columns, rows, xp=0, yp=0):
        if self.cell:
            return columns * self.cell[0], rows * self.cell[1]
        return self.window or (xp or columns * 8, yp or rows * 16)


def diagnostic_pixels(output, columns, rows):
    """Bounded pixel query for the dedicated, noninteractive graphics test."""
    import os
    import select
    import termios
    import tty
    try:
        fd = output.fileno()
        if not os.isatty(fd):
            return 0, 0
    except (AttributeError, OSError, ValueError):
        return 0, 0
    terminal = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    original = termios.tcgetattr(terminal)
    reports = PixelReports()
    try:
        tty.setcbreak(terminal)
        os.write(terminal, PIXEL_QUERY.encode())
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline:
            if not select.select([terminal], [], [], max(0, deadline-time.monotonic()))[0]:
                break
            reports.feed(os.read(terminal, 4096))
            if reports.cell:
                break
    finally:
        termios.tcsetattr(terminal, termios.TCSANOW, original)
        os.close(terminal)
    return reports.pixels(columns, rows)
