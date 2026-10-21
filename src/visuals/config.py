"""Validated visual preferences; loading never writes configuration."""
from dataclasses import dataclass, asdict
import json
import math
import os
from pathlib import Path

@dataclass(frozen=True)
class VisualConfig:
    theme: str = "neon"
    renderer: str = "kitty"
    glow: float = 0.45
    animation: bool = True
    fps: int = 30

    def __post_init__(self):
        if self.theme != "neon" or self.renderer not in ("auto", "ansi", "kitty", "sixel"):
            raise ValueError("Unsupported visual theme or renderer")
        if isinstance(self.glow, bool) or not isinstance(self.glow, (int, float)) or not math.isfinite(self.glow) or not 0 <= self.glow <= 1:
            raise ValueError("glow must be between 0 and 1")
        if type(self.animation) is not bool or type(self.fps) is not int or not 1 <= self.fps <= 30:
            raise ValueError("animation must be boolean; fps must be 1..30")

    @staticmethod
    def path() -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "cmdvault/visuals.json"

    @classmethod
    def load(cls):
        path = cls.path()
        if not path.exists():
            return cls()
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid visuals.json") from error

    def save(self) -> None:
        import tempfile
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".visuals-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(asdict(self), output, indent=2)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
