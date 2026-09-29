"""
Порог актуальности — ТЗ 6.2, 7.2, критерии 6-9.

Сутки модельного времени прогоняются без `sleep`: момент передаётся в проверку явно, а резолвер
подменяется портом. Ровно ради этого `at` и вынесен в параметр.
"""

import ipaddress
from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings
from apps.monitoring.models import Event, EventType
from apps.monitoring.selectors.addresses import address_statuses, fresh_set
from apps.monitoring.services.checking import run_check
from core.adapters.fake_resolver import FakeResolver, answered
from core.types.checking import AddressStatus

pytestmark = pytest.mark.django_db

START = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
THRESHOLD = timedelta(hours=24)
ALL_THREE = ("10.20.1.14", "10.20.2.60", "10.20.2.61")
LEAVING = "10.20.2.61"
NAME = "pay-gw.contoso.ru"


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name=NAME)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def check(domain: Domain, resolver: FakeResolver, at: datetime) -> None:
    run_check(domain_id=domain.pk, at=at, resolver=resolver)


def events(domain: Domain, event_type: EventType) -> int:
    return Event.objects.filter(domain=domain, event_type=event_type).count()


def status_of(domain: Domain, at: datetime) -> dict[str, AddressStatus]:
    return {str(view.address): view.status for view in address_statuses(domain.pk, at, THRESHOLD)}


def addresses(values: tuple[str, ...]) -> set[object]:
    return {ipaddress.ip_address(value) for value in values}


def run_hourly(domain: Domain, resolver: FakeResolver, hours: int, since: datetime) -> datetime:
    at = since
    for _ in range(hours):
        at += timedelta(hours=1)
        check(domain, resolver, at)
    return at


@pytest.fixture(autouse=True)
def _threshold_of_one_day() -> None:
    """
    Порог актуальности прибит к суткам, а не взят из умолчания.

    Умолчание — 14 суток (Q-D47), и тесты про сам порог обязаны называть своё значение сами:
    иначе они молча меняют смысл вместе с настройкой продукта.
    """
    GlobalSettings.objects.filter(pk=1).update(
        freshness_threshold_seconds=int(THRESHOLD.total_seconds())
    )


def test_6_rotation_inside_the_threshold_produces_no_events(domain: Domain) -> None:
    # Первый ответ ставит отправную точку: домен отдал все три адреса (7.4, базовая проверка).
    # Дальше DNS перебирает их по кругу, отдавая по два из трёх.
    resolver = FakeResolver({NAME: [answered(*ALL_THREE)]})
    check(domain, resolver, START)
    resolver.set(
        NAME,
        [
            answered("10.20.1.14", "10.20.2.60"),
            answered("10.20.2.60", "10.20.2.61"),
            answered("10.20.2.61", "10.20.1.14"),
        ],
    )

    at = START
    for _ in range(12):  # час при периоде 5 минут
        at += timedelta(minutes=5)
        check(domain, resolver, at)

    # Актуальный набор всё время содержит три адреса, хотя в каждом ответе их по два (7.2).
    assert fresh_set(domain.pk, at, THRESHOLD) == addresses(ALL_THREE)
    assert events(domain, EventType.ADDRESS_APPEARED) == 0
    assert events(domain, EventType.ADDRESS_GONE) == 0


def test_7_address_missing_less_than_the_threshold_stays_fresh(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered(*ALL_THREE)]})
    check(domain, resolver, START)
    resolver.set(NAME, [answered("10.20.1.14", "10.20.2.60")])

    at = run_hourly(domain, resolver, hours=23, since=START)

    assert status_of(domain, at)[LEAVING] is AddressStatus.MATCHED
    assert events(domain, EventType.ADDRESS_GONE) == 0


def test_8_crossing_the_threshold_produces_exactly_one_gone(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered(*ALL_THREE)]})
    check(domain, resolver, START)
    resolver.set(NAME, [answered("10.20.1.14", "10.20.2.60")])

    at = run_hourly(domain, resolver, hours=27, since=START)

    assert status_of(domain, at)[LEAVING] is AddressStatus.STALE
    # Событие возникает единственный раз — в момент пересечения границы (7.2).
    assert events(domain, EventType.ADDRESS_GONE) == 1
    assert fresh_set(domain.pk, at, THRESHOLD) == addresses(("10.20.1.14", "10.20.2.60"))


def test_9_address_returning_after_going_stale_appears_again(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered(*ALL_THREE)]})
    check(domain, resolver, START)
    resolver.set(NAME, [answered("10.20.1.14", "10.20.2.60")])

    at = run_hourly(domain, resolver, hours=27, since=START)
    assert events(domain, EventType.ADDRESS_GONE) == 1

    # Адрес вернулся.
    resolver.set(NAME, [answered(*ALL_THREE)])
    at += timedelta(hours=1)
    check(domain, resolver, at)

    assert events(domain, EventType.ADDRESS_APPEARED) == 1
    assert status_of(domain, at)[LEAVING] is AddressStatus.MATCHED
