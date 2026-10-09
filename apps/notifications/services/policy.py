"""
Уведомлять ли о событии — ТЗ 8.1, решения Q-D7 и Q-D8.

Таблица 8.1 переписана здесь явным отображением, чтобы код читался как спецификация: режим →
какие события порождают уведомление. Ветвления `if` по типам событий разъехались бы с ТЗ на
первой же правке.

Отказ — штатный исход с названной причиной, а не исключение (`CLAUDE.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.catalog.models import NotificationMode
from apps.monitoring.models import EventType

#: Таблица 8.1 дословно: какие события уведомляют в каждом режиме.
_BY_MODE: dict[str, frozenset[str]] = {
    NotificationMode.NONE.value: frozenset(),
    NotificationMode.ADDRESS_CHANGE.value: frozenset(
        {EventType.ADDRESS_APPEARED.value, EventType.ADDRESS_GONE.value}
    ),
    NotificationMode.NOT_ALLOWED.value: frozenset({EventType.NOT_ALLOWED.value}),
}

#: «RESOLVE_FAILED уведомляет во всех режимах, кроме „Не уведомлять". Это техническая
#: неисправность, а не изменение конфигурации» (8.1). Q-D8 распространил это на восстановление.
_TECHNICAL = frozenset({EventType.RESOLVE_FAILED.value, EventType.RESOLVE_RECOVERED.value})

#: Продолжения: существуют только при наличии открывающего сообщения (Q-D7).
_CONTINUATIONS = frozenset({EventType.BACK_TO_NORMAL.value, EventType.RESOLVE_RECOVERED.value})


class SkipReason(StrEnum):
    #: Режим группы такие события не отслеживает (8.1).
    MODE = 'mode'
    #: Уведомление по этому типу не отправляется никогда — например, WATCH_STARTED (7.4).
    NEVER = 'never'
    #: Нет открывающего сообщения, отвечать не на что (Q-D7).
    NO_OPENING_MESSAGE = 'no_opening_message'
    #: Флаг «сообщать о возврате в норму» выключен (8.1).
    BACK_TO_NORMAL_DISABLED = 'back_to_normal_disabled'


@dataclass(frozen=True, slots=True)
class Send:
    """Уведомление отправляется. ``reply_to`` пусто у самостоятельного сообщения."""

    reply_to: str | None = None


@dataclass(frozen=True, slots=True)
class Skip:
    reason: SkipReason


type Decision = Send | Skip


def decide(
    *,
    event_type: str,
    mode: str,
    notify_back_to_normal: bool,
    opening_message_id: str | None = None,
) -> Decision:
    """
    Решение по одному событию.

    ``opening_message_id`` — идентификатор сообщения, открывшего инцидент или сообщившего о
    неисправности. Для продолжений он обязателен: правило Q-D7 сформулировано по данным, поэтому
    оно само покрывает и случай, когда открывающее сообщение не дошло после всех повторов.
    """
    if event_type == EventType.WATCH_STARTED.value:
        # 7.4: базовая проверка ставит отправную точку молча.
        return Skip(reason=SkipReason.NEVER)

    if mode == NotificationMode.NONE.value:
        return Skip(reason=SkipReason.MODE)

    if event_type == EventType.BACK_TO_NORMAL.value and not notify_back_to_normal:
        return Skip(reason=SkipReason.BACK_TO_NORMAL_DISABLED)

    notifies = event_type in _TECHNICAL or event_type in _BY_MODE.get(mode, frozenset())
    if event_type == EventType.BACK_TO_NORMAL.value:
        # «Уведомляет дополнительно… и только в режимах, отличных от „Не уведомлять"» (8.1).
        notifies = True
    if not notifies:
        return Skip(reason=SkipReason.MODE)

    if event_type in _CONTINUATIONS and not opening_message_id:
        return Skip(reason=SkipReason.NO_OPENING_MESSAGE)

    return Send(reply_to=opening_message_id if event_type in _CONTINUATIONS else None)
