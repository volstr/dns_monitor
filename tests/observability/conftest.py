"""
Приёмник событий Sentry для тестов — ТЗ 11.14, критерии 69-74.

Клиент Sentry подключается по-настоящему, а вместо сети ему подставляется приёмник, который
складывает события в список. Патчить пути импорта в этом проекте нельзя (`CLAUDE.md`), и здесь
это не нужно: `transport` — штатный параметр `sentry_sdk.init`, то есть тот же приём, что и
подстановка часов или резолвера портом.

Проверять нужно именно **отправленное**, а не намерение отправить: перечень «что не
отправляется никогда» из 11.14 иначе остаётся обещанием.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from core.observability.setup import configure_sentry

#: Формат настоящий, проект несуществующий. Сеть всё равно не используется: события забирает
#: приёмник ниже.
TEST_DSN = "https://publickey@sentry.example/42"


class Recorder(Transport):
    """
    Приёмник вместо сети. Хранит события в том виде, в каком они ушли бы наружу.

    Наследник `Transport`, а не функция: функциональные приёмники в клиенте 2.x объявлены
    устаревшими, и событие здесь берётся из конверта — ровно то, что ушло бы по сети.
    """

    def __init__(self) -> None:
        super().__init__({"dsn": TEST_DSN})
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(dict(item.payload.json or {}))

    def flush(self, timeout: float, callback: Any = None) -> None:
        del timeout, callback

    def kill(self) -> None:
        pass

    @property
    def last(self) -> dict[str, Any]:
        assert self.events, "ни одного события не отправлено"
        return self.events[-1]

    def text(self) -> str:
        """Всё отправленное одной строкой — для проверки «здесь не должно быть секрета»."""
        return repr(self.events)


class RefusingTransport(Transport):
    """Приёмник, который всегда отказывает, — недоступный Sentry (критерий 73)."""

    def __init__(self) -> None:
        super().__init__({"dsn": TEST_DSN})

    def capture_envelope(self, envelope: Envelope) -> None:
        del envelope
        raise ConnectionError("sentry недоступен")

    def flush(self, timeout: float, callback: Any = None) -> None:
        del timeout, callback

    def kill(self) -> None:
        pass


@pytest.fixture
def broken_sentry() -> Iterator[None]:
    """Подключённый Sentry, до которого не достучаться."""
    configure_sentry(
        dsn=TEST_DSN, environment="test", traces_sample_rate=0.0, transport=RefusingTransport()
    )
    yield
    sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture
def sentry() -> Iterator[Recorder]:
    """Подключённый Sentry с приёмником вместо сети."""
    recorder = Recorder()
    configure_sentry(
        dsn=TEST_DSN,
        environment="test",
        traces_sample_rate=0.0,
        release="0.1.0-test",
        transport=recorder,
    )
    yield recorder
    sentry_sdk.get_client().flush()
    # Следующий тест не должен получить клиента от предыдущего: критерий 67 проверяет, что при
    # пустом DSN отправки нет вообще, и подключённый клиент сделал бы его ложно зелёным.
    configure_sentry(dsn=None, environment="test", traces_sample_rate=0.0)
    sentry_sdk.get_global_scope().set_client(None)
