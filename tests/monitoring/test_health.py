"""Авария инфраструктуры — ТЗ 11.13, Q-E4, критерий 64."""

from datetime import UTC, datetime

import pytest

from apps.accounts.models import AuditAction, AuditRecord
from apps.catalog.models import Domain, GlobalSettings
from apps.monitoring.models import Check
from apps.monitoring.services.health import DATABASE, REDIS, check_health
from apps.monitoring.services.runner import TickSkipped, run_one_tick
from apps.monitoring.services.tick import TickReport
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
NAME = "pay-gw.contoso.ru"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


def test_healthy_infrastructure_is_reported_as_such(store: FakeStateStore) -> None:
    report = check_health(state_store=store)

    assert report.healthy is True
    assert report.failed_component is None


def test_11_13_unavailable_redis_names_the_failed_component(clock: FakeClock) -> None:
    # «Не „что-то пошло не так“», а прямое называние отказавшего компонента.
    store = FakeStateStore(clock, available=False)

    report = check_health(state_store=store)

    assert report.healthy is False
    assert report.failed_component == REDIS
    assert report.detail


def test_known_components_are_named_explicitly() -> None:
    assert (REDIS, DATABASE) == ("redis", "postgresql")


def test_64_checks_stop_while_the_infrastructure_is_down(clock: FakeClock) -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["127.0.0.1"])
    Domain.objects.create(name=NAME, next_check_at=MOMENT)
    store = FakeStateStore(clock, available=False)

    result = run_one_tick(clock=clock, state_store=store)

    assert isinstance(result, TickSkipped)
    assert result.healthy is False
    assert REDIS in result.reason
    # Ни одной проверки не выполнено, домен не тронут.
    assert Check.objects.count() == 0


def test_64_stopping_checks_is_recorded_in_the_journal(clock: FakeClock) -> None:
    store = FakeStateStore(clock, available=False)

    run_one_tick(clock=clock, state_store=store)

    record = AuditRecord.objects.get()
    assert record.action == AuditAction.CHECKS_STOPPED
    assert record.occurred_at == MOMENT
    # Остановку вызвал не человек — автора у записи нет (Q-D4).
    assert record.author is None


def test_11_13_journal_is_not_flooded_every_tick(clock: FakeClock) -> None:
    # Тик идёт раз в пять секунд: запись об аварии нужна одна, а не двенадцать в минуту.
    store = FakeStateStore(clock, available=False)

    first = run_one_tick(clock=clock, state_store=store)
    assert isinstance(first, TickSkipped)
    run_one_tick(clock=clock, state_store=store, previously_healthy=first.healthy)
    run_one_tick(clock=clock, state_store=store, previously_healthy=first.healthy)

    assert AuditRecord.objects.filter(action=AuditAction.CHECKS_STOPPED).count() == 1


def test_recovered_infrastructure_resumes_checks(clock: FakeClock) -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["127.0.0.1"])
    Domain.objects.create(name=NAME, next_check_at=MOMENT)
    store = FakeStateStore(clock, available=False)
    run_one_tick(clock=clock, state_store=store)

    store.available = True
    result = run_one_tick(clock=clock, state_store=store, previously_healthy=False)

    assert isinstance(result, TickReport)
    assert result.claimed == 1
