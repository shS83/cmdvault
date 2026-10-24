"""Serialize fzf PTY output and Kitty decorations through one terminal owner.

fzf still handles every key and search operation. Its stdin/stdout are files;
its controlling terminal is a private PTY. The parent alone writes /dev/tty.
"""
import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import tty
from typing import Mapping, Sequence
from .config import VisualConfig
from .kitty import KittyDecoration, AnimationLoader, KittyPulseOverlay
import secrets


PULSE_RESERVED_ROWS = 4
PULSE_BG = "\x1b[48;2;11;8;18m"

# Label colors are intentionally independent of the metallic orb surface.
PULSE_LABEL_COLORS = {
    "ready": (242, 240, 246),
    "syncing": (179, 92, 255),
    "synced": (64, 230, 155),
    "error": (255, 64, 88),
    "offline": (255, 209, 102),
    "disabled": (119, 119, 131),
}


def pulse_label_color(label: str) -> str:
    rgb = PULSE_LABEL_COLORS.get(label.lower(), PULSE_LABEL_COLORS["ready"])
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"



def with_pulse_margin(argv: Sequence[str], rows: int, columns: int) -> list[str]:
    """Reserve the bottom rows in fzf itself, preserving keyboard navigation.

    fzf must know the reduced result height: hiding its output afterwards loses
    selections and corrupts scrolling. Keep existing top/side margin settings.
    """
    arguments = list(argv)
    if rows < 14 or columns < 32:
        return arguments
    for i, option in enumerate(arguments):
        if option.startswith('--margin='):
            parts = option.split('=', 1)[1].split(',')
            if len(parts) == 4:
                parts[2] = str(max(PULSE_RESERVED_ROWS, int(parts[2]) if parts[2].isdigit() else 0))
                arguments[i] = '--margin=' + ','.join(parts)
                return arguments
        elif option == '--margin' and i + 1 < len(arguments):
            parts = arguments[i + 1].split(',')
            if len(parts) == 4:
                parts[2] = str(max(PULSE_RESERVED_ROWS, int(parts[2]) if parts[2].isdigit() else 0))
                arguments[i + 1] = ','.join(parts)
                return arguments
    arguments.append(f'--margin=0,0,{PULSE_RESERVED_ROWS},0')
    return arguments


def pulse_background_packet(columns: int, rows: int) -> str:
    """Fill only the interior of fzf's reserved rows, leaving frame margins clear."""
    if rows < 14 or columns < 32:
        return ''
    start_row = max(1, rows - PULSE_RESERVED_ROWS + 1)
    interior = ' ' * max(0, columns - 4)
    return ('\x1b7' + PULSE_BG + ''.join(
        f'\x1b[{row};3H{interior}' for row in range(start_row, rows)
    ) + '\x1b[0m\x1b8')


def pulse_label_packet(columns: int, rows: int, label: str, *, pixel_width: int | None = None, pixel_height: int | None = None, orb_size: int = 104, margin: int = 64) -> str:
    """A subdued label in the reserved area, never over the fzf results."""
    if rows < 14 or columns < 32:
        return ''
    # Match KittyPulseOverlay pixel placement, not a hard-coded row.
    pw, ph = pixel_width or columns * 8, pixel_height or rows * 16
    cw, ch = pw / columns, ph / rows
    center_x = pw - margin - orb_size / 2
    center_y = ph - margin - orb_size / 2
    column = max(3, min(columns - 12, round((center_x - orb_size / 2 - 12 * cw) / cw) + 1))
    row = max(rows - PULSE_RESERVED_ROWS + 1, min(rows - 1, round(center_y / ch) + 1))
    return (f'\x1b7\x1b[{row};{column}H' + PULSE_BG +
            pulse_label_color(label) + f'{label:>11}' +
            '\x1b[0m\x1b8')


class EscapeBoundary:
    """Track split VT sequences so graphics never interrupt a child sequence."""
    def __init__(self):
        self.state = 'text'
        self.sequence = bytearray()
        self.erasures = 0
        self.utf8_remaining = 0

    def feed(self, data: bytes) -> bool:
        for byte in data:
            if self.state == 'text':
                if 0x80 <= byte <= 0xbf and self.utf8_remaining:
                    self.utf8_remaining -= 1
                elif byte >= 0xc2:
                    self.utf8_remaining = 1 if byte < 0xe0 else 2 if byte < 0xf0 else 3
                else:
                    self.utf8_remaining = 0
                if byte == 27:
                    self.state = 'escape'
            elif self.state == 'escape':
                if byte == ord('['):
                    self.state = 'csi'
                    self.sequence.clear()
                elif byte in (ord(']'),ord('P'),ord('_'),ord('^'),ord('X')):
                    self.state = 'string'
                elif 0x20 <= byte <= 0x2f:
                    self.state = 'intermediate'
                else:
                    self.state = 'text'
            elif self.state == 'csi':
                self.sequence.append(byte)
                if 0x40 <= byte <= 0x7e:
                    if bytes(self.sequence) in (b'2J',b'3J',b'?1049h',b'?1049l'):
                        self.erasures += 1
                    self.state = 'text'
            elif self.state == 'intermediate':
                if 0x30 <= byte <= 0x7e:
                    self.state = 'text'
            elif self.state == 'string':
                if byte == 7:
                    self.state = 'text'
                elif byte == 27:
                    self.state = 'string_escape'
            elif self.state == 'string_escape':
                self.state = 'text' if byte == ord('\\') else 'string'
        return self.safe

    @property
    def safe(self):
        return self.state == 'text' and self.utf8_remaining == 0


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd,view):]


def run_picker(argv: Sequence[str], *, input: str, env: Mapping[str, str],
               config: VisualConfig) -> subprocess.CompletedProcess:
    """subprocess.run-compatible selection result with a coordinated terminal."""
    terminal = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    master, slave = os.openpty()
    original = termios.tcgetattr(terminal)
    decoration = KittyDecoration(config)
    pulse_id = secrets.randbelow(2**31 - 1) + 1
    while pulse_id == decoration.image_id:
        pulse_id = secrets.randbelow(2**31 - 1) + 1
    pulse = KittyPulseOverlay(config, pulse_id)
    # Explicit, local-only visual preview; no sync API calls or history access.
    demo_state = os.environ.get("CMDVAULT_PULSE_DEMO", "").strip().lower()
    if demo_state in PULSE_LABEL_COLORS:
        pulse.set_state(demo_state)
    process = None
    previous_signal = None
    exit_signals = {}
    resized = True
    boundary = EscapeBoundary()
    screen_entered = False
    loader = None
    animation_geometry = None
    pulse_geometry = None
    pulse_needs_draw = True

    def resize_signal(signum, frame):
        nonlocal resized
        resized = True

    def exit_signal(signum, frame):
        raise KeyboardInterrupt

    def child_terminal():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    def geometry():
        raw = fcntl.ioctl(terminal,termios.TIOCGWINSZ,b'\0'*8)
        rows, columns, xp, yp = struct.unpack('HHHH',raw)
        fcntl.ioctl(master,termios.TIOCSWINSZ,raw)
        return max(1,columns),max(1,rows),xp,yp

    def emit(value):
        write_all(terminal,value.encode('ascii'))

    try:
        columns, rows, xp, yp = geometry()
        # Precompute before raw mode; frame generation must not delay keystrokes.
        upload = decoration.upload(xp or columns*8,yp or rows*16,columns,rows,animate=False)
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as result:
            source.write(input.encode('utf-8'))
            source.seek(0)
            tty.setraw(terminal)
            emit('\x1b[?1049h\x1b[2J\x1b[H')
            screen_entered = True
            picker_args = with_pulse_margin(argv, rows, columns)
            process = subprocess.Popen(picker_args,stdin=source,stdout=result,stderr=slave,
                                       env=env,preexec_fn=child_terminal,close_fds=True)
            os.close(slave)
            slave = -1
            previous_signal = signal.signal(signal.SIGWINCH,resize_signal)
            for signum in (signal.SIGTERM,signal.SIGHUP):
                exit_signals[signum] = signal.signal(signum,exit_signal)
            ready_to_place = False
            last_output = time.monotonic()
            while True:
                if resized:
                    columns,rows,xp,yp = geometry()
                    if process.poll() is None:
                        os.killpg(process.pid,signal.SIGWINCH)
                    resized = False
                    upload = decoration.upload(xp or columns*8,yp or rows*16,columns,rows,animate=False) or upload
                    ready_to_place = True
                    pulse_needs_draw = True
                readable,_,_ = select.select([terminal,master],[],[],
                    .02 if loader and not loader.done.is_set() else .05 if ready_to_place else .25)
                if terminal in readable:
                    keys = os.read(terminal,65536)
                    if not keys:
                        break
                    write_all(master,keys)
                if master in readable:
                    try:
                        data = os.read(master,65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not data:
                        break
                    write_all(terminal,data)
                    previous_erasures = boundary.erasures
                    boundary.feed(data)
                    # Re-place after erasure / alternate screen changes. Upload
                    # again only after destructive operations, not every key.
                    if boundary.erasures != previous_erasures:
                        decoration.key = None
                        upload = decoration.upload(xp or columns*8,yp or rows*16,columns,rows,animate=False) or upload
                        pulse.clear()
                        pulse_needs_draw = True
                    ready_to_place = True
                    last_output = time.monotonic()
                if ready_to_place and boundary.safe and time.monotonic()-last_output >= .025:
                    geometry_key = (columns,rows,xp,yp)
                    if upload or geometry_key != animation_geometry:
                        if loader:
                            loader.close()
                            loader = None
                        if config.animation:
                            loader = AnimationLoader(decoration,xp or columns*8,yp or rows*16,columns,rows)
                        animation_geometry = geometry_key
                    emit(upload + decoration.place(columns,rows))
                    # The parent is the only terminal writer. Place the overlay
                    # only after fzf's complete escape sequence and redraw.
                    pulse_needs_draw = True
                    upload = ''
                    ready_to_place = False
                if loader and boundary.safe and not upload:
                    emit(loader.drain())
                if boundary.safe and not ready_to_place and not upload:
                    location = (columns, rows, xp, yp)
                    if location != pulse_geometry:
                        pulse_needs_draw = True
                        pulse_geometry = location
                    packet = pulse.packet(
                        columns, rows, now=time.monotonic(),
                        force=pulse_needs_draw,
                        pixel_width=xp or columns * 8,
                        pixel_height=yp or rows * 16,
                    )
                    if pulse_needs_draw:
                        emit(pulse_background_packet(columns, rows))
                        emit(pulse_label_packet(columns, rows, pulse.label, pixel_width=xp or columns * 8, pixel_height=yp or rows * 16, orb_size=pulse.pulse.style.size))
                    if packet:
                        emit(packet)
                    pulse_needs_draw = False
                if process.poll() is not None and master not in readable:
                    break
            code = process.wait()
            result.seek(0)
            return subprocess.CompletedProcess(argv,code,result.read().decode('utf-8'),'')
    finally:
        if loader:
            loader.close()
        for signum,handler in exit_signals.items():
            signal.signal(signum,handler)
        if previous_signal is not None:
            signal.signal(signal.SIGWINCH,previous_signal)
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait()
        try:
            if screen_entered:
                emit(pulse.clear()+decoration.delete()+'\x1b[?2026l\x1b[?1000l\x1b[?1002l\x1b[?1006l\x1b[?2004l\x1b[?7h\x1b[0m\x1b[?25h\x1b[?1049l')
        finally:
            termios.tcsetattr(terminal,termios.TCSANOW,original)
            os.close(terminal)
            os.close(master)
            if slave >= 0:
                os.close(slave)
