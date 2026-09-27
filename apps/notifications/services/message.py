"""
Составление сообщения — ТЗ 8.4.

Порядок строк фиксирован: сначала факты, потом что делать, потом ссылка. Telegram не умеет ни
таблиц, ни цвета, поэтому смысл несут порядок строк и моноширинный шрифт для адресов (11.4).

Названия событий — утверждённая таблица Q-C11. Строка 4 по Q-D10 показывает **актуальный набор**,
а не последний ответ: статусы считаются по нему же, иначе сообщение спорило бы само с собой.

Сокращение при переполнении по 8.4: режется **список адресов**, комментарий не режется никогда.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.monitoring.models import EventType
from core.text import escape_html

#: Предел сообщения Telegram (8.4).
MAX_LENGTH = 4096
#: «первые 8 и „и ещё N"» (8.4).
ADDRESS_LIMIT = 8
#: «перечисление до 10 имён» в сводке (8.6).
SUMMARY_NAME_LIMIT = 10

#: Заголовки из утверждённой таблицы Q-C11.
HEADERS: dict[str, str] = {
    EventType.ADDRESS_APPEARED.value: "Появился адрес",
    EventType.ADDRESS_GONE.value: "Адрес пропал",
    EventType.NOT_ALLOWED.value: "Адрес вне разрешённых",
    EventType.BACK_TO_NORMAL.value: "Снова в норме",
    EventType.RESOLVE_FAILED.value: "Домен не резолвится",
    EventType.RESOLVE_RECOVERED.value: "Домен снова резолвится",
}

#: Знак у изменившегося адреса — строка 3 в 8.4: появившийся, пропавший, несовпавший.
SIGNS: dict[str, str] = {
    EventType.ADDRESS_APPEARED.value: "+",
    EventType.ADDRESS_GONE.value: "−",
    EventType.NOT_ALLOWED.value: "✕",
    EventType.BACK_TO_NORMAL.value: "−",
}


@dataclass(frozen=True, slots=True)
class MessageInput:
    event_type: str
    domain_name: str
    domain_url: str
    #: Изменившиеся адреса — строка 3.
    changed: tuple[str, ...] = ()
    #: Актуальный набор — строка 4 (Q-D10).
    current: tuple[str, ...] = ()
    group_name: str | None = None
    domain_comment: str = ""
    group_comment: str = ""
    #: Длительность инцидента для «Снова в норме» (критерий 17).
    duration: str | None = None
    #: Сколько проверок подряд домен не резолвится — для RESOLVE_FAILED.
    failed_attempts: int | None = None


def comment_of(message: MessageInput) -> str:
    """Правило подстановки из 8.4: комментарий домена, иначе группы, иначе строки нет."""
    return message.domain_comment.strip() or message.group_comment.strip()


def compose(
    message: MessageInput,
    *,
    include_current: bool = True,
    include_comment: bool = True,
    include_link: bool = True,
) -> str:
    """
    Собрать текст. Флаги — три переключателя из общих настроек (8.4, Q-C4).

    Список адресов режется **всегда**, а не только при переполнении: критерий 20 требует
    сокращения у домена с тридцатью адресами, и такой список нечитаем в чате задолго до предела
    в 4096 символов. Сам предел остаётся страховкой. Расхождение 8.4 и критерия 20 записано
    в Q-D26.

    Комментарий не режется никогда (8.4). Переполнение им одним невозможно: по 4.0 это
    `varchar(2000)`.
    """
    return _build(message, include_current, include_comment, include_link, limit=ADDRESS_LIMIT)


def _build(
    message: MessageInput,
    include_current: bool,
    include_comment: bool,
    include_link: bool,
    *,
    limit: int | None,
) -> str:
    lines = [
        f"<b>{escape_html(HEADERS.get(message.event_type, message.event_type))}</b>",
        f"<code>{escape_html(message.domain_name)}</code>",
    ]

    sign = SIGNS.get(message.event_type, "")
    for address in _shorten(message.changed, limit):
        lines.append(f"{sign} <code>{escape_html(address)}</code>".strip())

    if message.duration:
        lines.append(f"Проблема держалась {escape_html(message.duration)}.")
    if message.failed_attempts:
        lines.append(f"Не резолвится {message.failed_attempts} проверок подряд.")

    if include_current and message.current:
        shown = ", ".join(
            f"<code>{escape_html(address)}</code>"
            for address in _shorten(message.current, limit)
        )
        lines.append(f"Актуальные адреса: {shown}")
    if message.group_name:
        lines.append(f"Группа: {escape_html(message.group_name)}")

    comment = comment_of(message)
    if include_comment and comment:
        lines.append(escape_html(comment))
    if include_link:
        lines.append(f'<a href="{escape_html(message.domain_url)}">Открыть домен →</a>')

    return "\n".join(lines)


def compose_outage(*, component: str, detail: str | None = None) -> str:
    """
    Сообщение об аварии инфраструктуры — ТЗ 11.13.

    Главное требование раздела: остановка проверок обязана быть заметной. «Инструмент
    мониторинга, который молча перестал мониторить, опаснее отсутствия инструмента: люди
    продолжают считать, что за доменами следят.»
    """
    lines = [
        "<b>Проверки остановлены</b>",
        f"Не отвечает компонент инфраструктуры: <code>{escape_html(component)}</code>.",
        "Пока он не восстановлен, за доменами никто не следит.",
    ]
    if detail:
        lines.append(escape_html(detail))
    return "\n".join(lines)


def compose_reminder(
    *, domain_name: str, age: str, unmatched: tuple[str, ...], domain_url: str
) -> str:
    """
    Напоминание по открытому инциденту — ТЗ 8.5.

    Состав задан там же: возраст инцидента, **текущий** список несовпавших адресов (он мог
    измениться с момента открытия) и ссылка. Комментарий домена не повторяется — он уже был
    в первом сообщении.
    """
    lines = [
        "<b>Расхождение держится</b>",
        f"<code>{escape_html(domain_name)}</code>",
        f"Возраст: {escape_html(age)}",
    ]
    if unmatched:
        shown = ", ".join(
            f"<code>{escape_html(address)}</code>" for address in _shorten(unmatched, ADDRESS_LIMIT)
        )
        lines.append(f"Вне разрешённых: {shown}")
    lines.append(f'<a href="{escape_html(domain_url)}">Открыть домен →</a>')
    return "\n".join(lines)


def compose_summary(*, event_type: str, domain_names: tuple[str, ...], journal_url: str) -> str:
    """
    Сводное сообщение окна слияния — состав из 8.6 дословно.

    «Тип события, число затронутых доменов, перечисление до 10 имён, „и ещё N", ссылка на журнал
    с уже применённым фильтром». Общего числа проверенных здесь нет намеренно: формулировка 7.4
    «12 доменов из 200» — иллюстрация, а не спецификация формата (Q-D9).
    """
    header = HEADERS.get(event_type, event_type)
    shown = _shorten(domain_names, SUMMARY_NAME_LIMIT)
    lines = [
        f"<b>{escape_html(header)}</b>",
        f"Затронуто доменов: {len(domain_names)}",
        ", ".join(f"<code>{escape_html(name)}</code>" for name in shown),
        f'<a href="{escape_html(journal_url)}">Открыть журнал →</a>',
    ]
    return "\n".join(lines)


def _shorten(addresses: tuple[str, ...], limit: int | None) -> list[str]:
    if limit is None or len(addresses) <= limit:
        return list(addresses)
    return [*addresses[:limit], f"и ещё {len(addresses) - limit}"]
