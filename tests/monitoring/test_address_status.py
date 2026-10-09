"""Актуальный набор и статусы адресов — ТЗ 6.2, 6.3, 6.4, критерии 1, 4, 5, 46."""

import ipaddress
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain
from apps.monitoring.models import AddressRegistryEntry
from apps.monitoring.selectors.addresses import address_statuses, fresh_set
from core.types.checking import AddressStatus

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)
THRESHOLD = timedelta(hours=24)


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name='pay-gw.contoso.ru')


def allow(domain: Domain, raw: str) -> None:
    AllowlistEntry.objects.create(domain=domain, entry=raw, raw=raw)


def seen(domain: Domain, address: str, *, last_seen_at: datetime = MOMENT) -> None:
    AddressRegistryEntry.objects.create(
        domain=domain, address=address, first_seen_at=MOMENT, last_seen_at=last_seen_at
    )


def statuses(domain: Domain, at: datetime = MOMENT) -> dict[str, AddressStatus]:
    return {str(view.address): view.status for view in address_statuses(domain.pk, at, THRESHOLD)}


def test_1_three_addresses_inside_one_subnet_all_match(domain: Domain) -> None:
    allow(domain, '10.20.0.0/22')
    for address in ('10.20.1.14', '10.20.2.60', '10.20.2.61'):
        seen(domain, address)

    assert statuses(domain) == {
        '10.20.1.14': AddressStatus.MATCHED,
        '10.20.2.60': AddressStatus.MATCHED,
        '10.20.2.61': AddressStatus.MATCHED,
    }


def test_2_address_outside_the_list_is_unmatched(domain: Domain) -> None:
    allow(domain, '10.20.0.0/22')
    seen(domain, '185.44.9.201')

    assert statuses(domain)['185.44.9.201'] is AddressStatus.UNMATCHED


def test_46_maskless_entry_reports_an_exact_match(domain: Domain) -> None:
    # Критерий 46 и Q-D1: для записи без маски формулировка «входит в» не используется.
    allow(domain, '10.20.3.7')
    seen(domain, '10.20.3.7')

    match = address_statuses(domain.pk, MOMENT, THRESHOLD)[0].match

    assert match is not None
    assert match.entry == '10.20.3.7'
    assert match.is_network is False


def test_4_subnet_entry_reports_containment(domain: Domain) -> None:
    allow(domain, '10.20.0.0/22')
    seen(domain, '10.20.1.14')

    match = address_statuses(domain.pk, MOMENT, THRESHOLD)[0].match

    assert match is not None
    assert match.entry == '10.20.0.0/22'
    assert match.is_network is True


def test_46_full_mask_typed_by_hand_is_still_an_exact_match(domain: Domain) -> None:
    # Пользователь ввёл /32 — «входит в 10.20.3.7/32» было бы враньём (6.3).
    allow(domain, '10.20.3.7/32')
    seen(domain, '10.20.3.7')

    match = address_statuses(domain.pk, MOMENT, THRESHOLD)[0].match

    assert match is not None
    assert match.is_network is False


def test_5_ipv4_address_does_not_match_an_ipv6_entry(domain: Domain) -> None:
    allow(domain, '2a02:6b8::/32')
    seen(domain, '10.20.3.7')

    assert statuses(domain)['10.20.3.7'] is AddressStatus.UNMATCHED


def test_8_2_empty_allowlist_means_containment_is_not_evaluated(domain: Domain) -> None:
    # 8.2: такой домен «не пришлёт уведомление никогда», и логика там названа верной. Значит
    # пустой список — это не «все адреса не совпали», а «сравнивать не с чем» (Q-D25).
    seen(domain, '10.20.1.14')
    seen(domain, '185.44.9.201')

    assert set(statuses(domain).values()) == {AddressStatus.NOT_CHECKED}


def test_8_2_stale_still_wins_over_an_empty_allowlist(domain: Domain) -> None:
    seen(domain, '10.20.1.14', last_seen_at=MOMENT - THRESHOLD - timedelta(minutes=1))

    assert statuses(domain)['10.20.1.14'] is AddressStatus.STALE


def test_8_2_first_entry_switches_the_check_on(domain: Domain) -> None:
    seen(domain, '185.44.9.201')
    assert statuses(domain)['185.44.9.201'] is AddressStatus.NOT_CHECKED

    allow(domain, '10.20.0.0/22')

    assert statuses(domain)['185.44.9.201'] is AddressStatus.UNMATCHED


def test_6_4_stale_takes_priority_over_matching(domain: Domain) -> None:
    # Приоритет 6.4: сначала «Не актуален», вопрос о вхождении для статуса не задаётся.
    allow(domain, '10.20.0.0/22')
    seen(domain, '10.20.1.14', last_seen_at=MOMENT - THRESHOLD - timedelta(minutes=1))

    view = address_statuses(domain.pk, MOMENT, THRESHOLD)[0]

    assert view.status is AddressStatus.STALE
    # Причина совпадения остаётся видимой — так же показывает прототип.
    assert view.match is not None


def test_6_2_fresh_set_holds_addresses_seen_within_the_threshold(domain: Domain) -> None:
    seen(domain, '10.20.1.14', last_seen_at=MOMENT - timedelta(hours=23))
    seen(domain, '10.20.2.60', last_seen_at=MOMENT - THRESHOLD - timedelta(minutes=1))

    assert fresh_set(domain.pk, MOMENT, THRESHOLD) == {ipaddress.ip_address('10.20.1.14')}


def test_6_2_address_exactly_on_the_border_is_still_fresh(domain: Domain) -> None:
    # Формула 6.2 нестрогая: last_seen_at >= T - W.
    seen(domain, '10.20.1.14', last_seen_at=MOMENT - THRESHOLD)

    assert fresh_set(domain.pk, MOMENT, THRESHOLD) == {ipaddress.ip_address('10.20.1.14')}


def test_statuses_of_the_whole_registry_take_one_query(
    domain: Domain,
    django_assert_num_queries: Callable[[int], AbstractContextManager[None]],
) -> None:
    allow(domain, '10.20.0.0/22')
    allow(domain, '10.30.4.0/24')
    for address in ('10.20.1.14', '10.20.2.60', '10.30.4.8', '185.44.9.201'):
        seen(domain, address)

    with django_assert_num_queries(1):
        address_statuses(domain.pk, MOMENT, THRESHOLD)


def test_registry_of_another_domain_is_not_mixed_in(domain: Domain) -> None:
    other = Domain.objects.create(name='api.contoso.ru')
    allow(domain, '10.20.0.0/22')
    seen(domain, '10.20.1.14')
    seen(other, '10.99.0.1')

    assert set(statuses(domain)) == {'10.20.1.14'}
