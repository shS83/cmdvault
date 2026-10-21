"""A short, self-cleaning test frame; no terminal input is read."""
import shutil
import time
from .ansi import AnsiRenderer
from .kitty import KittyRenderer
from .sixel import SixelRenderer
from .detector import detect, select

def run(config, output, *, caps=None, duration=2.0):
    caps = caps or detect(output)
    backend, notice = select(config.renderer, caps, raster_layout=True)
    if backend != "ansi":
        try:
            import PIL
        except ImportError:
            backend, notice = "ansi", "Pillow unavailable; using ANSI"
    size = shutil.get_terminal_size((80,24))
    lines = ["cmdvault Neon", f"Terminal: {caps.term}", f"Backend: {backend}; fallback: ANSI", f"Theme: {config.theme}; glow: {config.glow:.0%}", f"Animation: {config.animation}; target: {config.fps} FPS", f"Dimensions: {size.columns} x {size.lines}", notice or caps.reason]
    renderer = {"ansi": AnsiRenderer, "kitty": KittyRenderer, "sixel": SixelRenderer}[backend](output, config, caps)
    if not caps.tty:
        renderer.draw(lines)
        return 0
    with renderer:
        renderer.resize(*size)
        renderer.draw(lines)
        start = time.monotonic()
        # Kitty owns frame timing; tick only uploads newly prepared assets.
        while config.animation and time.monotonic()-start < duration:
            time.sleep(min(1/config.fps, max(.001, duration-(time.monotonic()-start))))
            if hasattr(renderer,"tick"):
                renderer.tick()
            current = shutil.get_terminal_size((80,24))
            if current != size:
                size = current
                lines[5] = f"Dimensions: {size.columns} x {size.lines}"
                renderer.resize(*size)
                renderer.draw(lines)
    return 0
