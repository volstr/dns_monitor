"""Очередь исходящих — ТЗ 8.3, 4.5, Q-D24.

Повторы считает таблица, а не брокер: задержка — арифметика над `next_attempt_at`, конечность
попыток — условие запроса.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import Domain
from apps.monitoring.models import Event, EventType
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.services.outbox import (
    BACKOFF_BASE,
    MAX_ATTEMPTS,
    Delivered,
    GaveUp,
    Retrying,
    due,
    enqueue,
    mark_delivered,
    mark_failed,
)

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
CHAT = "-1001234567890"


def put(*, at: datetime = MOMENT, text: str = "Адрес вне разрешённых") -> Notification:
    return enqueue(channel=ChannelKind.TELEGRAM, recipient=CHAT, text=text, at=at)


def test_8_3_enqueued_message_waits_for_delivery() -> None:
    notification = put()

    assert notification.status == DeliveryStatus.PENDING
    assert notification.attempt == 0
    # Отправлять можно немедленно: задержка появляется только после неудачи.
    assert notification.next_attempt_at == MOMENT


def test_8_3_due_returns_what_is_ready_to_send() -> None:
    ready = put()
    later = put(at=MOMENT + timedelta(minutes=5))

    assert [item.pk for item in due(at=MOMENT)] == [ready.pk]
    assert {item.pk for item in due(at=MOMENT + timedelta(minutes=5))} == {ready.pk, later.pk}


def test_8_3_delivered_message_leaves_the_queue() -> None:
    notification = put()

    result = mark_delivered(notification_id=notification.pk, at=MOMENT, channel_message_id="42")

    assert result == Delivered(notification_id=notification.pk, channel_message_id="42")
    notification.refresh_from_db()
    assert notification.status == DeliveryStatus.SENT
    assert notification.sent_at == MOMENT
    # Идентификатор нужен, чтобы отвечать в ветку при закрытии инцидента (7.3).
    assert notification.channel_message_id == "42"
    assert due(at=MOMENT) == ()


def test_8_3_failure_schedules_an_exponential_retry() -> None:
    notification = put()

    result = mark_failed(notification_id=notification.pk, at=MOMENT, error="429 Too Many Requests")

    assert isinstance(result, Retrying)
    assert result.attempt == 1
    assert result.next_attempt_at == MOMENT + BACKOFF_BASE
    notification.refresh_from_db()
    assert notification.last_error == "429 Too Many Requests"
    # До назначенного момента строка из очереди не берётся.
    assert due(at=MOMENT) == ()


def test_8_3_delay_doubles_with_every_attempt() -> None:
    notification = put()

    delays = []
    at = MOMENT
    for _ in range(MAX_ATTEMPTS - 1):
        result = mark_failed(notification_id=notification.pk, at=at, error="таймаут")
        assert isinstance(result, Retrying)
        delays.append(result.next_attempt_at - at)
        at = result.next_attempt_at

    assert delays == [BACKOFF_BASE * 2**power for power in range(MAX_ATTEMPTS - 1)]


def test_8_3_attempts_are_finite() -> None:
    notification = put()
    at = MOMENT
    for _ in range(MAX_ATTEMPTS - 1):
        result = mark_failed(notification_id=notification.pk, at=at, error="таймаут")
        assert isinstance(result, Retrying)
        at = result.next_attempt_at

    final = mark_failed(notification_id=notification.pk, at=at, error="канал недоступен")

    assert isinstance(final, GaveUp)
    assert final.attempts == MAX_ATTEMPTS
    notification.refresh_from_db()
    assert notification.status == DeliveryStatus.FAILED
    # Исчерпанная строка больше не берётся — конечность попыток это условие запроса.
    assert due(at=at + timedelta(days=1)) == ()


def test_long_error_does_not_break_the_column() -> None:
    notification = put()

    mark_failed(notification_id=notification.pk, at=MOMENT, error="я" * 900)

    notification.refresh_from_db()
    assert len(notification.last_error) == 500


def test_d7_reply_keeps_the_thread() -> None:
    opening = put()
    mark_delivered(notification_id=opening.pk, at=MOMENT, channel_message_id="42")

    closing = enqueue(
        channel=ChannelKind.TELEGRAM,
        recipient=CHAT,
        text="Снова в норме",
        at=MOMENT + timedelta(minutes=26),
        reply_to_id=opening.pk,
    )

    assert closing.reply_to_id == opening.pk


def test_4_5_event_points_at_the_notification_it_produced() -> None:
    # Связь ведёт событие → уведомление: при слиянии (8.6) одно сообщение покрывает много событий.
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    notification = put()
    for _ in range(3):
        Event.objects.create(
            domain=domain,
            event_type=EventType.NOT_ALLOWED,
            occurred_at=MOMENT,
            notification=notification,
        )

    assert notification.events.count() == 3


def test_4_5_event_without_a_notification_is_normal() -> None:
    # Режим «Не уведомлять», базовая проверка (7.4) или событие, не порождающее уведомления.
    domain = Domain.objects.create(name="pay-gw.contoso.ru")

    event = Event.objects.create(
        domain=domain, event_type=EventType.WATCH_STARTED, occurred_at=MOMENT
    )

    assert event.notification is None
