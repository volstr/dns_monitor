"""
Тестовый кэш: сроки жизни двигаются часами, а не ожиданием.

Позволяет проверить истечение ключа без `sleep` — время сдвигает ``FakeClock``, как и во всех
остальных тестах. Отдельно считает попадания и промахи: без этого «значение взято из кэша» и
«значение пересчитано и совпало» неразличимы, а проверять нужно именно первое.
"""

from __future__ import annotations

from datetime import datetime

from core.ports.cache import PREFIX, Cache, CacheKey
from core.ports.clock import Clock


class FakeCache(Cache):
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._values: dict[str, str] = {}
        self._expires_at: dict[str, datetime] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> str | None:
        if not self._alive(key.name):
            self.misses += 1
            return None
        self.hits += 1
        return self._values[key.name]

    def put(self, key: CacheKey, value: str) -> None:
        self._values[key.name] = value
        self._expires_at[key.name] = self._clock.now() + key.ttl

    def forget(self, key: CacheKey) -> None:
        self._values.pop(key.name, None)
        self._expires_at.pop(key.name, None)

    def flush(self) -> int:
        names = [name for name in self._values if name.startswith(PREFIX)]
        for name in names:
            self._values.pop(name, None)
            self._expires_at.pop(name, None)
        return len(names)

    def keys(self) -> frozenset[str]:
        """Что лежит в кэше сейчас — для проверок в тестах."""
        return frozenset(name for name in self._values if self._alive(name))

    def _alive(self, name: str) -> bool:
        expires_at = self._expires_at.get(name)
        if expires_at is None:
            return False
        if expires_at <= self._clock.now():
            self._values.pop(name, None)
            del self._expires_at[name]
            return False
        return True
