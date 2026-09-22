"""Тестовое хранилище состояния: сроки жизни двигаются часами, а не ожиданием.

Позволяет проверять истечение блокировок и кулдаунов без `sleep`: время сдвигает ``FakeClock``,
как и во всех остальных тестах.
"""

from __future__ import annotations

from datetime import datetime

from core.ports.clock import Clock
from core.ports.state_store import StateKey, StateStore


class StateStoreUnavailable(RuntimeError):
    """Имитация аварии по 11.13."""


class FakeStateStore(StateStore):
    def __init__(self, clock: Clock, *, available: bool = True) -> None:
        self._clock = clock
        self._expires_at: dict[str, datetime] = {}
        #: Позволяет проверить поведение при недоступном хранилище (11.13).
        self.available = available

    def acquire(self, key: StateKey) -> bool:
        self._guard()
        if self._alive(key.name):
            return False
        self._expires_at[key.name] = self._clock.now() + key.ttl
        return True

    def release(self, key: StateKey) -> None:
        self._guard()
        self._expires_at.pop(key.name, None)

    def is_held(self, key: StateKey) -> bool:
        self._guard()
        return self._alive(key.name)

    def ping(self) -> None:
        self._guard()

    def held_keys(self) -> frozenset[str]:
        """Что занято сейчас — для проверок в тестах."""
        return frozenset(name for name in self._expires_at if self._alive(name))

    def _alive(self, name: str) -> bool:
        expires_at = self._expires_at.get(name)
        if expires_at is None:
            return False
        if expires_at <= self._clock.now():
            del self._expires_at[name]
            return False
        return True

    def _guard(self) -> None:
        if not self.available:
            message = "хранилище рабочего состояния недоступно"
            raise StateStoreUnavailable(message)
