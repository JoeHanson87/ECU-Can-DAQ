"""Ring buffer for time-series data."""
from __future__ import annotations

from collections import deque
from typing import Any
import time


class RingBuffer:
    """Thread-safe-ish (single writer, multiple readers via snapshot) ring buffer."""

    def __init__(self, capacity: int = 10000):
        self._capacity = capacity
        self._buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._overruns = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def overruns(self) -> int:
        return self._overruns

    def append(self, item: dict[str, Any]) -> None:
        if len(self._buffer) >= self._capacity:
            self._overruns += 1
        self._buffer.append(item)

    def snapshot(self, max_items: int | None = None) -> list[dict[str, Any]]:
        if max_items is None:
            return list(self._buffer)
        return list(self._buffer)[-max_items:]

    def clear(self) -> None:
        self._buffer.clear()
        self._overruns = 0

    def latest(self) -> dict[str, Any] | None:
        if not self._buffer:
            return None
        return self._buffer[-1]
