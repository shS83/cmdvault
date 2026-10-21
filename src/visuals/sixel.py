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
        # No portable under-text layering. Reserve top four text rows; static
        # image only, no cursor/scroll mode mutations or full-screen raster.
        super().draw(["", "", "", *lines], phase)
        key = (self.width, self.height)
        if getattr(self, "asset_key", None) != key:
            image = neon_asset(min(512,(self.width-2)*8), 32, self.config.glow).convert("RGB")
            self.emit("\x1b7\x1b[2;2H" + encode_sixel(*image.size, image.tobytes()) + "\x1b8")
            self.asset_key = key
