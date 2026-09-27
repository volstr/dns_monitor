"""
Сутки работы планировщика без `sleep` — plan.md, планка этапа 4.

Поштучные тесты тика проверяют один обход. Этот проверяет то, чего они увидеть не могут:
планировщик **самоподдерживается**. Каждый тик забирает наступившее, проверяет, пишет настоящее
`next_check_at = момент + период группы`, и домен возвращается ровно через период — сутки подряд.

Здесь же видно, что расписание живёт только в `next_check_at`: два домена с разными периодами
групп идут каждый своим тактом, хотя тик у них общий.

Ни одного `sleep`: время двигают часы, как и требует plan.md.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, Group
from apps.monitoring.models import Check
from apps.monitoring.services.scheduling import SUCCESSFUL_HISTORY
from apps.monitoring.services.tick import tick
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

START = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
TICKS_PER_DAY = 288  # сутки при шаге в пять минут

FAST = "pay-gw.contoso.ru"
SLOW = "cdn.contoso.ru"
ADDRESS = "10.20.1.14"


@pytest.fixture
def domains() -> tuple[Domain, Domain]:
    """Два домена: один с общим периодом (5 минут), другой с групповым (15 минут)."""
    slow_group = Group.objects.create(name="Внешние подрядчики", check_period_seconds=15 * 60)
    fast = Domain.objects.create(name=FAST, next_check_at=START)
    slow = Domain.objects.create(name=SLOW, group=slow_group, next_check_at=START)
    for domain in (fast, slow):
        AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return fast, slow


def test_scheduler_runs_a_full_day_without_sleeping(domains: tuple[Domain, Domain]) -> None:
    fast, slow = domains
    clock = FakeClock(START)
    store = FakeStateStore(clock)
    resolver = FakeResolver({FAST: [answered(ADDRESS)], SLOW: [answered(ADDRESS)]})

    checked = 0
    for _ in range(TICKS_PER_DAY):
        report = tick(at=clock.now(), resolver=resolver, state_store=store, concurrency=1)
        assert report.failed == 0
        checked += report.checked
        clock.advance(STEP)

    # Домен с периодом 5 минут проверен на каждом тике, с периодом 15 минут — на каждом третьем.
    assert checked == TICKS_PER_DAY + TICKS_PER_DAY // 3

    # Расписание не уехало и не удвоилось: следующая проверка ровно через свой период.
    fast.refresh_from_db()
    slow.refresh_from_db()
    last_tick = START + STEP * (TICKS_PER_DAY - 1)
    assert fast.next_check_at == last_tick + timedelta(minutes=5)
    assert slow.next_check_at == last_tick - timedelta(minutes=10) + timedelta(minutes=15)


def test_d23_history_stays_bounded_over_a_day(domains: tuple[Domain, Domain]) -> None:
    fast, _ = domains
    clock = FakeClock(START)
    store = FakeStateStore(clock)
    resolver = FakeResolver({FAST: [answered(ADDRESS)], SLOW: [answered(ADDRESS)]})

    for _ in range(TICKS_PER_DAY):
        tick(at=clock.now(), resolver=resolver, state_store=store, concurrency=1)
        clock.advance(STEP)

    # За сутки проверок были сотни, а в истории осталось ровно столько, сколько нужно колонке 11.5.
    assert Check.objects.filter(domain=fast).count() == SUCCESSFUL_HISTORY


def test_locks_do_not_leak_over_a_day(domains: tuple[Domain, Domain]) -> None:
    clock = FakeClock(START)
    store = FakeStateStore(clock)
    resolver = FakeResolver({FAST: [answered(ADDRESS)], SLOW: [answered(ADDRESS)]})

    for _ in range(TICKS_PER_DAY):
        tick(at=clock.now(), resolver=resolver, state_store=store, concurrency=1)
        clock.advance(STEP)

    # Каждая блокировка освобождается явно (11.11): после суток работы не висит ни одной.
    assert store.held_keys() == frozenset()
