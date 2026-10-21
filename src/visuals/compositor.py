"""Monotonic scheduling, no threads and no busy waiting."""
import time

class FrameClock:
    def __init__(self, fps: int = 12):
        self.interval = 1 / fps
        self.next_frame = 0.0

    def due(self, now: float, *, idle: bool = False) -> bool:
        if now < self.next_frame:
            return False
        self.next_frame = now + (max(1.0, self.interval) if idle else self.interval)
        return True

    def delay(self, now: float) -> float:
        return max(0.0, self.next_frame - now)
