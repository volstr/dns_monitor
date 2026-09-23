"""Очередь исходящих: постановка, выборка и учёт попыток — ТЗ 8.3.

Экспоненциальная задержка — арифметика над `next_attempt_at`, конечное число попыток — условие
в запросе. Никакого брокера: по Q-D24 очередь остаётся строкой в PostgreSQL, потому что 8.3
требует видеть результат доставки рядом с событием, а 11.11 запрещает делать источником истины
то, потеря чего допустима.

Отказ доставки — штатный исход и возвращается результатом, а не исключением (`CLAUDE.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.notifications.models import (
    ERROR_MAX_LENGTH,
    ChannelKind,
    DeliveryStatus,
    Notification,
)

#: Конечное число попыток (8.3). Константа, а не настройка: в 4.1 её нет.
MAX_ATTEMPTS = 5
#: Основание экспоненциальной задержки: 1, 2, 4, 8, 16 минут. Пятая неудача отстоит от первой
#: примерно на полчаса — дольше держать смысла нет, канал либо ожил, либо лежит всерьёз.
BACKOFF_BASE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Delivered:
    notification_id: int
    channel_message_id: str


@dataclass(frozen=True, slots=True)
class Retrying:
    notification_id: int
    attempt: int
    next_attempt_at: datetime


@dataclass(frozen=True, slots=True)
class GaveUp:
    """Повторы исчерпаны. TODO(ТЗ 11.14): отправка в Sentry — этап 8."""

    notification_id: int
    attempts: int
    last_error: str


def enqueue(
    *,
    channel: ChannelKind,
    recipient: str,
    text: str,
    at: datetime,
    reply_to_id: int | None = None,
) -> Notification:
    """Поставить сообщение в очередь. Текст складывается здесь, а не при отправке.

    Так журнал показывает именно то, что ушло, а повтор отправляет то же самое, даже если
    настройки успели измениться.
    """
    return Notification.objects.create(
        channel=channel,
        recipient=recipient,
        text=text,
        created_at=at,
        next_attempt_at=at,
        reply_to_id=reply_to_id,
    )


def due(*, at: datetime, limit: int = 100) -> tuple[Notification, ...]:
    """Что пора отправлять. Конечность попыток — условие запроса, а не проверка в цикле."""
    return tuple(
        Notification.objects.filter(
            status=DeliveryStatus.PENDING,
            next_attempt_at__lte=at,
            attempt__lt=MAX_ATTEMPTS,
        ).order_by("next_attempt_at")[:limit]
    )


def mark_delivered(*, notification_id: int, at: datetime, channel_message_id: str) -> Delivered:
    """Успех. Идентификатор сообщения нужен, чтобы отвечать в ветку (7.3, 8.5)."""
    Notification.objects.filter(pk=notification_id).update(
        status=DeliveryStatus.SENT,
        sent_at=at,
        channel_message_id=channel_message_id,
        last_error="",
    )
    return Delivered(notification_id=notification_id, channel_message_id=channel_message_id)


def mark_failed(
    *, notification_id: int, at: datetime, error: str, retryable: bool = True
) -> Retrying | GaveUp:
    """Неудача: либо назначаем следующую попытку, либо сдаёмся.

    ``retryable=False`` — отказ, который не изменится от повтора: «чат не найден», «бот
    заблокирован». Пять попыток такого отказа только оттянули бы запись в журнал.
    """
    notification = Notification.objects.get(pk=notification_id)
    attempt = notification.attempt + 1

    if not retryable or attempt >= MAX_ATTEMPTS:
        Notification.objects.filter(pk=notification_id).update(
            status=DeliveryStatus.FAILED, attempt=attempt, last_error=error[:ERROR_MAX_LENGTH]
        )
        return GaveUp(notification_id=notification_id, attempts=attempt, last_error=error)

    next_attempt_at = at + BACKOFF_BASE * (2 ** (attempt - 1))
    Notification.objects.filter(pk=notification_id).update(
        attempt=attempt, next_attempt_at=next_attempt_at, last_error=error[:ERROR_MAX_LENGTH]
    )
    return Retrying(
        notification_id=notification_id, attempt=attempt, next_attempt_at=next_attempt_at
    )
