"""Serialize fzf PTY output and Kitty decorations through one terminal owner.

fzf still handles every key and search operation. Its stdin/stdout are files;
its controlling terminal is a private PTY. The parent alone writes /dev/tty.
"""
import errno
import json
from pathlib import Path
import fcntl
import os
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import textwrap
import re
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



def with_pulse_margin(argv: Sequence[str], rows: int, columns: int, bottom: int = PULSE_RESERVED_ROWS) -> list[str]:
    """Reserve the bottom rows in fzf itself, preserving keyboard navigation.

    fzf must know the reduced result height: hiding its output afterwards loses
    selections and corrupts scrolling. Keep existing top/side margin settings.
    """
    arguments = list(argv)
    if rows < 14 or columns < 32:
        return arguments
    for i, option in enumerate(arguments):
        value_index = i + 1 if option == '--margin' else i
        if option != '--margin' and not option.startswith('--margin='):
            continue
        if value_index >= len(arguments):
            continue
        value = arguments[value_index] if option == '--margin' else option.split('=', 1)[1]
        parts = value.split(',')
        if len(parts) not in (1, 2, 4):
            continue
        if len(parts) == 1:
            parts *= 4
        elif len(parts) == 2:
            parts = [parts[0], parts[1], parts[0], parts[1]]
        parts[2] = str(max(bottom, int(parts[2]) if parts[2].isdigit() else 0))
        arguments[value_index] = ','.join(parts) if option == '--margin' else '--margin=' + ','.join(parts)
        return arguments
    arguments.append(f'--margin=0,0,{bottom},0')
    return arguments


def pulse_background_packet(columns: int, rows: int, *, reserved_rows: int = PULSE_RESERVED_ROWS, frame_rows: int = 1) -> str:
    """Fill only the interior of fzf's reserved rows, leaving frame margins clear."""
    if rows < 14 or columns < 32:
        return ''
    start_row = max(1, rows - reserved_rows + 1)
    interior = ' ' * max(0, columns - 4)
    return ('\x1b7' + PULSE_BG + ''.join(
        f'\x1b[{row};3H{interior}' for row in range(start_row, rows - frame_rows + 1)
    ) + '\x1b[0m\x1b8')


def pulse_label_packet(columns: int, rows: int, label: str, *, pixel_width: int | None = None, pixel_height: int | None = None, orb_size: int = 104, margin: int = 104) -> str:
    """A subdued label in the reserved area, never over the fzf results."""
    if rows < 14 or columns < 32:
        return ''
    # Match KittyPulseOverlay pixel placement, not a hard-coded row.
    pw, ph = pixel_width or columns * 8, pixel_height or rows * 16
    cw, ch = pw / columns, ph / rows
    center_x = pw - margin - orb_size / 2
    center_y = ph - margin - orb_size / 2
    column = max(3, min(columns - 12, round((center_x - orb_size / 2 - 12 * cw) / cw) + 1))
    row = max(2, min(rows - 1, round(center_y / ch) + 1))
    return (f'\x1b7\x1b[{row};{column}H' + PULSE_BG +
            pulse_label_color(label) + f'{label:>11}' +
            '\x1b[0m\x1b8')


def ansi_status_packet(columns: int, rows: int, label: str, *, footer_inset: int = 1) -> str:
    """Right-align sync state in the reserved footer without moving the cursor."""
    if rows < 14 or columns < 32:
        return ''
    text = "STATUS: " + label.upper()
    return (f'\x1b7\x1b[{rows - footer_inset};3H' + PULSE_BG + ' ' * (columns - 4)
            + f'\x1b[{rows - footer_inset};{columns - len(text) - 2}H'
            + pulse_label_color(label) + text + '\x1b[0m\x1b8')


HELP_LINES = (
    "ENTER       Run or select the highlighted command",
    "CTRL-S      Save favorite (stay in picker)",
    "ALT-F       Save favorite (alternative shortcut)",
    "CTRL-D      Permanently delete selected command",
    "CTRL-Y      Copy selected command",
    "CTRL-R      Synchronize approved commands with Cloud Sync",
    "RIGHT       Show full command preview",
    "LEFT        Close command preview",
    "ALT-SHIFT-H Open / close this help",
    "ALT-SHIFT-S Cycle command sorting",
    "CTRL-G      Show global commands",
    "ALT-SHIFT-F Show global favorites",
    "CTRL-F      Show favorites",
    "CTRL-L      Show local commands",
    "CTRL-P      Toggle priority favorite",
    "ALT-UP/DOWN Reorder priority favorite",
    "CTRL-SPACE  Mark / unmark command and move down",
    "CTRL-O      Toggle command preview",
    "ALT-LEFT/RIGHT Page command preview",
    "PGUP/PGDN   Page command list",
    "UP/DOWN     Move selection",
    "TAB/SHIFT-TAB Mark and move selection",
    "CTRL-U      Clear search before cursor",
    "CTRL-W      Delete previous search word",
    "ESC         Cancel picker (also on error) / close help",
)


def top_banner_packet(columns: int, rows: int, version: str) -> str:
    """Draw text in fzf's deliberately reserved top margin."""
    if columns < 40 or rows < 14:
        return ''
    left = ""
    right = "ALT-SHIFT-H for help"
    inner = max(0, columns - 8)
    left = left[:max(0, inner - len(right) - 2)]
    gap = max(2, inner - len(left) - len(right))
    header = (left + ' ' * gap + right)[:inner]
    return ('\x1b7\x1b[48;2;11;8;18m\x1b[38;2;195;150;245m'
            + f'\x1b[1;5H{header}' + '\x1b[0m\x1b8')


def help_modal_packet(columns: int, rows: int, enter_action: str = 'run', scroll: int = 0) -> str:
    """Centered, opaque text dialog: no network calls or history access."""
    if columns < 50 or rows < 18:
        return ''
    width = min(66, columns - 8)
    visible_lines = HELP_LINES[scroll:scroll + max(1, rows - 10)]
    height = len(visible_lines) + 6
    top = max(1, (rows - height) // 2 + 1)
    left = max(1, (columns - width) // 2 + 1)
    bg = '\x1b[48;2;17;13;27m'
    fg = '\x1b[38;2;229;217;245m'
    purple = '\x1b[38;2;179;92;255m'
    def line(y: int, value: str, *, accent=False) -> str:
        # All border glyphs are single-cell Unicode box-drawing characters.
        # Keep width measured in terminal cells; do not use ASCII +---+.
        return (f'\x1b[{y};{left}H' + bg + (purple if accent else fg)
                + value.ljust(width)[:width])

    def framed(text: str = "") -> str:
        usable = width - 4
        return "│ " + text[:usable].ljust(usable) + " │"

    content = [
        "╭" + "─" * (width - 2) + "╮",
        framed("CMDVAULT  ·  KEYBOARD HELP"),
        "├" + "─" * (width - 2) + "┤",
    ]
    for value in visible_lines:
        if value.startswith('ENTER'):
            value = value.replace('Run or select', enter_action.capitalize() + ' or select', 1)
        content.append(framed(value))
    content.extend([
        framed(),
        framed("ESC / ENTER  CLOSE · UP/DOWN SCROLL"),
        "╰" + "─" * (width - 2) + "╯",
    ])
    return ('\x1b7' + ''.join(line(top + i, value,
            accent=i in (0, 1, 2, len(content) - 1))
            for i, value in enumerate(content)) + '\x1b[0m\x1b8')



def error_modal_packet(columns: int, rows: int, message: str, scroll: int = 0) -> str:
    """An opaque scrollable error dialog; escape controls and ANSI input are stripped."""
    if columns < 42 or rows < 12:
        return ''
    width = min(76, columns - 6)
    usable = width - 4
    clean = ''.join(ch if ch >= ' ' or ch == '\n' else ' ' for ch in message)
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', clean)
    wrapped = []
    for paragraph in clean.splitlines() or ['Unknown error']:
        wrapped.extend(textwrap.wrap(paragraph, width=usable, break_long_words=True,
                                     break_on_hyphens=False) or [''])
    visible = min(rows - 9, max(3, len(wrapped)))
    start = min(max(0, scroll), max(0, len(wrapped) - visible))
    top = max(1, (rows - (visible + 6)) // 2 + 1)
    left = max(1, (columns - width) // 2 + 1)
    bg = '\x1b[48;2;17;13;27m'
    fg = '\x1b[38;2;238;225;244m'
    red = '\x1b[38;2;255;90;110m'
    def frame(s=''):
        return '│ ' + s[:usable].ljust(usable) + ' │'
    count = len(wrapped)
    lines = ['╭' + '─' * (width - 2) + '╮', frame('CMDVAULT  ·  ERROR'),
             '├' + '─' * (width - 2) + '┤']
    lines += [frame(s) for s in wrapped[start:start + visible]]
    lines += [frame(f'↑/↓ scroll  ·  {start + 1}–{min(count, start + visible)}/{count}  ·  ESC exit / ENTER close'),
              '╰' + '─' * (width - 2) + '╯']
    return ('\x1b7' + ''.join(f'\x1b[{top+i};{left}H' + bg +
             (red if i in (0,1,2,len(lines)-1) else fg) + line for i,line in enumerate(lines)) +
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
               config: VisualConfig, graphics: bool = True, sixel: bool = False) -> subprocess.CompletedProcess:
    """subprocess.run-compatible selection result with a coordinated terminal."""
    from .geometry import PixelReports, PIXEL_QUERY
    pixel_reports = PixelReports()
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
    sixel_frame_geometry = None
    pulse_needs_draw = True
    help_visible = False
    help_scroll = 0
    error_text = env.get("CMDVAULT_ERROR_MODAL", "")[:16384]
    error_visible = bool(error_text)
    error_scroll = 0
    error_drawn = False
    heading_version = env.get("CMDVAULT_VERSION", "0.1.7")
    sync_status_path = env.get("CMDVAULT_SYNC_STATUS_FILE")
    sync_status_last = None
    enter_action = env.get("CMDVAULT_ENTER_ACTION", "run")
    heading_dirty = True

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
        if sixel:
            xp, yp = pixel_reports.pixels(max(1, columns), max(1, rows), xp, yp)
        return max(1,columns),max(1,rows),xp,yp

    def decoration_upload(*args, **kwargs):
        return decoration.upload(*args, **kwargs) if graphics else ''

    def clear_pulse():
        return pulse.clear() if graphics else ''

    def emit(value):
        write_all(terminal,value.encode('utf-8'))

    try:
        columns, rows, xp, yp = geometry()
        # Precompute before raw mode; frame generation must not delay keystrokes.
        upload = decoration_upload(xp or columns*8,yp or rows*16,columns,rows,animate=False)
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as result:
            source.write(input.encode('utf-8'))
            source.seek(0)
            tty.setraw(terminal)
            emit('\x1b[?1049h\x1b[48;2;11;8;18m\x1b[2J\x1b[H')
            screen_entered = True
            if sixel:
                emit(PIXEL_QUERY)
            from .sixel import footer_rows
            picker_args = with_pulse_margin(argv, rows, columns, footer_rows(rows) if sixel else PULSE_RESERVED_ROWS)
            process = subprocess.Popen(picker_args,stdin=source,stdout=result,stderr=slave,
                                       env=env,preexec_fn=child_terminal,close_fds=True)
            os.close(slave)
            slave = -1
            previous_signal = signal.signal(signal.SIGWINCH,resize_signal)
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
                exit_signals[signum] = signal.signal(signum,exit_signal)
            ready_to_place = False
            last_output = time.monotonic()
            while True:
                if sync_status_path:
                    try:
                        status_text = Path(sync_status_path).read_text(encoding="utf-8")
                        if status_text != sync_status_last:
                            sync_status_last = status_text
                            status = json.loads(status_text)
                            state = status.get("state", "ready")
                            if state in PULSE_LABEL_COLORS:
                                pulse.set_state(state)
                                pulse_needs_draw = True
                            if state in ("error", "offline"):
                                error_text = str(status.get("message") or "Cloud Sync failed without an error message")[:16384]
                                error_scroll = 0
                                error_visible = True
                                error_drawn = False
                    except (OSError, ValueError, TypeError):
                        pass
                if resized:
                    columns,rows,xp,yp = geometry()
                    if process.poll() is None:
                        os.killpg(process.pid,signal.SIGWINCH)
                    resized = False
                    if sixel:
                        emit(PIXEL_QUERY)
                    upload = decoration_upload(xp or columns*8,yp or rows*16,columns,rows,animate=False) or upload
                    ready_to_place = True
                    pulse_needs_draw = True
                    heading_dirty = True
                    error_drawn = False
                readable,_,_ = select.select([terminal,master],[],[],
                    .02 if loader and not loader.done.is_set() else .05 if ready_to_place else .25)
                if terminal in readable or (sixel and pixel_reports.expired()):
                    if terminal in readable:
                        keys = os.read(terminal,65536)
                        if not keys:
                            break
                        if sixel:
                            previous_pixels = pixel_reports.pixels(columns, rows, xp, yp)
                            keys = pixel_reports.feed(keys)
                            current_pixels = pixel_reports.pixels(columns, rows, xp, yp)
                            if current_pixels != previous_pixels:
                                xp, yp = current_pixels
                                ready_to_place = pulse_needs_draw = heading_dirty = True
                    else:
                        keys = pixel_reports.flush()
                    if not keys:
                        continue
                    if error_visible:
                        if keys == b"\x1b":
                            error_visible = False
                            write_all(master, keys)
                        elif keys in (b"\r", b"\n", b"\x08"):
                            error_visible = False
                            emit('\x1b[2J\x1b[H')
                            sixel_frame_geometry = None
                            decoration.key = None
                            upload = decoration_upload(xp or columns*8, yp or rows*16,
                                                       columns, rows, animate=False)
                            clear_pulse()
                            write_all(master, b"\x0c")
                            heading_dirty = pulse_needs_draw = ready_to_place = True
                            last_output = time.monotonic()
                        elif keys in (b"\x1b[B", b"j"):
                            error_scroll += 1
                            error_drawn = False
                        elif keys in (b"\x1b[A", b"k"):
                            error_scroll = max(0, error_scroll - 1)
                            error_drawn = False
                    elif help_visible:
                        # A dismissal key refreshes fzf; do not feed it to search.
                        if keys in (b'\x1b[A', b'\x1b[B'):
                            help_scroll = max(0, min(max(0, len(HELP_LINES) - max(1, rows - 10)), help_scroll + (1 if keys == b'\x1b[B' else -1)))
                            emit(help_modal_packet(columns, rows, enter_action, help_scroll))
                        elif keys:
                            help_visible = False
                            emit('\x1b[2J\x1b[H')
                            sixel_frame_geometry = None
                            # Erasing the text screen can also discard Kitty
                            # placements, so restore both graphics afterwards.
                            decoration.key = None
                            upload = decoration_upload(xp or columns*8, yp or rows*16,
                                                       columns, rows, animate=False)
                            clear_pulse()
                            write_all(master, b'\x0c')  # fzf redraw-screen
                            heading_dirty = True
                            pulse_needs_draw = True
                            ready_to_place = True
                            last_output = time.monotonic()
                    elif b'\x1bH' in keys:
                        prefix, _, _suffix = keys.partition(b'\x1bH')
                        if prefix:
                            write_all(master, prefix)
                        help_visible = True
                        help_scroll = 0
                        emit(help_modal_packet(columns, rows, enter_action, help_scroll))
                    else:
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
                    if not help_visible and not error_visible:
                        write_all(terminal,data)
                    previous_erasures = boundary.erasures
                    boundary.feed(data)
                    # Re-place after erasure / alternate screen changes. Upload
                    # again only after destructive operations, not every key.
                    if boundary.erasures != previous_erasures:
                        sixel_frame_geometry = None
                        decoration.key = None
                        upload = decoration_upload(xp or columns*8,yp or rows*16,columns,rows,animate=False) or upload
                        clear_pulse()
                        pulse_needs_draw = True
                    ready_to_place = True
                    last_output = time.monotonic()
                if ready_to_place and not help_visible and boundary.safe and time.monotonic()-last_output >= .025:
                    geometry_key = (columns,rows,xp,yp)
                    if upload or geometry_key != animation_geometry:
                        if loader:
                            loader.close()
                            loader = None
                        if graphics and config.animation:
                            loader = AnimationLoader(decoration,xp or columns*8,yp or rows*16,columns,rows)
                        animation_geometry = geometry_key
                    emit(upload + (decoration.place(columns,rows) if graphics else ''))
                    if sixel and not error_visible and sixel_frame_geometry != geometry_key:
                        from .sixel import sixel_frame_packet
                        emit(sixel_frame_packet(columns, rows, xp, yp, config.glow))
                        sixel_frame_geometry = geometry_key
                    # The parent is the only terminal writer. Place the overlay
                    # only after fzf's complete escape sequence and redraw.
                    pulse_needs_draw = True
                    upload = ''
                    ready_to_place = False
                    heading_dirty = True
                if loader and boundary.safe and not upload and not help_visible and not error_visible:
                    emit(loader.drain())
                if not help_visible and boundary.safe and not ready_to_place and not upload:
                    if heading_dirty:
                        if not sixel:
                            emit(top_banner_packet(columns, rows, heading_version))
                        heading_dirty = False
                    location = (columns, rows, xp, yp)
                    if location != pulse_geometry:
                        pulse_needs_draw = True
                        pulse_geometry = location
                    packet = pulse.packet(
                        columns, rows, now=time.monotonic(),
                        force=pulse_needs_draw,
                        pixel_width=xp or columns * 8,
                        pixel_height=yp or rows * 16,
                    ) if graphics else ''
                    if pulse_needs_draw:
                        if sixel:
                            # The SIXEL bottom tube occupies the penultimate row;
                            # text erasure there deletes the image in Windows Terminal.
                            emit(pulse_background_packet(columns, rows,
                                reserved_rows=footer_rows(rows), frame_rows=2))
                        else:
                            emit(pulse_background_packet(columns, rows))
                        if graphics:
                            emit(pulse_label_packet(columns, rows, pulse.label, pixel_width=xp or columns * 8, pixel_height=yp or rows * 16, orb_size=pulse.pulse.style.size))
                        else:
                            if not sixel or error_visible:
                                emit(ansi_status_packet(columns, rows, pulse.label, footer_inset=2 if sixel else 1))
                            if sixel and not error_visible:
                                from .sixel import sixel_status_packet
                                emit(sixel_status_packet(columns, rows, pulse.label, xp, yp))
                    if packet:
                        emit(packet)
                    pulse_needs_draw = False
                if error_visible and boundary.safe and not error_drawn:
                    emit(error_modal_packet(columns, rows, error_text, error_scroll))
                    error_drawn = True
                if process.poll() is not None and master not in readable:
                    break
            code = process.wait()
            result.seek(0)
            return subprocess.CompletedProcess(argv,code,result.read().decode('utf-8'),'')
    finally:
        from .terminal import restore_terminal
        # A second interrupt must not abort recovery while the tty is raw.
        for signum in exit_signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            try:
                cleanup = clear_pulse() + (decoration.delete() if graphics else '')
                restore_terminal(terminal, original, cleanup if screen_entered else '')
            finally:
                # These operations can raise or wait; the keyboard is already restored.
                try:
                    if process is not None and process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=1)
                finally:
                    if loader:
                        loader.close()
        finally:
            for signum, handler in exit_signals.items():
                signal.signal(signum, handler)
            if previous_signal is not None:
                signal.signal(signal.SIGWINCH, previous_signal)
            for descriptor in (terminal, master, slave):
                if descriptor >= 0:
                    os.close(descriptor)
