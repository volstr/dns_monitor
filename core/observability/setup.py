"""
Подключение Sentry — ТЗ 11.14; критерии 67, 68, 73, 74.

Подключение необязательное. Пустой `SENTRY_DSN` — не ошибка: приложение работает как обычно и
не пишет в журнал попыток отправки (критерий 67). Неверный формат DSN — ошибка конфигурации, и
ловится она раньше, в `config/env.py`, вместе со всеми остальными переменными (критерий 68):
приложение не стартует и называет переменную.

`transport` вынесен в параметр не ради гибкости, а ради проверяемости: тесты критериев 69-74
подставляют сюда приёмник, который складывает конверты в список. Патчить пути импорта в этом
проекте нельзя (`CLAUDE.md`), а увидеть отправленное событие нужно — иначе перечень
«что не отправляется никогда» останется обещанием.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from core.observability.scrub import scrub_event

#: Журнал в Sentry не превращается. Записи уровня INFO и выше остаются «хлебными крошками» —
#: они видны внутри события и помогают понять, что происходило перед отказом, — но сами
#: событиями не становятся. Почему так: Q-D31 и 11.14, раздел «что не отправляется никогда».
LOGGING_INTEGRATION = LoggingIntegration(level=None, event_level=None)


def configure_sentry(
    *,
    dsn: str | None,
    environment: str,
    traces_sample_rate: float,
    release: str = '',
    transport: Transport | Callable[[Event], None] | None = None,
) -> bool:
    """Подключить Sentry. Возвращает, подключён ли он на самом деле."""
    if not dsn:
        # Критерий 67: ни инициализации, ни попыток отправки, ни записи о них.
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        # 11.14: «версия сборки указывается как релиз, иначе невозможно понять, какое
        # развёртывание сломало работу». Пустая строка означает «не задана» — не отправляем.
        release=release or None,
        integrations=[DjangoIntegration(), LOGGING_INTEGRATION],
        # Личные данные не собираются автоматически: почтовый адрес участника в событие не
        # идёт (11.14), а вырезать то, чего не добавляли, надёжнее.
        send_default_pii=False,
        before_send=_before_send,
        transport=transport,
    )
    return True


def _before_send(event: Event, hint: Hint) -> Event | None:
    """Последняя точка перед отправкой. Чистая работа делегируется `scrub_event`."""
    del hint
    # `Event` — это `TypedDict` Sentry, а чистка обходит событие как обычный словарь: набор
    # ключей задаётся форматом чужого клиента и меняется от версии к версии, перечислять его
    # у себя означало бы пропустить секрет в поле, о котором мы не знали.
    return cast('Event', scrub_event(dict(event)))
