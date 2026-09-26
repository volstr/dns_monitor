"""Отправка ошибок в Sentry — ТЗ 11.14; критерии 67-74.

**В Sentry уходит только то, что отправлено отсюда явно, плюс необработанные исключения.**
Автоматическая пересылка журнала отключена намеренно (Q-D31): 11.14 называет перечень того,
что не отправляется **никогда**, а «никогда» нельзя обеспечить правилом «шлём всё, что попало
в `logger.error`». Достаточно однажды залогировать доменный отказ на этом уровне — и квота
выгорает на `NXDOMAIN`.

Отсюда форма этого модуля: отправка — это вызов одной функции с **причиной**, а не побочный
эффект логирования.

Отпечаток собирается по причине, а не по объекту (11.14). Поэтому `Failure.cause` — короткий
идентификатор отказавшего механизма, а домен, группа и проверка идут метками и в отпечаток не
попадают: иначе пятьсот доменов дали бы пятьсот отдельных событий.

Sentry никогда не влияет на работу приложения: отправка асинхронная, а любой отказ самого
клиента проглатывается здесь (критерий 73).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

import sentry_sdk

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Failure:
    """Отказ, о котором должен узнать разработчик.

    ``cause`` задаёт группировку: события с одной причиной склеиваются в одно независимо от
    того, сколько объектов они задели.
    """

    cause: str
    message: str
    #: Сколько объектов задето. Для обхода это число доменов — 11.14 требует его в контексте,
    #: а не пятисот отдельных событий (критерий 72).
    affected: int = 1
    #: Метки предметной области: домен, группа, проверка, инцидент, идентификатор обхода.
    tags: Mapping[str, str] = field(default_factory=dict)
    #: Исключение, если отказ пришёл с ним. Без него уходит сообщение.
    error: BaseException | None = None


def report(failure: Failure) -> None:
    """Отправить отказ. Молча ничего не делает, если Sentry не подключён (критерий 67)."""
    try:
        _send(failure)
    except Exception:
        # Сюда попадают только аварии самого клиента: доставка асинхронная и свои ошибки
        # глотает сама. Пишем в журнал и продолжаем работать (11.14, критерий 73).
        logger.warning("не удалось отправить отказ в Sentry", extra={"cause": failure.cause})


def _send(failure: Failure) -> None:
    with sentry_sdk.new_scope() as scope:
        # Только причина, без `{{ default }}`: с ним Sentry добавил бы к отпечатку трассировку,
        # и пятьсот доменов с чуть разными сообщениями об ошибке снова разъехались бы по разным
        # событиям — ровно то, что запрещает критерий 72.
        scope.fingerprint = [failure.cause]
        scope.set_tag("cause", failure.cause)
        scope.set_context("affected", {"count": failure.affected})
        for name, value in failure.tags.items():
            scope.set_tag(name, value)
        if failure.error is not None:
            sentry_sdk.capture_exception(failure.error)
        else:
            sentry_sdk.capture_message(failure.message, level="error")
