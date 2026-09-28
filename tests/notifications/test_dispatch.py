"""Событие → очередь — ТЗ 8.1, 8.4, 8.6, критерии 14, 15, 17."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from apps.monitoring.models import Event, EventType, Incident
from apps.notifications.models import ChannelKind, Notification
from apps.notifications.services.dispatch import NotQueued, Queued, dispatch_event
from apps.notifications.services.policy import SkipReason
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
CHAT = "-1001234567890"


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


@pytest.fixture
def domain() -> Domain:
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id=CHAT, notification_mode=NotificationMode.NOT_ALLOWED
    )
    group = Group.objects.create(name="Платёжный контур", comment="Дежурит команда платежей")
    return Domain.objects.create(name="pay-gw.contoso.ru", group=group, comment="Платёжный шлюз")


def make_event(
    domain: Domain,
    event_type: EventType = EventType.NOT_ALLOWED,
    *,
    incident: Incident | None = None,
    payload: dict[str, object] | None = None,
) -> Event:
    return Event.objects.create(
        domain=domain,
        event_type=event_type,
        occurred_at=MOMENT,
        payload=payload or {"addresses": ["185.44.9.201"]},
        incident=incident,
    )


def test_8_6_message_waits_for_the_window_to_close(domain: Domain, store: FakeStateStore) -> None:
    event = make_event(domain)

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert isinstance(result, Queued)
    # Окно по умолчанию 60 секунд (4.1): раньше сообщение не уйдёт — критерий 39.
    assert result.send_after == MOMENT + timedelta(seconds=60)
    # Настроен один канал, значит и строка одна (Q-D11).
    assert [item.channel for item in result.to] == [ChannelKind.TELEGRAM]
    assert result.to[0].in_window == 1


def test_8_6_window_counts_events_of_the_same_type(domain: Domain, store: FakeStateStore) -> None:
    other = Domain.objects.create(name="api.contoso.ru", group=domain.group)

    first = dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)
    second = dispatch_event(event_id=make_event(other).pk, at=MOMENT, state_store=store)

    assert isinstance(first, Queued)
    assert isinstance(second, Queued)
    assert (first.to[0].in_window, second.to[0].in_window) == (1, 2)


def test_4_5_event_points_at_its_notification(domain: Domain, store: FakeStateStore) -> None:
    event = make_event(domain)

    dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert [item.recipient for item in event.notifications.all()] == [CHAT]


def test_14_mode_none_queues_nothing(domain: Domain, store: FakeStateStore) -> None:
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NONE)
    event = make_event(domain)

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert result == NotQueued(reason=SkipReason.MODE)
    assert Notification.objects.count() == 0
    assert event.notifications.count() == 0


def test_15_address_change_mode_ignores_not_allowed(
    domain: Domain, store: FakeStateStore
) -> None:
    GlobalSettings.objects.filter(pk=1).update(
        notification_mode=NotificationMode.ADDRESS_CHANGE
    )

    result = dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)

    assert result == NotQueued(reason=SkipReason.MODE)


def test_8_3_unconfigured_channel_queues_nothing(domain: Domain, store: FakeStateStore) -> None:
    # Пустой адресат — «Не настроен», нормальный режим, а не ошибка.
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="")

    result = dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)

    assert result == NotQueued(reason=SkipReason.NEVER)


def test_8_4_text_carries_the_domain_and_the_comment(
    domain: Domain, store: FakeStateStore
) -> None:
    dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)

    text = Notification.objects.get().text
    assert "<b>Адрес вне разрешённых</b>" in text
    assert "pay-gw.contoso.ru" in text
    assert "Платёжный шлюз" in text
    assert "Группа: Платёжный контур" in text


def test_17_back_to_normal_carries_the_duration(domain: Domain, store: FakeStateStore) -> None:
    incident = Incident.objects.create(domain=domain, opened_at=MOMENT - timedelta(minutes=26))
    opening = Notification.objects.create(
        channel="telegram",
        recipient=CHAT,
        text="Адрес вне разрешённых",
        created_at=MOMENT - timedelta(minutes=26),
        next_attempt_at=MOMENT - timedelta(minutes=26),
        channel_message_id="42",
        status="sent",
    )
    # Открывающее сообщение теперь находится по связи, а не по полю инцидента (Q-D11).
    opening_event = make_event(domain, incident=incident)
    opening_event.notifications.add(opening)
    event = make_event(domain, EventType.BACK_TO_NORMAL, incident=incident, payload={})

    dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    notification = Notification.objects.exclude(pk=opening.pk).get()
    assert "Проблема держалась 26 мин." in notification.text
    # Ответ уходит в ветку открывающего сообщения (7.3, Q-D7).
    assert notification.reply_to_id == opening.pk


def test_d7_back_to_normal_without_an_opening_message_is_skipped(
    domain: Domain, store: FakeStateStore
) -> None:
    incident = Incident.objects.create(domain=domain, opened_at=MOMENT - timedelta(minutes=5))
    event = make_event(domain, EventType.BACK_TO_NORMAL, incident=incident, payload={})

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert result == NotQueued(reason=SkipReason.NO_OPENING_MESSAGE)


def test_7_4_watch_started_never_queues(domain: Domain, store: FakeStateStore) -> None:
    event = make_event(domain, EventType.WATCH_STARTED, payload={"address_count": 3})

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert result == NotQueued(reason=SkipReason.NEVER)
