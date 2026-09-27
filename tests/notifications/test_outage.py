"""Уведомление об аварии — ТЗ 11.13, критерий 65."""

from datetime import UTC, datetime

import pytest

from apps.catalog.models import Domain, GlobalSettings
from apps.monitoring.services.runner import TickSkipped, run_one_tick
from apps.notifications.models import Notification
from apps.notifications.services.outage import AlertSent, AlertSkipped, send_outage_alert
from core.adapters.fake_channel import FakeChannel
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore
from core.ports.notification_channel import NotSent

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
CHAT = "-1001234567890"


@pytest.fixture
def configured() -> None:
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)


def test_65_alert_goes_straight_to_the_channel(configured: None) -> None:
    # Критерий 65: сообщение доставлено, хотя очередь и окно слияния недоступны.
    channel = FakeChannel()

    result = send_outage_alert(channel=channel, component="redis", detail="connection refused")

    assert isinstance(result, AlertSent)
    text = channel.messages[0].text
    assert "Проверки остановлены" in text
    assert "redis" in text
    assert "никто не следит" in text
    # Ни строки в очереди: путь в обход очереди — это и есть требование 11.13.
    assert Notification.objects.count() == 0


def test_11_13_alert_names_the_failed_component(configured: None) -> None:
    channel = FakeChannel()

    send_outage_alert(channel=channel, component="postgresql")

    assert "postgresql" in channel.messages[0].text


def test_8_3_unconfigured_channel_is_skipped_quietly() -> None:
    result = send_outage_alert(channel=FakeChannel(configured=False), component="redis")

    assert isinstance(result, AlertSkipped)


def test_11_13_missing_recipient_is_skipped_quietly() -> None:
    result = send_outage_alert(channel=FakeChannel(), component="redis")

    assert result == AlertSkipped(reason="адресат не задан")


def test_11_13_failed_delivery_does_not_raise(configured: None) -> None:
    # Повторять нечем: очередь по условию недоступна. Но падать сервис не должен.
    channel = FakeChannel(results=[NotSent(error="канал недоступен", retryable=True)])

    result = send_outage_alert(channel=channel, component="redis")

    assert result == AlertSkipped(reason="канал недоступен")


def test_11_13_checker_survives_the_outage_path(configured: None) -> None:
    """
    Тик при недоступном хранилище не падает и сообщает о нездоровье.

    Отправку в канал этот тест не проверяет: `run_one_tick` строит канал из окружения, а токен
    в тестах не задан. Сам путь «напрямую в канал, минуя очередь» покрыт тестом критерия 65 выше.
    """
    Domain.objects.create(name="pay-gw.contoso.ru", next_check_at=MOMENT)
    clock = FakeClock(MOMENT)
    store = FakeStateStore(clock, available=False)

    first = run_one_tick(clock=clock, state_store=store)
    second = run_one_tick(clock=clock, state_store=store, previously_healthy=False)

    assert isinstance(first, TickSkipped)
    assert first.healthy is False
    assert isinstance(second, TickSkipped)
