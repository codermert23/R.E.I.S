"""Basit TTL önbelleği (Qt'den bağımsız; saat enjekte edilebilir)."""
from __future__ import annotations

import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, ttl_s: float = 900.0, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl_s
        self._clock = clock
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        ts, value = item
        if self._clock() - ts >= self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, key, value) -> None:
        self._data[key] = (self._clock(), value)

    def clear(self) -> None:
        self._data.clear()
