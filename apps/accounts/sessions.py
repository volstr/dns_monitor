"""Сессии в Redis — Q-D20, ТЗ 3, 11.11.

Штатная таблица сессий не используется: по Q-D20 сессии живут в Redis. База — **состояния**, а не
кэша. Это важно и записано в 11.12 прямо: «не кэшируются никогда … данные аутентификации», а сброс
`cache:*` обязан быть безопасной операцией под нагрузкой. Сессия в кэше означала бы, что сброс кэша
выкидывает всех работающих людей.

Имя ключа и срок жизни берутся из `core.ports.state_store.session`, поэтому «сессия без срока
жизни» здесь невозможна так же, как любой другой ключ (11.11).

**Срок скользящий.** ТЗ 3 описывает его как срок бездействия — «через сколько бездействия
потребуется войти заново», — и прототип повторяет ту же формулировку. Поэтому каждая запись
продлевает ключ, а `SESSION_SAVE_EVERY_REQUEST` включён: иначе человек, непрерывно работающий
восемь часов, был бы выброшен посреди работы.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, SessionBase

from apps.catalog.models import GlobalSettings
from core.adapters.redis_state import RedisStateStore
from core.ports.state_store import StateStore, session

_store: StateStore | None = None


def state_store() -> StateStore:
    """Одно подключение на процесс: пул соединений не создаётся на каждый запрос."""
    global _store
    if _store is None:
        _store = RedisStateStore.from_url(
            settings.ENV.redis_url, db=settings.ENV.redis_db_state
        )
    return _store


def session_lifetime() -> timedelta:
    """Срок сессии — настройка продукта (ТЗ 3: 8 часов / 7 дней / 30 дней)."""
    return timedelta(seconds=GlobalSettings.objects.get().session_lifetime_seconds)


class SessionStore(SessionBase):
    """Хранилище сессий поверх порта состояния."""

    def __init__(self, session_key: str | None = None) -> None:
        super().__init__(session_key)
        self._store = state_store()

    def load(self) -> dict[str, Any]:
        if self.session_key is None:
            return {}
        raw = self._store.recall(session(self.session_key, session_lifetime()))
        if raw is None:
            # Срок истёк или сессию завершили. Ключ снимается, чтобы не выдавать его повторно.
            self._session_key = None
            return {}
        return dict(self.decode(raw))

    def exists(self, session_key: str) -> bool:
        return self._store.recall(session(session_key, session_lifetime())) is not None

    def create(self) -> None:
        for _ in range(10_000):
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return
        message = "не удалось получить свободный ключ сессии"
        raise RuntimeError(message)

    def save(self, must_create: bool = False) -> None:
        if self.session_key is None:
            self.create()
            return
        key = session(self.session_key, session_lifetime())
        if must_create and self._store.recall(key) is not None:
            raise CreateError
        self._store.remember(key, self.encode(self._get_session(no_load=must_create)))

    def delete(self, session_key: str | None = None) -> None:
        key = session_key if session_key is not None else self.session_key
        if key is None:
            return
        # `release` удаляет ключ независимо от того, чем он был записан (11.11).
        self._store.release(session(key, session_lifetime()))

    def get_expiry_age(self, **kwargs: Any) -> int:
        """Возраст cookie совпадает со сроком ключа: иначе одно переживало бы другое."""
        return int(session_lifetime().total_seconds())

    @classmethod
    def clear_expired(cls) -> None:
        """Ничего не делает: сроки снимает сам Redis.

        ТЗ 11.11 требует этого прямо — «блокировка снимается сама истечением срока ключа,
        отдельного задания по расписанию быть не должно». То же и с сессиями.
        """
