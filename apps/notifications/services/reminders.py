"""
Повторные напоминания по открытому инциденту — ТЗ 8.5, критерии 40-43.

«Одно сообщение в три часа ночи в шумном чате теряется, и заявленная ценность „узнать раньше
всех" исчезает» — поэтому по открытому инциденту уведомление повторяется.

Правила взяты из 8.5 дословно:

- интервал задаётся в группе или берётся из общих настроек, значение «не напоминать» выключает;
- напоминание уходит **ответом в ту же ветку**, что и сообщение об открытии;
- отсчёт ведётся от последнего отправленного сообщения по инциденту, а не от его открытия;
- сообщение о новом несовпавшем адресе засчитывается за напоминание — два подряд не уходят;
- напоминания прекращаются при закрытии инцидента.

Плюс два решения: по Q-D7 напоминаний нет, если открывающее сообщение не уходило (отвечать не
на что), по Q-D14 напоминания приостанавливаются у выключенного домена.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.catalog.selectors.settings import effective_settings
from apps.monitoring.models import Incident
from apps.monitoring.selectors.addresses import address_statuses
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.selectors.channels import ready_channels
from apps.notifications.services.message import compose_reminder
from apps.notifications.services.outbox import enqueue
from core.ports.state_store import StateStore, merge_window
from core.text import humanize_duration
from core.types.checking import AddressStatus

#: Тип «события» для окна слияния: напоминания сливаются так же, как события (Q-D9).
REMINDER_KIND = 'reminder'


@dataclass(frozen=True, slots=True)
class RemindersQueued:
    queued: int


def queue_due_reminders(*, at: datetime, state_store: StateStore) -> RemindersQueued:
    """
    Поставить напоминания по всем инцидентам, которым пора.

    Напоминание уходит в те же каналы, что и открывающее сообщение, — по строке на канал
    (Q-D11). Не «во все настроенные»: по Q-D7 продолжение существует только там, где было
    открывающее сообщение. Канал, подключённый посреди инцидента, получит его следующее
    событие, но не напоминание о том, чего он не открывал.
    """
    global_settings = GlobalSettings.objects.get()
    ready = ready_channels(global_settings)
    if not ready:
        return RemindersQueued(queued=0)

    window = timedelta(seconds=global_settings.merge_window_seconds)
    queued = 0

    for incident in _open_incidents():
        interval = effective_settings(incident.domain_id).reminder_interval.value
        if interval is None:
            # «Не напоминать» — критерий 42.
            continue
        since = incident.last_message_at or incident.opened_at
        if at - since < interval:
            continue

        openings = _openings(incident, ready)
        if not openings:
            # Q-D7: открывающее сообщение никуда не дошло — отвечать не на что.
            continue
        for kind, opening in openings.items():
            _queue_one(incident, at, kind, ready[kind], opening, window, state_store)
        queued += 1

    return RemindersQueued(queued=queued)


def _open_incidents() -> list[Incident]:
    return list(
        Incident.objects.select_related('domain').filter(
            closed_at__isnull=True,
            # Q-D14: у выключенного домена напоминания приостанавливаются.
            domain__watch_enabled=True,
        )
    )


def _openings(
    incident: Incident, ready: dict[ChannelKind, str]
) -> dict[ChannelKind, Notification]:
    """
    Открывающее сообщение инцидента в каждом канале, где оно было доставлено.

    Раньше хранилось строкой в `Incident.channel_message_id` — одно на инцидент. С несколькими
    каналами это перестало работать: идентификатор сообщения выдаёт сам канал, и ответить в
    Telegram на `Message-ID` письма нельзя.
    """
    openings: dict[ChannelKind, Notification] = {}
    sent = (
        Notification.objects.filter(
            events__incident_id=incident.pk, status=DeliveryStatus.SENT
        )
        .exclude(channel_message_id='')
        .order_by('created_at')
    )
    for notification in sent:
        kind = ChannelKind(notification.channel)
        if kind in ready and kind not in openings:
            openings[kind] = notification
    return openings


def _queue_one(
    incident: Incident,
    at: datetime,
    kind: ChannelKind,
    recipient: str,
    opening: Notification,
    window: timedelta,
    state_store: StateStore,
) -> None:
    threshold = effective_settings(incident.domain_id).freshness_threshold.value
    unmatched = tuple(
        sorted(
            str(view.address)
            for view in address_statuses(incident.domain_id, at, threshold)
            if view.status is AddressStatus.UNMATCHED
        )
    )
    key = merge_window(kind.value, REMINDER_KIND)
    state_store.accumulate(key, incident.domain.name)

    enqueue(
        channel=kind,
        recipient=recipient,
        text=compose_reminder(
            domain_name=incident.domain.name,
            age=_age(at - incident.opened_at),
            unmatched=unmatched,
            domain_url=f'{settings.ENV.app_base_url}/domains/{incident.domain_id}/',
        ),
        at=at,
        reply_to_id=opening.pk,
    )

    # Отсчёт следующего напоминания ведём от постановки, а не от доставки: иначе за время окна
    # слияния успели бы встать два напоминания подряд. Цена — при неудачной доставке интервал
    # отсчитывается от постановки.
    Incident.objects.filter(pk=incident.pk).update(last_message_at=at)


def _age(age: timedelta) -> str:
    """Возраст инцидента в читаемом виде — как в блоке 11.9 «держится 3 ч 12 мин»."""
    return humanize_duration(age)
