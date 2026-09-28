"""
Журнал событий и его выгрузка — ТЗ 11.7, 4.5; решения Q-D4, Q-C11, Q-C21; критерий 49.

**Журнал один, источников два.** По Q-D4 у события всегда есть домен, а записи, у которых домена
нет по определению — действия с участниками (3.1) и остановка проверок (11.13), — живут в
`AuditRecord`. Пользователю про это знать незачем: выдача склеивает оба источника и сортирует по
времени.

Склейка идёт в Python, а не `UNION` в базе: таблицы разной формы, страница ограничена сотней
строк, и ради неё городить общий запрос смысла нет. На выгрузке предел другой — сто тысяч строк
(11.7), и он проверяется до того, как что-то собирается в память.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import Q, QuerySet
from django.utils.timezone import localtime

from apps.accounts.models import AuditAction, AuditRecord
from apps.catalog.models import NotificationMode
from apps.monitoring.models import Event, EventType
from apps.notifications.models import DeliveryStatus

#: Сколько строк показывает экран. Выгрузка не ограничена этим числом.
PAGE_SIZE = 100

#: 11.7: предел выгрузки. Больше — не выгружаем и предлагаем сузить период.
EXPORT_LIMIT = 100_000

#: 11.7: «UTF-8 с BOM, разделитель — точка с запятой» — сочетание, которое Excel открывает сам.
CSV_DELIMITER = ";"
BOM = "﻿"

EVENT_TONES = {
    EventType.ADDRESS_APPEARED: "change",
    EventType.ADDRESS_GONE: "change",
    EventType.NOT_ALLOWED: "breach",
    EventType.BACK_TO_NORMAL: "ok",
    EventType.RESOLVE_FAILED: "fail",
    EventType.RESOLVE_RECOVERED: "ok",
    EventType.WATCH_STARTED: "ok",
}

#: Сегменты фильтра из прототипа. Названия — по утверждённой таблице Q-C11.
CHANGE_TYPES = (EventType.ADDRESS_APPEARED, EventType.ADDRESS_GONE)
FAILURE_TYPES = (EventType.RESOLVE_FAILED, EventType.RESOLVE_RECOVERED)


class JournalFilter(StrEnum):
    ALL = "all"
    NOT_ALLOWED = "not_allowed"
    CHANGES = "changes"
    RESOLVE_ERRORS = "resolve_errors"


class JournalPeriod(StrEnum):
    """Q-C21: значения прототипа плюс «всё хранимое»."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    ALL = "all"


PERIOD_LENGTH = {
    JournalPeriod.TODAY: timedelta(days=1),
    JournalPeriod.WEEK: timedelta(days=7),
    JournalPeriod.MONTH: timedelta(days=30),
}


@dataclass(frozen=True, slots=True)
class JournalRow:
    """Строка журнала. Оба источника приведены к одному виду (Q-D4)."""

    occurred_at: datetime
    label: str
    tone: str
    domain_id: int | None
    domain_name: str
    group_name: str
    addresses: tuple[str, ...]
    notification_mode: str
    channel: str
    delivery: str
    #: Кто совершил действие. У событий проверки автора нет — их совершает расписание.
    author_name: str
    #: Кого коснулось действие с участником (3.1: «кто, кого, что изменил и когда»). У событий
    #: роль подлежащего играет домен, и поле пустует.
    target_name: str


@dataclass(frozen=True, slots=True)
class JournalPage:
    rows: tuple[JournalRow, ...]
    chosen: JournalFilter
    period: JournalPeriod
    search: str
    #: Строк больше, чем помещается на страницу: подсказка сузить выборку.
    truncated: bool


@dataclass(frozen=True, slots=True)
class ExportReady:
    content: bytes
    rows: int


@dataclass(frozen=True, slots=True)
class ExportRefused:
    reason: str


def journal_page(
    *,
    at: datetime,
    search: str = "",
    chosen: JournalFilter = JournalFilter.ALL,
    period: JournalPeriod = JournalPeriod.ALL,
) -> JournalPage:
    """Страница журнала с применёнными фильтрами."""
    rows = _rows(at=at, search=search, chosen=chosen, period=period, limit=PAGE_SIZE + 1)
    return JournalPage(
        rows=tuple(rows[:PAGE_SIZE]),
        chosen=chosen,
        period=period,
        search=search,
        truncated=len(rows) > PAGE_SIZE,
    )


def journal_export(
    *,
    at: datetime,
    search: str = "",
    chosen: JournalFilter = JournalFilter.ALL,
    period: JournalPeriod = JournalPeriod.ALL,
) -> ExportReady | ExportRefused:
    """
    Выгрузка текущей выборки — ТЗ 11.7, критерий 49.

    Выгружается именно **выборка с фильтрами**, а не весь журнал: об этом 11.7 говорит прямо.
    """
    events, audit = _sources(at=at, search=search, chosen=chosen, period=period)
    total = events.count() + audit.count()
    if total > EXPORT_LIMIT:
        return ExportRefused(
            reason=(
                f"В выборке {total} строк, это больше предела в {EXPORT_LIMIT}. "
                f"Сузьте период или фильтр."
            )
        )

    rows = _rows(at=at, search=search, chosen=chosen, period=period, limit=EXPORT_LIMIT)
    return ExportReady(content=_csv(rows), rows=len(rows))


def _csv(rows: list[JournalRow]) -> bytes:
    """
    CSV по 11.7: UTF-8 с BOM, точка с запятой, момент в ISO 8601.

    Колонок девять, а не восемь: последняя, «Участник», добавлена сверх перечня 11.7 по
    решению Q-D43. Перечень там закрытый и составлен из доменных событий, а журнал по Q-D4
    показывает ещё и действия с участниками, от которых 3.1 требует «кто, кого, что изменил и
    когда». Без такой колонки строка «Участник отключён» уходила в выгрузку безымянной: на
    экране видно, кого отключили, а в файле, по которому разбирают инцидент, — нет.

    Она именно последняя: восемь колонок 11.7 стоят в заданном порядке, и всё, что читает
    выгрузку по номеру колонки, продолжает работать.
    """
    buffer = io.StringIO()
    buffer.write(BOM)
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(
        [
            "Момент",
            "Домен",
            "Группа",
            "Тип события",
            "Затронутые адреса",
            "Режим уведомлений",
            "Канал доставки",
            "Результат доставки",
            "Участник",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                # 11.7: ISO 8601. Смещение в нём есть, поэтому момент однозначен, а время
                # совпадает с тем, что человек видел на экране (4.0).
                localtime(row.occurred_at).isoformat(),
                row.domain_name,
                row.group_name,
                row.label,
                " ".join(row.addresses),
                row.notification_mode,
                row.channel,
                row.delivery,
                row.target_name,
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _sources(
    *, at: datetime, search: str, chosen: JournalFilter, period: JournalPeriod
) -> tuple[QuerySet[Event], QuerySet[AuditRecord]]:
    events = Event.objects.select_related("domain", "domain__group", "notification")
    audit = AuditRecord.objects.all()

    since = _since(at, period)
    if since is not None:
        events = events.filter(occurred_at__gte=since)
        audit = audit.filter(occurred_at__gte=since)

    match chosen:
        case JournalFilter.NOT_ALLOWED:
            events = events.filter(event_type=EventType.NOT_ALLOWED)
            audit = audit.none()
        case JournalFilter.CHANGES:
            events = events.filter(event_type__in=[item.value for item in CHANGE_TYPES])
            audit = audit.none()
        case JournalFilter.RESOLVE_ERRORS:
            events = events.filter(event_type__in=[item.value for item in FAILURE_TYPES])
            audit = audit.none()
        case JournalFilter.ALL:
            pass

    if search:
        text = search.strip()
        # Адреса лежат в полезной нагрузке, поэтому поиск по ним идёт по тексту JSON, а не по
        # реестру: журнал показывает, что было, а реестр — что есть сейчас.
        events = events.filter(Q(domain__name__icontains=text) | Q(payload__icontains=text))
        # По записям об участниках ищем по именам — по тем самым, которые строка и показывает.
        # Домена и адресов у них нет, но искать «кто» и «кого» человек будет именно здесь: до
        # того как имена стали видны, искать в них было нечего, и поиск их просто отбрасывал.
        audit = audit.filter(
            Q(author_name__icontains=text) | Q(target_name__icontains=text)
        )

    return events, audit


def _since(at: datetime, period: JournalPeriod) -> datetime | None:
    length = PERIOD_LENGTH.get(period)
    return None if length is None else at - length


def _rows(
    *, at: datetime, search: str, chosen: JournalFilter, period: JournalPeriod, limit: int
) -> list[JournalRow]:
    events, audit = _sources(at=at, search=search, chosen=chosen, period=period)

    rows = [_event_row(event) for event in events.order_by("-occurred_at")[:limit]]
    rows.extend(_audit_row(record) for record in audit.order_by("-occurred_at")[:limit])
    rows.sort(key=lambda row: row.occurred_at, reverse=True)
    return rows[:limit]


def _event_row(event: Event) -> JournalRow:
    payload = event.payload or {}
    mode = payload.get("notification_mode") or ""

    return JournalRow(
        occurred_at=event.occurred_at,
        label=str(EventType(event.event_type).label),
        tone=EVENT_TONES[EventType(event.event_type)],
        domain_id=event.domain_id,
        domain_name=event.domain.name,
        group_name=event.domain.group.name if event.domain.group else "",
        addresses=tuple(str(address) for address in payload.get("addresses", ())),
        notification_mode=str(NotificationMode(mode).label) if mode else "",
        channel=event.notification.get_channel_display() if event.notification else "",
        delivery=_delivery(event),
        author_name=event.author_name,
        target_name="",
    )


def _delivery(event: Event) -> str:
    """Результат доставки словами. Пусто — уведомление не отправлялось (4.5)."""
    notification = event.notification
    if notification is None:
        return ""
    if notification.status == DeliveryStatus.SENT:
        if notification.sent_at is None:
            return "доставлено"
        return f"доставлено {localtime(notification.sent_at):%H:%M:%S}"
    if notification.status == DeliveryStatus.FAILED:
        return f"не доставлено: {notification.last_error}"
    return "в очереди"


def _audit_row(record: AuditRecord) -> JournalRow:
    """
    Запись без домена — Q-D4. В интерфейсе она стоит в том же журнале.

    Автор и участник, которого действие коснулось, — **разные** имена, и показывать нужно оба:
    3.1 требует от журнала «кто, кого, что изменил и когда». Прежде здесь стояло
    `author_name or target_name`, то есть из двух имён выводилось ровно одно, и, поскольку
    автор есть у каждой такой записи, второе не показывалось никогда. Строка «Участник
    отключён» называла администратора — и читалась так, будто отключили его (Q-D43).
    """
    return JournalRow(
        occurred_at=record.occurred_at,
        label=str(AuditAction(record.action).label),
        tone="",
        domain_id=None,
        domain_name="",
        group_name="",
        addresses=(),
        notification_mode="",
        channel="",
        delivery="",
        author_name=record.author_name,
        target_name=record.target_name,
    )
