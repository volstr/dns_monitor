"""Повторные напоминания — ТЗ 8.5, критерии 40-43, решения Q-D7 и Q-D14."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import (
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.monitoring.models import AddressRegistryEntry, Event, EventType, Incident
from apps.notifications.models import Notification
from apps.notifications.services.reminders import queue_due_reminders
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
SIX_HOURS = timedelta(hours=6)
CHAT = '-1001234567890'
OPENING_ID = '42'


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


@pytest.fixture
def incident() -> Incident:
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id=CHAT, notification_mode=NotificationMode.NOT_ALLOWED
    )
    domain = Domain.objects.create(name='pay-gw.contoso.ru')
    AllowlistEntry.objects.create(domain=domain, entry='10.20.0.0/22', raw='10.20.0.0/22')
    AddressRegistryEntry.objects.create(
        domain=domain, address='185.44.9.201', first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    opening = Notification.objects.create(
        channel='telegram',
        recipient=CHAT,
        text='Адрес вне разрешённых',
        created_at=MOMENT,
        next_attempt_at=MOMENT,
        channel_message_id=OPENING_ID,
        status='sent',
    )
    incident = Incident.objects.create(domain=domain, opened_at=MOMENT)
    # Открывающее сообщение находится по связи «событие ↔ уведомление» с отбором по каналу:
    # у инцидента столько веток, сколько каналов (Q-D11). Поэтому событие обязательно.
    event = Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=MOMENT,
        payload={'addresses': ['185.44.9.201']},
        incident=incident,
    )
    event.notifications.add(opening)
    return incident


def reminders(at: datetime, store: FakeStateStore) -> int:
    return queue_due_reminders(at=at, state_store=store).queued


def test_40_one_reminder_after_the_interval(incident: Incident, store: FakeStateStore) -> None:
    # Критерий 40: инцидент держится 7 часов при интервале 6 → ровно одно напоминание.
    seven_hours = MOMENT + timedelta(hours=7)

    assert reminders(MOMENT + timedelta(hours=5), store) == 0
    assert reminders(seven_hours, store) == 1
    # Повторно в тот же момент — уже нет: отсчёт пошёл заново.
    assert reminders(seven_hours, store) == 0


def test_8_5_reminder_answers_into_the_opening_thread(
    incident: Incident, store: FakeStateStore
) -> None:
    reminders(MOMENT + SIX_HOURS, store)

    reminder = Notification.objects.exclude(channel_message_id=OPENING_ID).get()
    assert reminder.reply_to is not None
    assert reminder.reply_to.channel_message_id == OPENING_ID


def test_8_5_reminder_carries_age_and_current_addresses(
    incident: Incident, store: FakeStateStore
) -> None:
    reminders(MOMENT + timedelta(hours=7), store)

    text = Notification.objects.exclude(channel_message_id=OPENING_ID).get().text
    assert 'Расхождение держится' in text
    assert 'Возраст: 7 ч 0 мин' in text
    assert '185.44.9.201' in text
    # Комментарий домена в напоминании не повторяется (8.5).
    assert 'Открыть домен' in text


def test_41_a_message_about_a_new_address_counts_as_a_reminder(
    incident: Incident, store: FakeStateStore
) -> None:
    # 8.5: если за интервал состав изменился, уходит обычное сообщение, и оно засчитывается.
    Incident.objects.filter(pk=incident.pk).update(last_message_at=MOMENT + timedelta(hours=5))

    assert reminders(MOMENT + timedelta(hours=7), store) == 0


def test_42_never_remind_means_no_reminders(incident: Incident, store: FakeStateStore) -> None:
    group = Group.objects.create(name='Тестовые стенды', reminder_interval_seconds=0)
    Domain.objects.filter(pk=incident.domain_id).update(group=group)

    assert reminders(MOMENT + timedelta(days=1), store) == 0


def test_43_closed_incident_stops_reminders(incident: Incident, store: FakeStateStore) -> None:
    Incident.objects.filter(pk=incident.pk).update(closed_at=MOMENT + timedelta(minutes=26))

    assert reminders(MOMENT + timedelta(hours=7), store) == 0


def test_d7_no_opening_message_means_no_reminders(
    incident: Incident, store: FakeStateStore
) -> None:
    # Продолжение существует только при наличии открывающего сообщения. «Нет открывающего» —
    # это теперь отсутствие доставленного уведомления по инциденту, а не пустая строка в поле.
    Notification.objects.update(channel_message_id='')

    assert reminders(MOMENT + timedelta(hours=7), store) == 0


def test_d14_disabled_domain_pauses_reminders(
    incident: Incident, store: FakeStateStore
) -> None:
    Domain.objects.filter(pk=incident.domain_id).update(watch_enabled=False)

    assert reminders(MOMENT + timedelta(hours=7), store) == 0
    # Инцидент при этом остаётся открытым — Q-D14.
    assert Incident.objects.filter(closed_at__isnull=True).count() == 1
