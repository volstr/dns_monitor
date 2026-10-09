"""Ручная проверка — ТЗ 11.6, 11.11, Q-D18, критерии 47 и 48."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import Domain, Group
from apps.monitoring.services.manual_check import (
    CONFIRMATION_THRESHOLD,
    CheckRejected,
    CheckRequested,
    RejectionReason,
    request_domain_check,
    request_group_check,
)
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
LATER = MOMENT + timedelta(minutes=5)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


@pytest.fixture
def group() -> Group:
    return Group.objects.create(name='Платёжный контур')


def make_domain(name: str, *, group: Group | None = None, enabled: bool = True) -> Domain:
    return Domain.objects.create(
        name=name, group=group, watch_enabled=enabled, next_check_at=LATER
    )


def test_48_manual_check_moves_the_next_scheduled_check(store: FakeStateStore) -> None:
    domain = make_domain('pay-gw.contoso.ru')

    result = request_domain_check(domain_id=domain.pk, at=MOMENT, state_store=store)

    assert result == CheckRequested(domains=1)
    domain.refresh_from_db()
    # Время следующей проверки сдвинуто: ближайший тик разберёт домен, а не через пять минут.
    assert domain.next_check_at == MOMENT


def test_47_second_domain_check_is_rejected_by_cooldown(store: FakeStateStore) -> None:
    domain = make_domain('pay-gw.contoso.ru')
    request_domain_check(domain_id=domain.pk, at=MOMENT, state_store=store)

    result = request_domain_check(domain_id=domain.pk, at=MOMENT, state_store=store)

    assert result == CheckRejected(reason=RejectionReason.COOLDOWN, domains=1)


def test_47_domain_cooldown_lasts_thirty_seconds(
    store: FakeStateStore, clock: FakeClock
) -> None:
    domain = make_domain('pay-gw.contoso.ru')
    request_domain_check(domain_id=domain.pk, at=MOMENT, state_store=store)

    clock.advance(timedelta(seconds=31))
    result = request_domain_check(domain_id=domain.pk, at=clock.now(), state_store=store)

    assert isinstance(result, CheckRequested)


def test_11_1_disabled_domain_is_not_queued(store: FakeStateStore) -> None:
    domain = make_domain('status.contoso.ru', enabled=False)

    result = request_domain_check(domain_id=domain.pk, at=MOMENT, state_store=store)

    assert result == CheckRejected(reason=RejectionReason.NOTHING_TO_CHECK)
    domain.refresh_from_db()
    assert domain.next_check_at == LATER


def test_11_6_group_check_queues_every_enabled_domain(
    store: FakeStateStore, group: Group
) -> None:
    for index in range(3):
        make_domain(f'domain-{index}.contoso.ru', group=group)
    make_domain('disabled.contoso.ru', group=group, enabled=False)

    result = request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    assert result == CheckRequested(domains=3)
    assert Domain.objects.filter(group=group, next_check_at=MOMENT).count() == 3


def test_47_second_group_check_is_rejected_by_cooldown(
    store: FakeStateStore, group: Group
) -> None:
    make_domain('pay-gw.contoso.ru', group=group)
    request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    result = request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    assert result == CheckRejected(reason=RejectionReason.COOLDOWN, domains=1)


def test_47_group_cooldown_lasts_a_minute(
    store: FakeStateStore, clock: FakeClock, group: Group
) -> None:
    make_domain('pay-gw.contoso.ru', group=group)
    request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    clock.advance(timedelta(seconds=31))
    assert isinstance(
        request_group_check(group_id=group.pk, at=clock.now(), state_store=store), CheckRejected
    )
    clock.advance(timedelta(seconds=30))
    assert isinstance(
        request_group_check(group_id=group.pk, at=clock.now(), state_store=store), CheckRequested
    )


def test_11_6_large_group_needs_confirmation(store: FakeStateStore, group: Group) -> None:
    Domain.objects.bulk_create(
        Domain(name=f'domain-{index}.contoso.ru', group=group, next_check_at=LATER)
        for index in range(CONFIRMATION_THRESHOLD + 1)
    )

    result = request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    assert result == CheckRejected(
        reason=RejectionReason.NEEDS_CONFIRMATION, domains=CONFIRMATION_THRESHOLD + 1
    )
    # Ничего не поставлено в очередь и кулдаун не израсходован: подтверждение — ещё не запуск.
    assert Domain.objects.filter(next_check_at=MOMENT).count() == 0


def test_11_6_confirmed_large_group_is_queued(store: FakeStateStore, group: Group) -> None:
    Domain.objects.bulk_create(
        Domain(name=f'domain-{index}.contoso.ru', group=group, next_check_at=LATER)
        for index in range(CONFIRMATION_THRESHOLD + 1)
    )

    result = request_group_check(group_id=group.pk, at=MOMENT, state_store=store, confirmed=True)

    assert result == CheckRequested(domains=CONFIRMATION_THRESHOLD + 1)
    assert Domain.objects.filter(next_check_at=MOMENT).count() == CONFIRMATION_THRESHOLD + 1


def test_empty_group_has_nothing_to_check(store: FakeStateStore, group: Group) -> None:
    result = request_group_check(group_id=group.pk, at=MOMENT, state_store=store)

    assert result == CheckRejected(reason=RejectionReason.NOTHING_TO_CHECK)
