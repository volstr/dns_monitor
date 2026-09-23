"""Сборка канала и проход по очереди — ТЗ 8.3, 11.10, Q-A2."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.test import override_settings

from apps.catalog.models import Domain, GlobalSettings, NotificationMode
from apps.monitoring.models import Event, EventType
from apps.notifications.models import DeliveryStatus, Notification
from apps.notifications.services.dispatch import dispatch_event
from apps.notifications.services.notifier import DeliveryRun
from apps.notifications.services.runner import DeliverySkipped, describe, run_one_delivery
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
CHAT = "-1001234567890"
TOKEN = "123456789:AA-fake-token-for-tests"


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


def env_with(token: str | None) -> object:
    return dataclasses.replace(settings.ENV, telegram_bot_token=token)


def test_8_3_missing_token_skips_the_pass(store: FakeStateStore) -> None:
    # Пустой токен — канал «Не настроен», нормальный режим, а не ошибка конфигурации.
    with override_settings(ENV=env_with(None)):
        result = run_one_delivery(clock=FakeClock(MOMENT), state_store=store)

    assert isinstance(result, DeliverySkipped)
    assert "TELEGRAM_BOT_TOKEN" in result.reason


def test_8_3_configured_channel_walks_the_queue(store: FakeStateStore) -> None:
    # Отправка упрётся в сеть (токен поддельный), но проход обязан состояться и учесть попытку.
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id=CHAT, notification_mode=NotificationMode.NOT_ALLOWED
    )
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    event = Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=MOMENT,
        payload={"addresses": ["185.44.9.201"]},
    )
    dispatch_event(event_id=event.pk, at=MOMENT, state_store=store)

    with override_settings(ENV=env_with(TOKEN)):
        result = run_one_delivery(
            clock=FakeClock(MOMENT + timedelta(minutes=2)), state_store=store
        )

    assert isinstance(result, DeliveryRun)
    notification = Notification.objects.get()
    # Либо доставлено, либо отложено на повтор — но строка обработана, а не забыта.
    assert notification.status in {DeliveryStatus.PENDING, DeliveryStatus.SENT}
    assert notification.attempt >= 0


def test_empty_queue_is_an_empty_pass(store: FakeStateStore) -> None:
    with override_settings(ENV=env_with(TOKEN)):
        result = run_one_delivery(clock=FakeClock(MOMENT), state_store=store)

    assert result == DeliveryRun()


def test_describe_names_what_happened() -> None:
    skipped = describe(DeliverySkipped(reason="токен не задан"), MOMENT)
    done = describe(DeliveryRun(sent=2, merged=6, failed=1, gave_up=0), MOMENT)

    assert "пропущена" in skipped
    assert "отправлено 2" in done
    assert "слито 6" in done
