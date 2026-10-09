"""Сборка портов и один обход по настройкам установки — ТЗ 4.1, 6.1, 11.1."""

from datetime import UTC, datetime

import pytest

from apps.catalog.models import Domain, GlobalSettings
from apps.monitoring.models import Check
from apps.monitoring.services.runner import TickSkipped, describe, run_one_tick
from apps.monitoring.services.tick import TickReport
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
NAME = 'pay-gw.contoso.ru'


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


def set_servers(*servers: str) -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=list(servers))


def no_system_servers() -> tuple[str, ...]:
    """Система без резолверов: `/etc/resolv.conf` пуст или его нет."""
    return ()

def test_d51_empty_list_falls_back_to_the_system_servers(
    clock: FakeClock, store: FakeStateStore
) -> None:
    """
    Пустой список в настройках означает «спрашивать у системы» (Q-D51).

    Дальше всё идёт как обычно: тот же обход по порядку, тот же переход к следующему при
    таймауте. Поэтому проверяется не особый режим, а то, что обход **состоялся**: адрес
    заведомо не отвечает, и это штатный исход 6.1, а не отказ тика.
    """
    Domain.objects.create(name=NAME, next_check_at=MOMENT)

    report = run_one_tick(
        clock=clock, state_store=store, servers_of_system=lambda: ('127.0.0.1',)
    )

    assert not isinstance(report, TickSkipped)
    assert Check.objects.count() == 1


def test_4_1_tick_is_skipped_when_nobody_has_a_server(
    clock: FakeClock, store: FakeStateStore
) -> None:
    """Ни в настройках, ни у системы: падать каждые пять секунд бессмысленно."""
    Domain.objects.create(name=NAME, next_check_at=MOMENT)

    report = run_one_tick(clock=clock, state_store=store, servers_of_system=no_system_servers)

    assert isinstance(report, TickSkipped)
    assert 'DNS-сервера' in report.reason
    assert Check.objects.count() == 0


def test_empty_list_does_not_consume_the_domain(clock: FakeClock, store: FakeStateStore) -> None:
    # Домен не должен уйти в резервирование из-за того, что проверять его было нечем.
    domain = Domain.objects.create(name=NAME, next_check_at=MOMENT)

    run_one_tick(clock=clock, state_store=store, servers_of_system=no_system_servers)

    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT


def test_11_1_tick_runs_with_configured_servers(clock: FakeClock, store: FakeStateStore) -> None:
    # Серверов нет в природе, но обход обязан состояться: домен резолвится неудачно и это
    # штатный исход (6.1), а не падение процесса.
    set_servers('127.0.0.1')
    Domain.objects.create(name=NAME, next_check_at=MOMENT)

    report = run_one_tick(clock=clock, state_store=store)

    assert isinstance(report, TickReport)
    assert report.claimed == 1
    assert Check.objects.filter(domain__name=NAME).count() == 1


def test_no_due_domains_is_an_empty_tick(clock: FakeClock, store: FakeStateStore) -> None:
    set_servers('127.0.0.1')

    report = run_one_tick(clock=clock, state_store=store)

    assert isinstance(report, TickReport)
    assert (report.claimed, report.checked) == (0, 0)


def test_describe_names_what_happened() -> None:
    skipped = describe(TickSkipped(reason='список DNS-серверов пуст'), MOMENT)
    done = describe(TickReport(claimed=3, checked=2, skipped=1, failed=0), MOMENT)

    assert 'пропущен' in skipped
    assert 'взято 3' in done
    assert 'проверено 2' in done
