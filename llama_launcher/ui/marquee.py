"""Width-limited scrolling text: holds the start, drops one leading character per step until the end fits,
holds there, then restarts. Width is whatever `measure` returns (pixels in the tray)."""

from __future__ import annotations

from typing import Callable


class Marquee:
    def __init__(self, limit: int, measure: Callable[[str], int] = len, step_ms: int = 250,
                 start_hold_ms: int = 1000, hold_ms: int = 1000):
        self.limit = limit
        self.measure = measure
        self.step_ms = step_ms
        self.start_hold_ms = start_hold_ms
        self.hold_ms = hold_ms
        self.text = ""
        self.offset = 0
        self._held = 0

    @property
    def scrolls(self) -> bool:
        return self.measure(self.text) > self.limit

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text, self.offset, self._held = text, 0, 0

    def view(self) -> str:
        """The longest run from the current offset that fits the limit."""
        rest = self.text[self.offset:]
        n = len(rest)
        while n > 0 and self.measure(rest[:n]) > self.limit:
            n -= 1
        return rest[:n]

    def tick(self) -> None:
        """Advance by one step (step_ms of time)."""
        if not self.scrolls:
            return
        self._held += self.step_ms
        if self.offset == 0:
            if self._held >= self.start_hold_ms:  # the start stays readable for start_hold_ms
                self.offset, self._held = 1, 0
        elif self.measure(self.text[self.offset:]) > self.limit:
            self.offset, self._held = self.offset + 1, 0
        elif self._held >= self.hold_ms:  # the end stays for hold_ms, then back to the start
            self.offset, self._held = 0, 0
