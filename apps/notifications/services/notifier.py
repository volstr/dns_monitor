"""Разбор очереди исходящих — ТЗ 8.3, 8.6, Q-D9.

Отдельный процесс от проверок: недоступность канала не должна занимать потоки резолвера
(plan.md, этап 5). Здесь только доставка — решение об отправке и текст уже приняты в `dispatch`.

**Схлопывание окна.** Сообщения одного окна ждали в очереди до момента его закрытия. Когда он
настал, смотрим накопитель: если событий набралось больше порога слияния, всё окно уходит
**одним** сводным сообщением (критерий 39), а идентификатор этого сообщения проставляется каждому
инциденту, который в сводку вошёл, — иначе о закрытии двенадцати инцидентов никто не узнает
(Q-D9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.monitoring.models import Event, Incident
from apps.notifications.models import Notification
from apps.notifications.services.message import compose_summary
from apps.notifications.services.outbox import GaveUp, due, mark_delivered, mark_failed
from core.ports.notification_channel import NotificationChannel, Sent
from core.ports.state_store import StateKey, StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryRun:
    sent: int = 0
    #: Сколько строк очереди ушло в составе сводок вместо отдельных сообщений.
    merged: int = 0
    failed: int = 0
    gave_up: int = 0


def deliver_due(
    *,
    at: datetime,
    channel: NotificationChannel,
    state_store: StateStore,
    limit: int = 100,
) -> DeliveryRun:
    pending = due(at=at, limit=limit)
    if not pending:
        return DeliveryRun()

    global_settings = GlobalSettings.objects.get()
    window = timedelta(seconds=global_settings.merge_window_seconds)
    threshold = global_settings.merge_threshold

    groups: dict[str, list[Notification]] = {}
    for notification in pending:
        groups.setdefault(notification.merge_key, []).append(notification)

    run = DeliveryRun()
    for merge_key, batch in groups.items():
        if merge_key and len(batch) > threshold:
            run = _send_summary(batch, merge_key, window, at, channel, state_store, run)
            continue
        for notification in batch:
            run = _send_one(notification, at, channel, run)
        if merge_key:
            # Окно закрыто: накопитель больше не нужен, даже если сводки не было.
            state_store.release(StateKey(merge_key, window))
    return run


def _send_one(
    notification: Notification, at: datetime, channel: NotificationChannel, run: DeliveryRun
) -> DeliveryRun:
    result = channel.send(
        recipient=notification.recipient,
        text=notification.text,
        reply_to=_reply_to(notification),
    )
    if isinstance(result, Sent):
        mark_delivered(
            notification_id=notification.pk, at=at, channel_message_id=result.channel_message_id
        )
        _remember_for_incidents(notification, result.channel_message_id)
        return _plus(run, sent=1)

    outcome = mark_failed(
        notification_id=notification.pk, at=at, error=result.error, retryable=result.retryable
    )
    if isinstance(outcome, GaveUp):
        # TODO(ТЗ 11.14): исчерпанные повторы уходят в Sentry — этап 8.
        logger.error(
            "уведомление не доставлено, повторы исчерпаны",
            extra={"notification": notification.pk, "error": result.error},
        )
        return _plus(run, gave_up=1)
    return _plus(run, failed=1)


def _send_summary(
    batch: list[Notification],
    merge_key: str,
    window: timedelta,
    at: datetime,
    channel: NotificationChannel,
    state_store: StateStore,
    run: DeliveryRun,
) -> DeliveryRun:
    key = StateKey(merge_key, window)
    # Накопитель — источник имён; если он успел истечь, берём их из самих событий.
    names = state_store.collected(key) or _domain_names(batch)
    event_type = merge_key.rsplit(":", 1)[-1]

    result = channel.send(
        recipient=batch[0].recipient,
        text=compose_summary(
            event_type=event_type,
            domain_names=names,
            journal_url=f"{settings.ENV.app_base_url}/events/?type={event_type}",
        ),
    )
    state_store.release(key)

    if not isinstance(result, Sent):
        for notification in batch:
            mark_failed(
                notification_id=notification.pk,
                at=at,
                error=result.error,
                retryable=result.retryable,
            )
        return _plus(run, failed=len(batch))

    for notification in batch:
        mark_delivered(
            notification_id=notification.pk, at=at, channel_message_id=result.channel_message_id
        )
        _remember_for_incidents(notification, result.channel_message_id)
    return _plus(run, sent=1, merged=len(batch))


def _domain_names(batch: list[Notification]) -> tuple[str, ...]:
    names = Event.objects.filter(notification__in=batch).values_list("domain__name", flat=True)
    return tuple(sorted(set(names)))


def _remember_for_incidents(notification: Notification, channel_message_id: str) -> None:
    """Открывающее сообщение инцидента — то, по которому потом отвечают (7.3, 8.5, Q-D7).

    Записывается только в пустое поле: у инцидента ветка одна, и повторная доставка её не меняет.
    """
    incident_ids = list(
        Event.objects.filter(notification=notification, incident__isnull=False).values_list(
            "incident_id", flat=True
        )
    )
    Incident.objects.filter(pk__in=incident_ids, channel_message_id="").update(
        channel_message_id=channel_message_id
    )


def _reply_to(notification: Notification) -> str | None:
    if notification.reply_to is None:
        return None
    return notification.reply_to.channel_message_id or None


def _plus(run: DeliveryRun, **delta: int) -> DeliveryRun:
    return DeliveryRun(
        sent=run.sent + delta.get("sent", 0),
        merged=run.merged + delta.get("merged", 0),
        failed=run.failed + delta.get("failed", 0),
        gave_up=run.gave_up + delta.get("gave_up", 0),
    )
