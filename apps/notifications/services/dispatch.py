"""
Событие → уведомление — ТЗ 8.1, 8.4, 8.6.

Здесь сходятся три уже готовых куска: решение об отправке (`policy`), составление текста
(`message`) и очередь исходящих (`outbox`). Сам резолв и события остаются в `apps.monitoring` —
доставка о них ничего не решает.

**Окно слияния.** Сообщение ставится в очередь не «на сейчас», а на момент закрытия окна:
`next_attempt_at = at + merge_window_seconds`. Пока окно открыто, в Redis копятся имена доменов —
по ним потом видно, набралось ли сверх порога и что писать в сводке (8.6). Сообщения при этом
ждут **в PostgreSQL**: ключ накопителя истёк бы раньше, чем окно закроется, и сводку стало бы не
из чего собирать, а по 11.11 Redis не источник истины (Q-D9).

Собирает сводку и отправляет — `run_notifier`. Здесь только постановка в очередь.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.catalog.selectors.settings import effective_settings
from apps.monitoring.models import Event, EventType, Incident
from apps.monitoring.selectors.addresses import fresh_set
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.selectors.channels import ready_channels
from apps.notifications.services.message import MessageInput, compose
from apps.notifications.services.policy import Send, SkipReason, decide
from core.ports.state_store import StateStore, merge_window
from core.text import humanize_duration


@dataclass(frozen=True, slots=True)
class QueuedTo:
    """Одна строка очереди — один канал. Судьба доставки у каждого своя (Q-D11)."""

    channel: ChannelKind
    notification_id: int
    #: Сколько событий этого типа уже накопилось в окне **этого канала**: ключ окна слияния по
    #: 8.6 строится «канал:тип события», и порог считается по каждому каналу отдельно.
    in_window: int


@dataclass(frozen=True, slots=True)
class Queued:
    #: По строке на каждый готовый канал, в порядке `ChannelKind`. Пустым не бывает: когда
    #: ставить некуда, возвращается `NotQueued`.
    to: tuple[QueuedTo, ...]
    #: Момент, когда окно закроется и сообщения можно будет отправить. Общий: окно у каналов
    #: одно по длительности и отсчитывается от одного события.
    send_after: datetime


@dataclass(frozen=True, slots=True)
class NotQueued:
    reason: SkipReason


type DispatchResult = Queued | NotQueued


def dispatch_event(
    *, event_id: int, at: datetime, state_store: StateStore
) -> DispatchResult:
    """Поставить уведомление по событию в очередь, если оно вообще уведомляет."""
    event = Event.objects.select_related('domain', 'domain__group', 'incident').get(pk=event_id)
    resolved = effective_settings(event.domain_id)
    global_settings = GlobalSettings.objects.get()

    # Требование 11.7: выгрузка журнала содержит режим уведомлений **на момент события**.
    # Восстановить его потом нельзя — настройку могли поменять, — поэтому он записывается здесь,
    # где уже разрешён по цепочке наследования, и до любых ранних выходов ниже.
    _remember_mode(event, resolved.notification_mode.value)

    ready = ready_channels(global_settings)
    if not ready:
        # Ни один канал не настроен — нормальный режим (8.3), а не ошибка.
        return NotQueued(reason=SkipReason.NEVER)

    # Решение «уведомлять ли вообще» — одно на событие, а не на канал: 8.1 говорит о режиме
    # уведомлений, а не о способе доставки. Ветка переписки (Q-D7) при этом своя у каждого
    # канала, поэтому `opening_message_id` спрашивается внутри цикла.
    text = _text(event, resolved.freshness_threshold.value, at, global_settings)
    window = timedelta(seconds=global_settings.merge_window_seconds)
    send_after = at + window

    queued: list[QueuedTo] = []
    skipped: SkipReason | None = None
    for kind, recipient in ready.items():
        opening = _opening_notification(event, kind)
        decision = decide(
            event_type=event.event_type,
            mode=resolved.notification_mode.value,
            notify_back_to_normal=resolved.notify_back_to_normal.value,
            opening_message_id=opening.channel_message_id if opening else None,
        )
        if not isinstance(decision, Send):
            skipped = decision.reason
            continue

        key = merge_window(kind.value, event.event_type)
        in_window = state_store.accumulate(key, event.domain.name)
        notification = Notification.objects.create(
            channel=kind,
            recipient=recipient,
            text=text,
            created_at=at,
            # Ждём закрытия окна: критерий 39 требует одного сводного сообщения на шесть событий.
            next_attempt_at=send_after,
            status=DeliveryStatus.PENDING,
            reply_to=opening if decision.reply_to else None,
            merge_key=key.name,
        )
        event.notifications.add(notification)
        queued.append(
            QueuedTo(channel=kind, notification_id=notification.pk, in_window=in_window)
        )

    if not queued:
        return NotQueued(reason=skipped or SkipReason.NEVER)

    if event.incident_id:
        # 8.5: сообщение о новом несовпавшем адресе засчитывается за напоминание — два подряд
        # не отправляются. Поэтому отсчёт интервала сдвигает любое сообщение по инциденту.
        Incident.objects.filter(pk=event.incident_id).update(last_message_at=at)

    return Queued(to=tuple(queued), send_after=send_after)


def _opening_notification(event: Event, kind: ChannelKind) -> Notification | None:
    """
    Открывающее сообщение, в ветку которого отвечаем, — правило Q-D7, **в этом канале**.

    Канал здесь обязателен. Идентификатор сообщения выдаёт сам канал: у Telegram это число,
    у почты — `Message-ID`. Ответить в Telegram на `Message-ID` письма нельзя, и наоборот;
    пока канал был один, вопроса не возникало.

    Раньше открывающее сообщение инцидента хранилось строкой в `Incident.channel_message_id` —
    одно на инцидент. Теперь оно ищется по связи: у инцидента столько веток, сколько каналов.
    """
    sent = Notification.objects.filter(channel=kind, status=DeliveryStatus.SENT).exclude(
        channel_message_id=''
    )
    if event.event_type == EventType.BACK_TO_NORMAL.value:
        if event.incident_id is None:
            return None
        return sent.filter(events__incident_id=event.incident_id).order_by('created_at').first()
    if event.event_type == EventType.RESOLVE_RECOVERED.value:
        return (
            sent.filter(
                events__domain_id=event.domain_id,
                events__event_type=EventType.RESOLVE_FAILED.value,
            )
            .order_by('-created_at')
            .first()
        )
    return None


def _remember_mode(event: Event, mode: str) -> None:
    """Дописать режим в полезную нагрузку события, не трогая остальное."""
    payload = dict(event.payload or {})
    if payload.get('notification_mode') == mode:
        return
    payload['notification_mode'] = mode
    event.payload = payload
    Event.objects.filter(pk=event.pk).update(payload=payload)


def _text(
    event: Event, threshold: timedelta, at: datetime, global_settings: GlobalSettings
) -> str:
    payload = event.payload or {}
    addresses = tuple(str(address) for address in payload.get('addresses', ()))
    current = tuple(sorted(str(address) for address in fresh_set(event.domain_id, at, threshold)))
    duration = _duration(event)

    return compose(
        MessageInput(
            event_type=event.event_type,
            domain_name=event.domain.name,
            domain_url=f'{settings.ENV.app_base_url}/domains/{event.domain_id}/',
            changed=addresses,
            current=current,
            group_name=event.domain.group.name if event.domain.group else None,
            domain_comment=event.domain.comment,
            group_comment=event.domain.group.comment if event.domain.group else '',
            duration=duration,
        ),
        include_current=global_settings.message_include_full_set,
        include_comment=global_settings.message_include_comment,
        include_link=global_settings.message_include_link,
    )


def _duration(event: Event) -> str | None:
    """Длительность инцидента для «Снова в норме» — критерий 17."""
    if event.event_type != EventType.BACK_TO_NORMAL.value or event.incident is None:
        return None
    return humanize_duration(event.occurred_at - event.incident.opened_at)


def pending_incident(event: Event) -> Incident | None:
    """Инцидент события, если он есть. Нужен `run_notifier` для записи id сводки (Q-D9)."""
    return event.incident
