"""Smooth raw per-frame people counts before they reach the state machine."""

from __future__ import annotations

from collections import deque


class CountSmoother:
    """Rolling median of people counts over the last `window` seconds.

    A median ignores single-frame dropouts (missed detection, brief occlusion)
    and single-frame false positives, without lagging as much as a mean.
    With an even number of samples it takes the lower middle value, so a
    50/50 split between 0 and 1 reads as 0 (conservative about presence).
    """

    def __init__(self, window: float = 5.0) -> None:
        if window <= 0:
            raise ValueError("window must be > 0")
        self.window = window
        self._buf: deque[tuple[float, int]] = deque()

    def update(self, ts: float, count: int) -> int:
        self._buf.append((ts, count))
        while self._buf and self._buf[0][0] <= ts - self.window:
            self._buf.popleft()
        values = sorted(c for _, c in self._buf)
        return values[(len(values) - 1) // 2] if values else 0
