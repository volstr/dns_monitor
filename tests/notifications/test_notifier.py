"""Разбор очереди и слияние — ТЗ 8.3, 8.6, критерии 38, 39, решение Q-D9."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, NotificationMode
from apps.monitoring.models import Event, EventType, Incident
from apps.monitoring.services.tick import tick
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.services.dispatch import dispatch_event
from apps.notifications.services.notifier import deliver_due
from apps.notifications.services.outbox import MAX_ATTEMPTS
from core.adapters.fake_channel import FakeChannel
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore
from core.ports.notification_channel import NotSent

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
WINDOW = timedelta(seconds=60)
AFTER_WINDOW = MOMENT + WINDOW
CHAT = "-1001234567890"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


@pytest.fixture(autouse=True)
def configured_channel() -> None:
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id=CHAT, notification_mode=NotificationMode.NOT_ALLOWED
    )


def queue_events(store: FakeStateStore, count: int, *, with_incident: bool = False) -> None:
    for index in range(count):
        domain = Domain.objects.create(name=f"domain-{index}.contoso.ru")
        incident = (
            Incident.objects.create(domain=domain, opened_at=MOMENT) if with_incident else None
        )
        event = Event.objects.create(
            domain=domain,
            event_type=EventType.NOT_ALLOWED,
            occurred_at=MOMENT,
            payload={"addresses": ["185.44.9.201"]},
            incident=incident,
        )
        dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)


def test_8_6_nothing_is_sent_before_the_window_closes(store: FakeStateStore) -> None:
    queue_events(store, 2)

    run = deliver_due(

        at=MOMENT,

        channels={ChannelKind.TELEGRAM.value: FakeChannel()},

        state_store=store,

    )

    assert run.sent == 0
    assert Notification.objects.filter(status=DeliveryStatus.PENDING).count() == 2


def test_8_6_below_the_threshold_messages_go_separately(store: FakeStateStore) -> None:
    queue_events(store, 3)  # порог по умолчанию 5
    channel = FakeChannel()

    run = deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    assert (run.sent, run.merged) == (3, 0)
    assert len(channel.messages) == 3
    assert Notification.objects.filter(status=DeliveryStatus.SENT).count() == 3


def test_39_six_events_in_one_window_become_one_summary(store: FakeStateStore) -> None:
    # Критерий 39: шесть событий одного типа при пороге 5 → одно сводное сообщение.
    queue_events(store, 6)
    channel = FakeChannel()

    run = deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    assert (run.sent, run.merged) == (1, 6)
    assert len(channel.messages) == 1
    text = channel.messages[0].text
    assert "Затронуто доменов: 6" in text
    assert "Открыть журнал" in text
    # Шесть записей журнала при этом остаются раздельными (8.6).
    assert Event.objects.count() == 6
    assert Notification.objects.filter(status=DeliveryStatus.SENT).count() == 6


def test_d9_summary_id_reaches_every_incident_it_covered(store: FakeStateStore) -> None:
    queue_events(store, 6, with_incident=True)
    channel = FakeChannel()

    deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    # Открывающее сообщение инцидента находится по связи «событие ↔ уведомление», а не полем
    # инцидента: у каждого канала своя ветка (Q-D11). Сводка накрывает все шесть инцидентов.
    covered = {
        incident.pk: set(
            Notification.objects.filter(
                events__incident_id=incident.pk, status=DeliveryStatus.SENT
            ).values_list("channel_message_id", flat=True)
        )
        for incident in Incident.objects.all()
    }
    assert list(covered.values()) == [{"1"}] * 6, (
        "иначе о закрытии двенадцати инцидентов никто не узнает"
    )


def test_8_6_summary_lists_up_to_ten_names(store: FakeStateStore) -> None:
    queue_events(store, 12)
    channel = FakeChannel()

    deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    text = channel.messages[0].text
    assert "Затронуто доменов: 12" in text
    assert "и ещё 2" in text


def test_38_baseline_violation_is_delivered_inside_a_summary(store: FakeStateStore) -> None:
    """
    Критерий 38 через настоящую проверку, а не через собранные руками события.

    7.4: базовая проверка молчит об изменениях адресов, но вхождение в белый список — критерий
    абсолютный, и он не глушится. При массовом импорте такие сообщения обязаны уйти **сводкой**.
    """
    resolver = FakeResolver({})
    for index in range(6):
        name = f"domain-{index}.contoso.ru"
        domain = Domain.objects.create(name=name, next_check_at=MOMENT)
        AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
        resolver.set(name, [answered("185.44.9.201")])

    # limit задаём явно: по умолчанию тик берёт concurrency × 4 домена, то есть четыре из шести.
    tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1, limit=6)

    # Каждая проверка — базовая: инцидент открыт и статус виден, хотя событий изменения нет.
    assert Incident.objects.filter(closed_at__isnull=True).count() == 6
    assert Event.objects.filter(event_type=EventType.ADDRESS_APPEARED).count() == 0

    channel = FakeChannel()
    run = deliver_due(
        at=AFTER_WINDOW,
        channels={ChannelKind.TELEGRAM.value: channel},
        state_store=store,
    )

    # Одно сводное сообщение вместо шести отдельных.
    assert (run.sent, run.merged) == (1, 6)
    assert len(channel.messages) == 1
    assert "Затронуто доменов: 6" in channel.messages[0].text


def test_8_3_retryable_failure_stays_in_the_queue(store: FakeStateStore) -> None:
    queue_events(store, 1)
    channel = FakeChannel(results=[NotSent(error="429 Too Many Requests", retryable=True)])

    run = deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    assert (run.failed, run.gave_up) == (1, 0)
    notification = Notification.objects.get()
    assert notification.status == DeliveryStatus.PENDING
    assert notification.attempt == 1
    assert notification.next_attempt_at > AFTER_WINDOW


def test_8_3_permanent_failure_gives_up_at_once(store: FakeStateStore) -> None:
    queue_events(store, 1)
    channel = FakeChannel(results=[NotSent(error="chat not found", retryable=False)])

    run = deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: channel},

        state_store=store,

    )

    assert (run.failed, run.gave_up) == (0, 1)
    notification = Notification.objects.get()
    assert notification.status == DeliveryStatus.FAILED
    # Пять попыток «чат не найден» только оттянули бы запись в журнал.
    assert notification.attempt < MAX_ATTEMPTS


def test_8_3_delivery_result_is_recorded_next_to_the_event(store: FakeStateStore) -> None:
    queue_events(store, 1)

    deliver_due(

        at=AFTER_WINDOW,

        channels={ChannelKind.TELEGRAM.value: FakeChannel()},

        state_store=store,

    )

    notification = Event.objects.get().notifications.get()
    assert notification.status == DeliveryStatus.SENT
    assert notification.channel_message_id == "1"


def test_empty_queue_does_nothing(store: FakeStateStore) -> None:
    run = deliver_due(
        at=AFTER_WINDOW,
        channels={ChannelKind.TELEGRAM.value: FakeChannel()},
        state_store=store,
    )

    assert (run.sent, run.merged, run.failed, run.gave_up) == (0, 0, 0, 0)
