"""Тик планировщика — ТЗ 11.1, 11.6, 11.11, plan.md этап 4."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import (
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.monitoring.models import Check
from apps.monitoring.services.scheduling import RESERVATION, SUCCESSFUL_HISTORY
from apps.monitoring.services.tick import tick
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification
from apps.notifications.services.notifier import deliver_due
from core.adapters.fake_channel import FakeChannel
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore
from core.ports.notification_channel import NotSent
from core.ports.state_store import scheduler_lock
from core.types.checking import CheckOutcome

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
NAME = "pay-gw.contoso.ru"


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name=NAME, next_check_at=MOMENT)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def resolver_for(*names: str) -> FakeResolver:
    return FakeResolver({name: [answered("10.20.1.14")] for name in names})


def test_11_1_tick_checks_a_due_domain(domain: Domain, store: FakeStateStore) -> None:
    resolver = resolver_for(NAME)

    report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    assert report == type(report)(claimed=1, checked=1, skipped=0, failed=0)
    assert resolver.calls[0][0] == NAME
    assert Check.objects.filter(domain=domain).count() == 1


def test_11_1_real_next_check_at_is_written_after_the_check(
    domain: Domain, store: FakeStateStore
) -> None:
    group = Group.objects.create(name="Платёжный контур", check_period_seconds=5 * 60)
    domain.group = group
    domain.save(update_fields=["group"])

    tick(at=MOMENT, resolver=resolver_for(NAME), state_store=store, concurrency=1)

    domain.refresh_from_db()
    # Не резервирование, а настоящий период группы от момента проверки.
    assert domain.next_check_at == MOMENT + timedelta(minutes=5)
    assert domain.next_check_at != MOMENT + RESERVATION


def test_11_6_domain_locked_by_a_manual_check_is_skipped(
    domain: Domain, store: FakeStateStore
) -> None:
    # Ключ занят: домен уже проверяется по кнопке «Проверить сейчас».
    store.acquire(scheduler_lock(domain.pk))
    resolver = resolver_for(NAME)

    report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    assert (report.claimed, report.checked, report.skipped) == (1, 0, 1)
    assert resolver.calls == []


def test_11_11_lock_is_released_after_the_check(domain: Domain, store: FakeStateStore) -> None:
    tick(at=MOMENT, resolver=resolver_for(NAME), state_store=store, concurrency=1)

    assert store.is_held(scheduler_lock(domain.pk)) is False


def test_one_broken_domain_does_not_stop_the_tick(store: FakeStateStore) -> None:
    Domain.objects.create(name="broken.contoso.ru", next_check_at=MOMENT)
    healthy = Domain.objects.create(name=NAME, next_check_at=MOMENT)
    # Для broken ответы не заданы: тестовый резолвер бросит LookupError.
    resolver = resolver_for(NAME)

    report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    assert (report.claimed, report.checked, report.failed) == (2, 1, 1)
    assert Check.objects.filter(domain=healthy).count() == 1


def test_11_11_lock_is_released_even_when_the_check_fails(store: FakeStateStore) -> None:
    broken = Domain.objects.create(name="broken.contoso.ru", next_check_at=MOMENT)

    tick(at=MOMENT, resolver=FakeResolver(), state_store=store, concurrency=1)

    assert store.is_held(scheduler_lock(broken.pk)) is False


def test_8_1_check_queues_a_notification_for_its_event(
    domain: Domain, store: FakeStateStore
) -> None:
    """Сквозной путь: проверка → событие → очередь исходящих."""
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id="-1001234567890", notification_mode=NotificationMode.NOT_ALLOWED
    )
    # Адрес вне списка: список у домена есть (фикстура), значит вхождение вычисляется.
    resolver = FakeResolver({NAME: [answered("185.44.9.201")]})

    tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    notification = Notification.objects.get()
    assert notification.recipient == "-1001234567890"
    assert "Адрес вне разрешённых" in notification.text
    # Уведомление ждёт закрытия окна слияния, а не уходит немедленно (Q-D9).
    assert notification.next_attempt_at > MOMENT


def test_8_1_check_without_notifications_queues_nothing(
    domain: Domain, store: FakeStateStore
) -> None:
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NONE)
    resolver = FakeResolver({NAME: [answered("185.44.9.201")]})

    tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    assert Notification.objects.count() == 0


def test_8_3_broken_channel_does_not_stop_checks(domain: Domain, store: FakeStateStore) -> None:
    """
    Условие сдачи этапа 5: падение канала не мешает проверкам идти.

    Архитектурно это обеспечено разделением процессов — постановка лишь пишет строку в очередь,
    а канала касается только `run_notifier`. Здесь это закреплено проверкой, а не рассуждением.
    """
    GlobalSettings.objects.filter(pk=1).update(
        telegram_chat_id="-1001234567890", notification_mode=NotificationMode.NOT_ALLOWED
    )
    resolver = FakeResolver({NAME: [answered("185.44.9.201")]})
    tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1)

    # Канал лежит: доставка не проходит, строка остаётся в очереди.
    channel = FakeChannel(results=[NotSent(error="канал недоступен", retryable=True)] * 5)
    deliver_due(
        at=MOMENT + timedelta(minutes=2),
        channels={ChannelKind.TELEGRAM.value: channel},
        state_store=store,
    )
    assert Notification.objects.filter(status=DeliveryStatus.PENDING).count() == 1

    # Проверки при этом продолжаются как ни в чём не бывало.
    later = MOMENT + timedelta(minutes=10)
    Domain.objects.filter(pk=domain.pk).update(next_check_at=later)
    report = tick(at=later, resolver=resolver, state_store=store, concurrency=1)

    assert (report.claimed, report.checked, report.failed) == (1, 1, 0)
    assert Check.objects.filter(domain=domain).count() == 2


def test_empty_tick_does_nothing(store: FakeStateStore) -> None:
    report = tick(at=MOMENT, resolver=FakeResolver(), state_store=store, concurrency=1)

    assert (report.claimed, report.checked) == (0, 0)


def test_d23_history_is_pruned_after_every_check(domain: Domain, store: FakeStateStore) -> None:
    for index in range(SUCCESSFUL_HISTORY + 5):
        Check.objects.create(
            domain=domain,
            checked_at=MOMENT - timedelta(minutes=index + 1),
            outcome=CheckOutcome.NORMAL,
        )

    tick(at=MOMENT, resolver=resolver_for(NAME), state_store=store, concurrency=1)

    assert Check.objects.filter(domain=domain).count() == SUCCESSFUL_HISTORY


def test_tick_claims_no_more_than_the_limit(store: FakeStateStore) -> None:
    for index in range(5):
        Domain.objects.create(name=f"domain-{index}.contoso.ru", next_check_at=MOMENT)
    resolver = FakeResolver(
        {f"domain-{index}.contoso.ru": [answered("10.20.1.14")] for index in range(5)}
    )

    report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1, limit=2)

    assert report.claimed == 2


@pytest.mark.django_db(transaction=True)
def test_11_1_pool_checks_domains_in_parallel(global_settings: object) -> None:
    """
    Путь через ThreadPoolExecutor: у каждого потока своё соединение с БД.

    Общие настройки запрашиваются фикстурой: транзакционный тест вычищает таблицы, и строка,
    созданную миграцией, приходится восстанавливать.
    """
    store = FakeStateStore(FakeClock(MOMENT))
    names = [f"domain-{index}.contoso.ru" for index in range(4)]
    for name in names:
        Domain.objects.create(name=name, next_check_at=MOMENT)
    resolver = FakeResolver({name: [answered("10.20.1.14")] for name in names})

    try:
        report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=4)

        assert (report.claimed, report.checked, report.failed) == (4, 4, 0)
        assert Check.objects.count() == 4
    finally:
        Domain.objects.all().delete()
