"""Self-contained, state-driven Sync Pulse artwork for cmdvault.

No network access, terminal writes or threads. Caller owns the render loop.
Pillow is imported lazily so ANSI-only users need not install it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from math import exp, pi, sin
from time import monotonic


class SyncState(StrEnum):
    READY = "ready"  # Authenticated, real history sync not yet implemented.
    SYNCED = "synced"
    SYNCING = "syncing"
    ERROR = "error"
    OFFLINE = "offline"
    DISABLED = "disabled"


COLORS = {
    SyncState.READY: (144, 102, 177),
    SyncState.SYNCED: (64, 230, 155),
    SyncState.SYNCING: (179, 92, 255),
    SyncState.ERROR: (255, 64, 88),
    SyncState.OFFLINE: (105, 105, 115),
    SyncState.DISABLED: (52, 52, 60),
}


@dataclass(frozen=True)
class PulseStyle:
    size: int = 96
    glow: float = 0.65
    fps: int = 12

    def __post_init__(self):
        if not 32 <= self.size <= 512:
            raise ValueError("size must be 32..512")
        if not 0 <= self.glow <= 1:
            raise ValueError("glow must be 0..1")
        if not 1 <= self.fps <= 60:
            raise ValueError("fps must be 1..60")


def intensity(state: SyncState, elapsed: float) -> float:
    elapsed = max(elapsed, 0.0)
    if state == SyncState.SYNCED:
        return 0.28 + 0.13 * (1 + sin(2 * pi * 0.3 * elapsed)) / 2
    if state == SyncState.SYNCING:
        return 0.40 + 0.55 * (1 + sin(2 * pi * 1.2 * elapsed)) / 2
    if state == SyncState.ERROR:
        t = elapsed % 1.8
        return min(1.0, 0.25 + 0.70 * max(exp(-((t - 0.2)/0.1)**2), exp(-((t - 0.52)/0.1)**2)))
    return {SyncState.READY: .38, SyncState.OFFLINE: .07, SyncState.DISABLED: .01}[state]


@lru_cache(maxsize=192)
def _render(state: SyncState, size: int, glow_level: int, pulse_step: int):
    from PIL import Image, ImageDraw, ImageFilter

    s = size * 2
    cx = cy = s / 2
    r = s * .29
    color = COLORS[state]
    power = glow_level / 100 * pulse_step / 100
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Diffuse luminous aura; no hard colored outline.
    for mult, blur, alpha in [(1.32, .115, 210), (1.18, .075, 180), (1.06, .032, 125)]:
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        rr = r * mult
        d.ellipse((cx-rr, cy-rr, cx+rr, cy+rr), fill=(*color, round(alpha * power)))
        layer = layer.filter(ImageFilter.GaussianBlur(s * blur))
        canvas = Image.alpha_composite(canvas, layer)

    # Metallic sphere, antialiasing via 2x supersampling.
    sphere = Image.new("RGBA", (s, s))
    pix = sphere.load()
    for y in range(s):
        ny = (y - cy)/r
        for x in range(s):
            nx = (x - cx)/r
            d2 = nx*nx + ny*ny
            if d2 >= 1:
                continue
            highlight = exp(-((nx+.36)**2/.16 + (ny+.46)**2/.10))
            base = round(7 + 17 * (1-d2) + 29 * highlight)
            # A little smooth antialiasing at the sphere perimeter.
            edge = min(1.0, max(0.0, (1 - d2) * r / 1.2))
            pix[x, y] = (base, base, base+5, round(255 * edge))
    canvas = Image.alpha_composite(canvas, sphere)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


class SyncPulse:
    def __init__(self, style: PulseStyle | None = None, state: SyncState = SyncState.DISABLED):
        self.style = style or PulseStyle()
        self.state = SyncState(state)
        self._since = monotonic()

    def set_state(self, state: SyncState | str) -> None:
        value = SyncState(state)
        if value != self.state:
            self.state = value
            self._since = monotonic()

    @property
    def label(self) -> str:
        return self.state.value.upper()

    @property
    def animated(self) -> bool:
        return self.state in (SyncState.SYNCED, SyncState.SYNCING, SyncState.ERROR)

    def frame(self, now: float | None = None):
        now = monotonic() if now is None else now
        level = round(intensity(self.state, now-self._since) * 100)
        return _render(self.state, self.style.size, round(self.style.glow*100), level)

    def frame_png(self, now: float | None = None) -> bytes:
        from io import BytesIO
        output = BytesIO()
        self.frame(now).save(output, format="PNG")
        return output.getvalue()
