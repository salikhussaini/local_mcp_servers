from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class TtlCache(Generic[T]):
    ttl_seconds: float
    _value: T | None = None
    _expires_at: float = 0.0

    def get_or_set(self, factory: Callable[[], T]) -> T:
        now = time.time()
        if self._value is not None and now < self._expires_at:
            return self._value
        value = factory()
        self._value = value
        self._expires_at = now + self.ttl_seconds
        return value

    def clear(self) -> None:
        self._value = None
        self._expires_at = 0.0
