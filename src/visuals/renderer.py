"""One owner for terminal output and cleanup."""
from abc import ABC, abstractmethod
from typing import TextIO, Sequence
from .config import VisualConfig
from .detector import Capabilities

class Renderer(ABC):
    def __init__(self, output: TextIO, config: VisualConfig, caps: Capabilities):
        self.output, self.config, self.caps = output, config, caps
        self.width, self.height = 80, 24
        self.active = False

    def emit(self, value: str) -> None:
        if self.caps.tty:
            self.output.write(value)
            self.output.flush()

    def initialize(self) -> None:
        self.active = True
        self.emit("\x1b7\x1b[?1049h\x1b[?25l")

    @abstractmethod
    def draw(self, lines: Sequence[str], phase: float = 0) -> None:
        pass

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = max(1, width), max(1, height)
        self.emit("\x1b[2J")

    def cleanup(self) -> None:
        if self.active:
            self.emit("\x1b[0m\x1b[?25h\x1b[?1049l\x1b8")
            self.active = False

    def __enter__(self):
        try:
            self.initialize()
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, *exc):
        self.cleanup()
