"""Kitty graphics with cached terminal-driven animation and scoped cleanup."""
import base64
from functools import lru_cache
import secrets
import queue
import threading
import zlib
from .ansi import AnsiRenderer
from .config import VisualConfig
from .effects import neon_asset, LOOP_SECONDS

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


class KittyRenderer(AnsiRenderer):
    def __init__(self,*args):
        super().__init__(*args)
        self.decoration = KittyDecoration(self.config)
        self.image_id = self.decoration.image_id
        self.loader = None

    def resize(self, width: int, height: int) -> None:
        # ED2 erases Kitty images as well as text; re-upload cached frames.
        if self.loader:
            self.loader.close()
            self.loader = None
        self.decoration.key = None
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
        for y in range(self.height):
            text = lines[y-1] if 1 <= y <= len(lines) else ''
            row = ('  '+text[:max(0,self.width-4)]).ljust(self.width)
            rows.append(f"\x1b[{y+1};1H"+color+row)
        self.emit(''.join(rows)+"\x1b[0m")
        self.emit(self.decoration.place(self.width,self.height))

    def tick(self):
        if self.loader:
            self.emit(self.loader.drain())

    def cleanup(self):
        if self.loader:
            self.loader.close()
            self.loader = None
        try:
            if self.active:
                self.emit(self.decoration.delete())
        finally:
            super().cleanup()
