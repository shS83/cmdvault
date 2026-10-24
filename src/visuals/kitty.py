"""Kitty graphics with cached terminal-driven animation and scoped cleanup."""
import base64
from functools import lru_cache
import secrets
import queue
import threading
import time
import zlib
from .ansi import AnsiRenderer
from .config import VisualConfig
from .effects import neon_asset, LOOP_SECONDS
from .sync_pulse import SyncPulse, PulseStyle, SyncState, intensity

SESSION_SEED = secrets.randbits(32)


def command(control: str, payload: str = '') -> str:
    return '\x1b_G' + control + (';' + payload if payload else '') + '\x1b\\'


def encode_rgba(data: bytes, width: int, height: int, image_id: int, *,
                action: str = 't', gap: int = 83, compressed: bool = False) -> str:
    payload = base64.b64encode(zlib.compress(data) if compressed else data).decode('ascii')
    return encode_payload(payload,width,height,image_id,action=action,gap=gap,compressed=compressed)


def encode_payload(payload: str, width: int, height: int, image_id: int, *,
                   action: str = 't', gap: int = 83, compressed: bool = True) -> str:
    chunks = [payload[i:i+4096] for i in range(0,len(payload),4096)]
    header = f'a={action},f=32,s={width},v={height},i={image_id},q=2,'
    if compressed:
        header += 'o=z,'
    if action == 'f':
        header += f'z={gap},X=1,'
    continuation = 'a=f,q=2,' if action == 'f' else 'q=2,'
    return ''.join(command((header if i == 0 else continuation) +
                          f'm={int(i<len(chunks)-1)}',chunk)
                   for i,chunk in enumerate(chunks))


@lru_cache(maxsize=256)
def frame_payload(width, height, glow, frame, frames, inset_x, inset_y, radius, seed=0):
    return base64.b64encode(zlib.compress(
        neon_asset(width,height,glow,frame,frames,inset_x=inset_x,
                   inset_y=inset_y,radius=radius,seed=seed).tobytes(),1)).decode('ascii')


def frame_payloads(width: int, height: int, glow: float, frames: int,
                   inset_x: float = 8, inset_y: float = 8, radius: float = 0, seed: int = 0) -> tuple:
    return tuple(frame_payload(width,height,glow,frame,frames,inset_x,inset_y,radius,seed)
                 for frame in range(frames))


def raster_geometry(width: int, height: int, columns: int, rows: int) -> tuple:
    # Uniform downscaling preserves tube thickness. Insets share the picker's fixed
    # one-row / two-column padding, with the tube centered in the outer cells.
    width, height = max(32,width), max(32,height)
    scale = min(1,480/width,320/height)
    rw,rh = max(32,round(width*scale)),max(32,round(height*scale))
    cell_x,cell_y = rw/max(1,columns),rh/max(1,rows)
    return rw,rh,cell_x*.5,cell_y*.5,0


def frame_gap(frame: int, fps: int) -> int:
    """Distribute 33/34 ms gaps so 30 frames occupy exactly one second."""
    return round((frame+1)*1000/fps)-round(frame*1000/fps)


class KittyDecoration:
    """Cached assets, application-owned IDs and terminal-driven playback."""
    def __init__(self, config: VisualConfig):
        self.config = config
        self.seed = SESSION_SEED
        self.image_id = secrets.randbelow(2**31-1)+1
        self.key = None

    def delete(self) -> str:
        self.key = None
        return command(f'a=d,d=I,i={self.image_id},q=2')

    def upload(self, width: int, height: int, columns: int = 80, rows: int = 24, *, animate: bool = True) -> str:
        width,height,inset_x,inset_y,radius = raster_geometry(width,height,columns,rows)
        key = (width,height,inset_x,inset_y,radius)
        if self.key == key:
            return ''
        result = self.delete()
        frames = self.config.fps*LOOP_SECONDS if self.config.animation and animate else 1
        gap = round(1000/self.config.fps)
        for frame,payload in enumerate(frame_payloads(width,height,self.config.glow,frames,inset_x,inset_y,radius,self.seed)):
            result += encode_payload(payload,width,height,self.image_id,
                                     action='t' if frame == 0 else 'f',gap=frame_gap(frame,self.config.fps))
        if self.config.animation and animate:
            result += command(f'a=a,i={self.image_id},r=1,z={gap},q=2')
            result += command(f'a=a,i={self.image_id},s=3,v=1,q=2')
        self.key = key
        return result

    def place(self, columns: int, rows: int) -> str:
        # Positive z is safe here: only reserved margins contain nontransparent
        # pixels. Unlike negative z this also survives opaque text backgrounds.
        return ('\x1b7\x1b[H' + command(
            f'a=p,i={self.image_id},p=1,c={columns},r={rows},z=1,C=1,q=2') + '\x1b8')


class AnimationLoader:
    """Compute pixels off the UI loop; only the caller writes protocol packets."""
    def __init__(self, decoration, width, height, columns, rows):
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.packets = queue.Queue(maxsize=8)
        geometry = raster_geometry(width,height,columns,rows)
        self.worker = threading.Thread(target=self._load,args=(decoration,geometry),daemon=True)
        self.worker.start()

    def _load(self, decoration, geometry):
        width,height,ix,iy,radius = geometry
        config,image_id = decoration.config,decoration.image_id
        frames = config.fps*LOOP_SECONDS
        gap = round(1000/config.fps)
        def put(packet):
            while not self.cancel.is_set():
                try:
                    self.packets.put(packet,timeout=.05)
                    return True
                except queue.Full:
                    pass
            return False
        try:
            if not put(command(f'a=a,i={image_id},r=1,z={gap},q=2')):
                return
            for frame in range(1,frames):
                if self.cancel.is_set():
                    return
                payload = frame_payload(width,height,config.glow,frame,frames,ix,iy,radius,decoration.seed)
                packet = encode_payload(payload,width,height,image_id,action='f',gap=frame_gap(frame,config.fps))
                if frame == 1:
                    packet += command(f'a=a,i={image_id},s=2,q=2')
                if not put(packet):
                    return
            put(command(f'a=a,i={image_id},s=3,v=1,q=2'))
        except Exception as error:
            # Keep the static frame; a worker must never print to the terminal.
            self.error = error
        finally:
            self.done.set()

    def drain(self) -> str:
        packets = []
        while True:
            try:
                packets.append(self.packets.get_nowait())
            except queue.Empty:
                return ''.join(packets)

    def close(self):
        self.cancel.set()
        self.worker.join(timeout=1)


class KittyPulseOverlay:
    """Owns a dedicated bottom-right orb, away from the neon border."""

    def __init__(self, config, image_id: int):
        self.config = config
        self.image_id = image_id
        self.pulse = SyncPulse(PulseStyle(size=104, glow=min(1.0, max(.85, config.glow)), fps=config.fps), SyncState.READY)
        self.last_frame_key = None
        self.last_update = 0.0
        self.placed_at = None

    @property
    def label(self):
        return self.pulse.label

    def set_state(self, state):
        before = self.pulse.state
        self.pulse.set_state(state)
        if self.pulse.state != before:
            self.last_frame_key = None

    def clear(self):
        self.last_frame_key = None
        self.placed_at = None
        return command(f'a=d,d=I,i={self.image_id},q=2')

    def packet(self, columns: int, rows: int, now=None, *, force=False, pixel_width=None, pixel_height=None) -> str:
        """Place the orb in reserved bottom rows, inset from the neon border."""
        if columns < 32 or rows < 14:
            if self.placed_at is not None:
                return self.clear()
            return ''
        now = time.monotonic() if now is None else now
        frame_interval = 1 / max(1, self.config.fps)
        if not force and now - self.last_update < frame_interval:
            return ''
        self.last_update = now
        # Quantize alpha to limit redraws and avoid an excessively large cache.
        level = round(intensity(self.pulse.state, max(0, now - self.pulse._since)) * 20)
        key = (self.pulse.state, level, round(self.config.glow * 100))
        # Kitty placement X/Y are pixel offsets inside a terminal cell.
        # Keep 12px between the orb and the INNER edge of the neon border.
        # The outer 20px are occupied by the frame itself.
        pw = pixel_width or columns * 8
        ph = pixel_height or rows * 16
        cw, ch = pw / columns, ph / rows
        margin = 64  # user-adjusted inset from window edges, in physical pixels
        orb_px = self.pulse.style.size
        left_px = max(0, pw - margin - orb_px)
        top_px = max(0, ph - margin - orb_px)
        col = max(1, int(left_px // cw) + 1)
        row = max(1, int(top_px // ch) + 1)
        off_x = round(left_px - (col - 1) * cw)
        off_y = round(top_px - (row - 1) * ch)
        position = (col, row, off_x, off_y)
        if key == self.last_frame_key and position == self.placed_at:
            return ''
        # Composite onto the exact panel color. No transparent rectangular patch
        # or terminal/desktop bleed-through behind the metallic sphere.
        from PIL import Image
        orb = self.pulse.frame(now)
        image = Image.new("RGBA", orb.size, (11, 8, 18, 255))
        image.alpha_composite(orb)
        result = self.clear()
        result += encode_rgba(image.tobytes(), image.width, image.height,
                              self.image_id, compressed=True)
        # Never scale the orb disproportionately to a fixed 5x3 cell rectangle.
        col, row, ox, oy = position
        # Omit c/r: Kitty renders the actual image pixels at native size.
        result += ('\x1b7' + f'\x1b[{row};{col}H' + command(
            f'a=p,i={self.image_id},p=1,X={ox},Y={oy},z=3,C=1,q=2') + '\x1b8')
        self.placed_at = position
        self.last_frame_key = key
        return result


class KittyRenderer(AnsiRenderer):
    def __init__(self,*args):
        super().__init__(*args)
        self.decoration = KittyDecoration(self.config)
        self.image_id = self.decoration.image_id
        self.loader = None
        pulse_id = secrets.randbelow(2**31-1)+1
        while pulse_id == self.image_id:
            pulse_id = secrets.randbelow(2**31-1)+1
        self.sync_pulse = KittyPulseOverlay(self.config, pulse_id)

    def set_sync_state(self, state):
        """Called by the application when a REAL sync event changes state."""
        self.sync_pulse.set_state(state)

    def resize(self, width: int, height: int) -> None:
        # ED2 erases Kitty images as well as text; re-upload cached frames.
        if self.loader:
            self.loader.close()
            self.loader = None
        self.decoration.key = None
        self.emit(self.sync_pulse.clear())
        super().resize(width,height)

    def draw(self,lines,phase=0):
        if not self.caps.tty:
            return super().draw(lines,phase)
        pixel_width,pixel_height = self.width*8,self.height*16
        try:
            import fcntl
            import struct
            import termios
            rows,columns,xp,yp = struct.unpack('HHHH',fcntl.ioctl(
                self.output.fileno(),termios.TIOCGWINSZ,b'\0'*8))
            if xp and yp and columns == self.width and rows == self.height:
                pixel_width,pixel_height = xp,yp
        except (OSError,ValueError,AttributeError):
            pass
        upload = self.decoration.upload(pixel_width,pixel_height,self.width,self.height,animate=False)
        self.emit(upload)
        if upload and self.config.animation:
            if self.loader:
                self.loader.close()
            self.loader = AnimationLoader(self.decoration,pixel_width,pixel_height,self.width,self.height)
        # Use the same content inset as fzf; no second ANSI box underneath.
        color = "\x1b[38;2;238;232;255m\x1b[48;2;11;8;18m"
        rows = []
        pulse_visible = self.width >= 32 and self.height >= 14
        # Reserve the last six rows as a dedicated pulse region: fill them with
        # the same opaque panel background before placing the image.  No picker
        # text is permitted in this area, even when many results are present.
        reserved_rows = 6 if pulse_visible else 0
        content_end = self.height - reserved_rows
        for y in range(self.height):
            text = lines[y-1] if 1 <= y <= min(len(lines), content_end - 1) else ''
            row = ('  '+text[:max(0,self.width-4)]).ljust(self.width)
            rows.append(f"\x1b[{y+1};1H"+color+row)
        if pulse_visible:
            # Center the text underneath the 5-column orb, without touching
            # either the lower or right Neon Frame.
            label = self.sync_pulse.label
            label_col = max(3, self.width - 23)
            label_row = self.height - 4
            rows.append(f"\x1b[{label_row};{label_col}H" + color +
                        f"\x1b[38;2;199;180;216m{label:>11}\x1b[0m")
        self.emit(''.join(rows)+"\x1b[0m")
        self.emit(self.decoration.place(self.width,self.height))
        self._pulse_pixel_geometry = (pixel_width, pixel_height)
        self.emit(self.sync_pulse.packet(self.width, self.height, force=True,
              pixel_width=pixel_width, pixel_height=pixel_height))

    def tick(self):
        if self.loader:
            self.emit(self.loader.drain())
        # The host UI already owns this tick. No separate terminal writer.
        if self.active and self.caps.tty:
            pw, ph = getattr(self, '_pulse_pixel_geometry', (self.width * 8, self.height * 16))
            self.emit(self.sync_pulse.packet(self.width, self.height,
                      pixel_width=pw, pixel_height=ph))

    def cleanup(self):
        if self.loader:
            self.loader.close()
            self.loader = None
        try:
            if self.active:
                self.emit(self.sync_pulse.clear())
                self.emit(self.decoration.delete())
        finally:
            super().cleanup()
