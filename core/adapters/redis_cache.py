"""
Боевой кэш на Redis — ТЗ 11.12.

Единственное место, где создаётся подключение к логической базе кэша. База **другая**, чем у
рабочего состояния, и это главное свойство адаптера: сброс `cache:*` не должен задевать
блокировки планировщика и счётчики попыток входа (критерий 62).

Недоступность Redis здесь не глушится, как и в состоянии: по 11.13 это авария, а не режим
пониженной функциональности. Кэш-промах и отказ хранилища — разные вещи, и подменять второе
первым значило бы прятать аварию.
"""

from __future__ import annotations

import redis

from core.ports.cache import PREFIX, Cache, CacheKey

#: Сколько ключей забирать за один проход SCAN при сбросе. Смысл не в скорости, а в том, чтобы
#: не блокировать Redis длинной командой: 11.12 требует безопасного сброса **под нагрузкой**,
#: а `KEYS` на большом пространстве останавливает сервер целиком.
SCAN_BATCH = 500


class RedisCache(Cache):
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str, db: int) -> RedisCache:
        return cls(redis.Redis.from_url(url, db=db, decode_responses=True))

    def get(self, key: CacheKey) -> str | None:
        value = self._client.get(key.name)
        return None if value is None else str(value)

    def put(self, key: CacheKey, value: str) -> None:
        # SET EX — значение и срок жизни одной командой: ключа без срока не возникает даже на
        # мгновение. Срок обязателен ещё и потому, что политика вытеснения кэша `volatile-lru`
        # трогает только ключи со сроком: бессрочный ключ пережил бы вытеснение (11.12, Q-A1).
        self._client.set(key.name, value, ex=key.ttl)

    def forget(self, key: CacheKey) -> None:
        self._client.delete(key.name)

    def flush(self) -> int:
        deleted = 0
        for batch in self._batches():
            deleted += int(self._client.delete(*batch))  # type: ignore[arg-type]
        return deleted

    def _batches(self) -> list[list[str]]:
        """
        Обход курсором, а не `KEYS`: последний останавливает сервер на время обхода.

        Сброс — операция под нагрузкой (11.12), и цена у неё должна быть размазанной.
        """
        batches: list[list[str]] = []
        cursor = 0
        while True:
            cursor, keys = self._client.scan(  # type: ignore[misc]
                cursor, match=f'{PREFIX}*', count=SCAN_BATCH
            )
            if keys:
                batches.append([str(key) for key in keys])
            if cursor == 0:
                return batches
