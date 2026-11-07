"""Real SIXEL encoding; decorations occupy a reserved area above text."""
from functools import lru_cache
from .ansi import AnsiRenderer
from .effects import neon_asset

@lru_cache(maxsize=16)
def encode_sixel(width: int, height: int, pixels: bytes) -> str:
    # Fixed 16-level purple palette: deterministic, cheap quantization.
    palette = "".join(f"#{i};2;{round(i*80/15)};{round(i*45/15)};{round(i*100/15)}" for i in range(16))
    indices = [min(15, round(max(pixels[i:i+3])*15/255)) for i in range(0,len(pixels),3)]
    bands = []
    for y in range(0,height,6):
        layers = []
        for color in range(16):
            values = []
            for x in range(width):
                bits = sum(1<<dy for dy in range(min(6,height-y)) if indices[(y+dy)*width+x] == color)
                values.append(chr(63+bits))
            if any(c != '?' for c in values):
                layers.append(f"#{color}" + "".join(values))
        bands.append("$".join(layers))
    return '\x1bP0;1;0q"1;1;' + str(width) + ';' + str(height) + palette + "-".join(bands) + "\x1b\\"

class SixelRenderer(AnsiRenderer):
    def draw(self, lines, phase=0):
        if not self.caps.tty or self.height < 8 or self.width < 12:
            return super().draw(lines, phase)
        # Use the same assets and reserved margins as the interactive picker.
        self.emit('\x1b[2J\x1b[48;2;11;8;18m\x1b[38;2;238;232;255m')
        for index, text in enumerate(lines[:max(0, self.height-footer_rows(self.height)-3)]):
            self.emit(f'\x1b[{index+3};5H' + text[:max(0, self.width-8)])
        self.emit('\x1b[0m')
        from .geometry import diagnostic_pixels
        xp, yp = diagnostic_pixels(self.output, self.width, self.height)
        self.emit(sixel_frame_packet(self.width, self.height, xp, yp, self.config.glow))
        self.emit(sixel_status_packet(self.width, self.height, 'ready', xp, yp))



@lru_cache(maxsize=24)
def sixel_status_packet(columns: int, rows: int, label: str,
                        pixel_width: int = 0, pixel_height: int = 0) -> str:
    """Static sync artwork in the footer; never cover fzf or modal text.

    SIXEL has no portable image deletion or layering. Repaint this small reserved
    rectangle after text redraws, rather than animating a full-screen border.
    """
    if columns < 32 or rows < 14:
        return ''
    from PIL import Image
    from .sync_pulse import SyncPulse, PulseStyle, SyncState, COLORS
    cell_width = (pixel_width or columns * 8) / columns
    cell_height = (pixel_height or rows * 16) / rows
    size = max(1, min(104, int(cell_height * (footer_rows(rows) - 2))))
    pulse = SyncPulse(PulseStyle(size=max(32, size), glow=.85), SyncState(label.lower()))
    frame = pulse.frame(pulse._since).resize((size, size))
    image = Image.new('RGBA', frame.size, (11, 8, 18, 255))
    image.alpha_composite(frame)
    column = max(3, columns - int(size / cell_width + 1) - 3)
    label_text = 'STATUS: ' + label.upper()
    rgb = COLORS[SyncState(label.lower())]
    label_color = f'\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m'
    label_column = max(3, column - len(label_text) - 2)
    label_row = rows - footer_rows(rows) + 1 + int(size / cell_height / 2)
    return (f'\x1b7\x1b[{rows - footer_rows(rows) + 1};{column}H'
            + encode_image(image)
            + f'\x1b[{label_row};{label_column}H' + label_color
            + label_text + '\x1b[0m\x1b8')



def encode_image(image) -> str:
    """Adaptive RGB palette and run compression preserve the shared artwork."""
    from itertools import groupby
    image = image.convert('RGB').quantize(colors=32)
    width, height = image.size
    pixels = list(image.tobytes())
    colors = sorted(set(pixels))
    rgb = image.getpalette()
    palette = ''.join(f'#{c};2;' + ';'.join(str(round(v * 100 / 255))
                      for v in rgb[c*3:c*3+3]) for c in colors)
    bands = []
    for y in range(0, height, 6):
        layers = []
        for color in colors:
            values = [chr(63 + sum(1 << dy for dy in range(min(6, height-y))
                       if pixels[(y+dy)*width+x] == color)) for x in range(width)]
            if any(v != '?' for v in values):
                packed = []
                for value, group in groupby(values):
                    count = sum(1 for _ in group)
                    packed.append(f'!{count}{value}' if count > 3 else value * count)
                layers.append(f'#{color}' + ''.join(packed))
        bands.append('$'.join(layers))
    return f'\x1bP0;1;0q"1;1;{width};{height}' + palette + '-'.join(bands) + '\x1b\\'


def footer_rows(rows):
    return max(4, min(8, rows // 3))


@lru_cache(maxsize=8)
def sixel_frame_packet(columns, rows, pixel_width=0, pixel_height=0, glow=.45):
    """Crop the shared Kitty frame into margins, leaving all text untouched."""
    if columns < 32 or rows < 14:
        return ''
    from PIL import Image
    from .kitty import raster_geometry, SESSION_SEED
    width, screen_height = pixel_width or columns*8, pixel_height or rows*16
    cw = max(1, round(width / columns))
    ch = max(6, round(screen_height / rows))
    # A SIXEL band always consumes six pixel rows, including zero padding.
    # Drawing a 20px strip at the last text row consumes 24px and scrolls WT.
    # Keep the last text row free and fit horizontal strips to whole bands.
    strip_height = ch // 6 * 6
    bottom_y = (rows - 2) * ch
    height = bottom_y + strip_height
    rw, rh, ix, iy, radius = raster_geometry(width, height, columns, rows)
    asset = neon_asset(rw, rh, glow, inset_x=ix, inset_y=iy,
                       radius=radius, seed=SESSION_SEED).resize((width, height))
    image = Image.new('RGBA', (width, height), (11, 8, 18, 255))
    image.alpha_composite(asset)
    # Cursor restoration cannot undo a text scroll caused by image overflow.
    strips = [(1, 1, (0, 0, width, strip_height)),
              (rows-1, 1, (0, bottom_y, width, height)),
              (2, 1, (0, ch, cw*2, bottom_y)),
              (2, columns-1, (width-cw*2, ch, width, bottom_y))]
    return ''.join(f'\x1b7\x1b[{row};{col}H' + encode_image(image.crop(box))
                   + '\x1b8' for row, col, box in strips)
