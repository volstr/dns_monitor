"""Статусы и исходы проверки — ТЗ 6.4, 11.5.

Имена статуса адреса утверждены в Q-13.8: «Совпал / Не совпал / Не актуален». Корень ``match``
уже занят нужным смыслом (``AllowlistMatch``, ``matched_entry``) и не сталкивается ни с
``resolve*``, ни с ``allowlist``.
"""

from __future__ import annotations

from enum import StrEnum


class AddressStatus(StrEnum):
    """Статус адреса. Вычисляется, не хранится (ТЗ 6.4)."""

    #: Актуален и совпал с записью белого списка.
    MATCHED = "matched"
    #: Актуален и не совпал ни с одной записью.
    UNMATCHED = "unmatched"
    #: Последнее обнаружение раньше границы `T - W`. Вопрос о совпадении не задаётся (6.4).
    STALE = "stale"


class DomainState(StrEnum):
    """Состояние домена — производная от статусов его адресов (ТЗ 6.4)."""

    NORMAL = "normal"
    DISCREPANCY = "discrepancy"
    RESOLVE_ERROR = "resolve_error"


class CheckOutcome(StrEnum):
    """Исход одной проверки — цвет отметки в колонке «История» (ТЗ 11.5)."""

    NORMAL = "normal"
    ADDRESS_SET_CHANGED = "address_set_changed"
    #: Прежнее имя `UNRESOLVED` убрано по Q-13.8: одно слово стояло в двух перечислениях.
    HAS_UNMATCHED = "has_unmatched"
    RESOLVE_ERROR = "resolve_error"
    #: Q-D5: ни один сервер не ответил. В журнал событий не пишется, в колонке — пустая отметка.
    NOT_PERFORMED = "not_performed"
