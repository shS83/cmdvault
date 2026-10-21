"""Passive detection: never steal input or leave delayed probe replies behind."""
from dataclasses import dataclass
import os
from typing import Mapping, TextIO

@dataclass(frozen=True)
class Capabilities:
    tty: bool
    term: str
    truecolor: bool = False
    kitty: bool = False
    sixel: bool = False
    reason: str = "No safely verified raster protocol; ANSI fallback"

def detect(output: TextIO, env: Mapping[str, str] = None) -> Capabilities:
    env = os.environ if env is None else env
    tty = output.isatty()
    term = env.get("TERM", "unknown")
    direct = not any(env.get(key) for key in ("TMUX", "STY")) and not term.startswith(("screen", "tmux"))
    kitty = tty and direct and term == "xterm-kitty" and bool(env.get("KITTY_WINDOW_ID"))
    return Capabilities(tty, term,
                        tty and term != "dumb" and (kitty or env.get("COLORTERM", "") in ("truecolor", "24bit")),
                        kitty=kitty,
                        reason="Direct Kitty session" if kitty else "Unverified graphics transport; ANSI fallback")

def select(requested: str, caps: Capabilities, *, raster_layout: bool = False):
    available = caps.tty and raster_layout
    backend = "kitty" if available and caps.kitty else "sixel" if available and caps.sixel else "ansi"
    if requested == "ansi":
        return "ansi", ""
    if requested == "auto":
        return backend, ""
    if available and getattr(caps, requested, False):
        return requested, ""
    return "ansi", f"Requested {requested} unavailable for this layout; using ANSI. {caps.reason}"
