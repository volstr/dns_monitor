"""
Боевое хранилище рабочего состояния на Redis — ТЗ 11.11.

Единственное место, где создаётся подключение к логической базе состояния. Недоступность Redis
не глушится: по 11.13 это авария, а не режим пониженной функциональности, и исключение обязано
дойти до вызывающего.
"""

from __future__ import annotations

import redis

from core.ports.state_store import StateKey, StateStore


class RedisStateStore(StateStore):
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str, db: int) -> RedisStateStore:
        return cls(redis.Redis.from_url(url, db=db, decode_responses=True))

    def acquire(self, key: StateKey) -> bool:
        # SET NX EX — захват и срок жизни одной командой: промежутка, в котором ключ уже есть,
        # а срока у него ещё нет, не существует.
        return bool(self._client.set(key.name, "1", nx=True, ex=key.ttl))

    def release(self, key: StateKey) -> None:
        self._client.delete(key.name)

    def is_held(self, key: StateKey) -> bool:
        return bool(self._client.exists(key.name))

    def accumulate(self, key: StateKey, item: str) -> int:
        length = int(self._client.rpush(key.name, item))  # type: ignore[arg-type]
        if length == 1:
            # Только при первом добавлении: окно фиксированное и потоком событий не продлевается
            # (11.11). EXPIRE отдельной командой — RPUSH срока жизни не ставит.
            self._client.expire(key.name, key.ttl)
        return length

    def collected(self, key: StateKey) -> tuple[str, ...]:
        items = self._client.lrange(key.name, 0, -1)
        return tuple(str(item) for item in items)  # type: ignore[union-attr]

    def remember(self, key: StateKey, value: str) -> None:
        # SET EX — значение и срок жизни одной командой, как в acquire: ключа без срока не
        # возникает даже на мгновение.
        self._client.set(key.name, value, ex=key.ttl)

    def recall(self, key: StateKey) -> str | None:
        value = self._client.get(key.name)
        return None if value is None else str(value)

    def ping(self) -> None:
        self._client.ping()
