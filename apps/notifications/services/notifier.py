"""
Разбор очереди исходящих — ТЗ 8.3, 8.6, Q-D9.

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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.monitoring.models import Event
from apps.notifications.models import Notification
from apps.notifications.services.message import compose_summary
from apps.notifications.services.outbox import GaveUp, due, mark_delivered, mark_failed
from core.observability.report import Failure, report
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
    channels: Mapping[str, NotificationChannel],
    state_store: StateStore,
    limit: int = 100,
) -> DeliveryRun:
    """
    Разобрать наступившие строки очереди — по одной реализации на вид канала (Q-D11).

    Раньше сюда приходил **один** канал, и вид строки не спрашивался: получатель был один.
    Теперь строка сама говорит, чем её отправлять, а отсутствие реализации для её вида — это
    ошибка маршрутизации, а не молчаливый пропуск: строка появилась, значит кто-то счёл канал
    готовым (`ready_channels`), и расхождение нужно видеть, а не заметать.

    Группировка по окну слияния при этом не меняется: ключ окна по 8.6 уже содержит вид канала,
    поэтому сводки собираются по каждому каналу отдельно сами собой.
    """
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
        channel = channels.get(batch[0].channel)
        if channel is None:
            run = _no_implementation(batch, at, run)
            continue
        if merge_key and len(batch) > threshold:
            run = _send_summary(batch, merge_key, window, at, channel, state_store, run)
            continue
        for notification in batch:
            run = _send_one(notification, at, channel, run)
        if merge_key:
            # Окно закрыто: накопитель больше не нужен, даже если сводки не было.
            state_store.release(StateKey(merge_key, window))
    return run


def _no_implementation(
    batch: list[Notification], at: datetime, run: DeliveryRun
) -> DeliveryRun:
    """
    Строка есть, а отправлять нечем. Повторять бессмысленно — реализация от повтора не появится.

    Случиться это может ровно одним способом: строки поставил один состав каналов, а разбирает
    очередь другой — например, `run_notifier` старой версии. Тихо оставить строку в очереди
    нельзя: она будет браться каждый круг доставки и никогда не уйдёт.
    """
    kind = batch[0].channel
    for notification in batch:
        mark_failed(
            notification_id=notification.pk,
            at=at,
            error=f'нет реализации канала «{kind}»',
            retryable=False,
        )
    logger.error('нет реализации канала', extra={'component': 'notifier', 'channel': kind})
    return _plus(run, gave_up=len(batch))


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
        return _plus(run, sent=1)

    outcome = mark_failed(
        notification_id=notification.pk, at=at, error=result.error, retryable=result.retryable
    )
    if isinstance(outcome, GaveUp):
        logger.error(
            'уведомление не доставлено, повторы исчерпаны',
            extra={'notification': notification.pk, 'error': result.error},
        )
        # 11.14: неудача доставки **после исчерпания всех повторов** — ошибка приложения.
        # Отдельная неудачная попытка ею не является: ради этого и существуют повторы.
        report(
            Failure(
                cause='delivery:gave-up',
                message=f'уведомление не доставлено, повторы исчерпаны: {result.error}',
                tags={'notification': str(notification.pk)},
            )
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
    event_type = merge_key.rsplit(':', 1)[-1]

    result = channel.send(
        recipient=batch[0].recipient,
        text=compose_summary(
            event_type=event_type,
            domain_names=names,
            journal_url=f'{settings.ENV.app_base_url}/events/?type={event_type}',
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
    return _plus(run, sent=1, merged=len(batch))


def _domain_names(batch: list[Notification]) -> tuple[str, ...]:
    names = Event.objects.filter(notifications__in=batch).values_list('domain__name', flat=True)
    return tuple(sorted(set(names)))


def _reply_to(notification: Notification) -> str | None:
    if notification.reply_to is None:
        return None
    return notification.reply_to.channel_message_id or None


def _plus(run: DeliveryRun, **delta: int) -> DeliveryRun:
    return DeliveryRun(
        sent=run.sent + delta.get('sent', 0),
        merged=run.merged + delta.get('merged', 0),
        failed=run.failed + delta.get('failed', 0),
        gave_up=run.gave_up + delta.get('gave_up', 0),
    )
