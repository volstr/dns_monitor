"""
Маршрутизация по каналам — ТЗ 8.3, 8.6; решение Q-D11, вариант 1.

Событие уходит **во все настроенные каналы**, по строке очереди на каждый. До этого решения
`dispatch` знал только Telegram, а заглушки почты и вебхука реализовывали протокол, но вызвать
их было некому.

Проверяется здесь не «письмо дошло» — за это отвечает `test_smtp_channel.py`, — а именно
развозка: сколько строк появилось, у кого какой адресат, что считается «настроенным» и что
происходит, когда один канал доставил, а другой нет.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, NotificationMode
from apps.monitoring.models import Event, EventType, Incident
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.selectors.channels import ready_channels
from apps.notifications.services.dispatch import NotQueued, Queued, dispatch_event
from apps.notifications.services.notifier import deliver_due
from core.adapters.fake_channel import FakeChannel
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore
from core.ports.notification_channel import NotSent

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
AFTER_WINDOW = MOMENT + timedelta(seconds=60)
CHAT = "-1001234567890"
MAIL = ["duty@contoso.ru", "noc@contoso.ru"]


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


@pytest.fixture
def domain() -> Domain:
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id=CHAT,
        email_recipients=MAIL,
        notification_mode=NotificationMode.NOT_ALLOWED,
    )
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def make_event(domain: Domain, **extra: object) -> Event:
    return Event.objects.create(
        domain=domain,
        event_type=extra.pop("event_type", EventType.NOT_ALLOWED),
        occurred_at=MOMENT,
        payload={"addresses": ["185.44.9.201"]},
        **extra,
    )


def both_channels() -> dict[str, FakeChannel]:
    return {
        ChannelKind.TELEGRAM.value: FakeChannel(),
        ChannelKind.EMAIL.value: FakeChannel(),
    }


# ─────────────── что считается настроенным каналом ───────────────


def test_d11_a_channel_needs_both_a_recipient_and_an_implementation(domain: Domain) -> None:
    """
    Условие — связка, а не одно из двух.

    У заглушки вебхука нет подключения, поэтому её `is_configured` всегда `True`. Если
    развозить «по всем, у кого есть адресат», вебхук на каждое событие дал бы в журнале запись
    «не доставлено» — подводный камень, названный в Q-D11 прямо.
    """
    GlobalSettings.objects.filter(pk=1).update(webhook_url="https://hooks.contoso.ru/dns")

    ready = ready_channels(GlobalSettings.objects.get())

    assert set(ready) == {ChannelKind.TELEGRAM, ChannelKind.EMAIL}


def test_d11_empty_recipient_means_the_channel_is_not_configured(domain: Domain) -> None:
    """Пустое значение — «Не настроен», и это нормальный режим работы (8.3)."""
    GlobalSettings.objects.filter(pk=1).update(email_recipients=[])

    assert set(ready_channels(GlobalSettings.objects.get())) == {ChannelKind.TELEGRAM}


def test_d11_mail_recipients_reach_the_channel_one_per_line(domain: Domain) -> None:
    """Адресат почты — перечень адресов, и канал получает его строкой (8.3)."""
    ready = ready_channels(GlobalSettings.objects.get())

    assert ready[ChannelKind.EMAIL] == "duty@contoso.ru\nnoc@contoso.ru"


# ─────────────── развозка ───────────────


def test_d11_event_is_queued_to_every_configured_channel(
    domain: Domain, store: FakeStateStore
) -> None:
    event = make_event(domain)

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert isinstance(result, Queued)
    assert [item.channel for item in result.to] == [ChannelKind.TELEGRAM, ChannelKind.EMAIL]
    assert event.notifications.count() == 2
    assert set(Notification.objects.values_list("recipient", flat=True)) == {
        CHAT,
        "duty@contoso.ru\nnoc@contoso.ru",
    }


def test_d11_nothing_is_queued_when_no_channel_is_configured(
    domain: Domain, store: FakeStateStore
) -> None:
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="", email_recipients=[])
    event = make_event(domain)

    result = dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    assert isinstance(result, NotQueued)
    assert Notification.objects.count() == 0


def test_8_6_merge_window_is_counted_per_channel(
    domain: Domain, store: FakeStateStore
) -> None:
    """
    8.6: «на один канал доставки». Ключ окна строится «канал:тип события», значит и порог
    слияния считается по каждому каналу отдельно — иначе два канала вдвое быстрее набирали бы
    порог, и сводка уходила бы там, где событий было вдвое меньше.
    """
    other = Domain.objects.create(name="api.contoso.ru")

    dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)
    result = dispatch_event(event_id=make_event(other).pk, at=MOMENT, state_store=store)

    assert isinstance(result, Queued)
    assert [item.in_window for item in result.to] == [2, 2]


# ─────────────── доставка ───────────────


def test_d11_each_channel_is_delivered_by_its_own_implementation(
    domain: Domain, store: FakeStateStore
) -> None:
    dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)
    channels = both_channels()

    run = deliver_due(at=AFTER_WINDOW, channels=channels, state_store=store)

    assert run.sent == 2
    assert channels[ChannelKind.TELEGRAM.value].messages[0].recipient == CHAT
    assert channels[ChannelKind.EMAIL.value].messages[0].recipient.startswith("duty@")


def test_d11_one_channel_failing_does_not_affect_the_other(
    domain: Domain, store: FakeStateStore
) -> None:
    """
    Судьба доставки у каждого канала своя — ровно ради этого связь и стала многие-ко-многим.

    Раньше событие ссылалось на одно уведомление, и «доставлено» либо «не доставлено» было
    одно на событие. Теперь в журнале рядом стоят оба исхода.
    """
    dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)
    channels = {
        ChannelKind.TELEGRAM.value: FakeChannel(),
        ChannelKind.EMAIL.value: FakeChannel(
            results=[NotSent(error="сервер не отвечает", retryable=True)]
        ),
    }

    run = deliver_due(at=AFTER_WINDOW, channels=channels, state_store=store)

    assert (run.sent, run.failed) == (1, 1)
    statuses = dict(Notification.objects.values_list("channel", "status"))
    assert statuses[ChannelKind.TELEGRAM.value] == DeliveryStatus.SENT
    assert statuses[ChannelKind.EMAIL.value] == DeliveryStatus.PENDING


def test_d11_a_row_without_an_implementation_is_not_retried_forever(
    domain: Domain, store: FakeStateStore
) -> None:
    """
    Строка есть, отправлять нечем — это ошибка маршрутизации, и она обязана быть видна.

    Оставить строку в очереди нельзя: она бралась бы каждый круг доставки и не ушла бы никогда.
    """
    dispatch_event(event_id=make_event(domain).pk, at=MOMENT, state_store=store)

    run = deliver_due(
        at=AFTER_WINDOW,
        channels={ChannelKind.TELEGRAM.value: FakeChannel()},
        state_store=store,
    )

    assert run.gave_up == 1
    email = Notification.objects.get(channel=ChannelKind.EMAIL)
    assert email.status == DeliveryStatus.FAILED
    assert "нет реализации" in email.last_error


# ─────────────── ветка переписки у каждого канала своя ───────────────


def test_7_3_reply_goes_into_the_thread_of_the_same_channel(
    domain: Domain, store: FakeStateStore
) -> None:
    """
    Идентификатор сообщения выдаёт сам канал: у Telegram это число, у почты — `Message-ID`.

    Ответить в Telegram на `Message-ID` письма нельзя, поэтому открывающее сообщение ищется с
    отбором по каналу. Пока канал был один, вопроса не возникало, а хранился он одним полем
    инцидента — и с двумя каналами одно из двух продолжений ушло бы не в ту ветку.
    """
    incident = Incident.objects.create(domain=domain, opened_at=MOMENT - timedelta(hours=1))
    dispatch_event(
        event_id=make_event(domain, incident=incident).pk, at=MOMENT, state_store=store
    )
    deliver_due(at=AFTER_WINDOW, channels=both_channels(), state_store=store)
    openings = dict(
        Notification.objects.filter(status=DeliveryStatus.SENT).values_list("channel", "pk")
    )

    closing = make_event(domain, event_type=EventType.BACK_TO_NORMAL, incident=incident)
    dispatch_event(event_id=closing.pk, at=AFTER_WINDOW, state_store=store)

    replies = dict(
        closing.notifications.values_list("channel", "reply_to_id")  # type: ignore[misc]
    )
    assert replies[ChannelKind.TELEGRAM.value] == openings[ChannelKind.TELEGRAM.value]
    assert replies[ChannelKind.EMAIL.value] == openings[ChannelKind.EMAIL.value]
