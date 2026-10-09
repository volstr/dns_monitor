"""
Настройка «Проверять адреса IPv6» — решение Q-D27.

Снятый флаг означает не «перестать обновлять», а «адресов IPv6 не существует». Разница
проверяется прямо: записи AAAA не запрашиваются, адреса не попадают в реестр, прежние исчезают
с экранов и **не порождают** `ADDRESS_GONE` — ради отсутствия этого шума флаг и снимают.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings
from apps.monitoring.models import AddressRegistryEntry, Event, EventType
from apps.monitoring.selectors.addresses import address_statuses, fresh_set
from apps.monitoring.services.checking import run_check
from core.adapters.fake_resolver import FakeResolver, answered
from core.types.dns import RecordType

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)
DAY = timedelta(days=1)
IPV4 = '216.58.198.46'
IPV6 = '2a00:1450:400e:809::200e'


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name='google.com', next_check_at=MOMENT)
    AllowlistEntry.objects.create(domain=domain, entry='10.20.0.0/22', raw='10.20.0.0/22')
    return domain


def disable_ipv6() -> None:
    GlobalSettings.objects.filter(pk=1).update(check_ipv6=False)


def resolver_with_both() -> FakeResolver:
    # Один ответ с двумя адресами: список в тестовом резолвере — это ответы на
    # последовательные вызовы, а не содержимое одного ответа.
    return FakeResolver({'google.com': [answered(IPV4, IPV6)] * 4})


def test_d27_aaaa_is_not_requested_when_the_flag_is_off(domain: Domain) -> None:
    """«Полностью игнорируем» значит и не спрашивать: иначе сервер отвечал бы впустую."""
    disable_ipv6()
    resolver = resolver_with_both()

    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver)

    asked_types = resolver.calls[0][1]
    assert RecordType.AAAA not in asked_types
    assert RecordType.A in asked_types


def test_d27_aaaa_is_requested_when_the_flag_is_on(domain: Domain) -> None:
    resolver = resolver_with_both()

    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver)

    assert RecordType.AAAA in resolver.calls[0][1]


def test_d27_ipv6_never_reaches_the_registry(domain: Domain) -> None:
    disable_ipv6()

    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver_with_both())

    addresses = set(AddressRegistryEntry.objects.values_list('address', flat=True))
    assert str(IPV4) in {str(item) for item in addresses}
    assert IPV6 not in {str(item) for item in addresses}


def test_d27_existing_ipv6_disappears_without_a_gone_event(domain: Domain) -> None:
    """
    Главное свойство: снятие флага не порождает пачку «Адрес пропал».

    Если бы адрес просто перестал обновляться, через порог актуальности он стал бы неактуальным
    и породил событие — ровно тот шум, ради отсутствия которого флаг и снимают.
    """
    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver_with_both())
    assert AddressRegistryEntry.objects.count() == 2
    Event.objects.all().delete()

    disable_ipv6()
    run_check(domain_id=domain.pk, at=MOMENT + timedelta(minutes=5), resolver=resolver_with_both())

    assert Event.objects.filter(event_type=EventType.ADDRESS_GONE).count() == 0


def test_d27_statuses_and_fresh_set_ignore_ipv6(domain: Domain) -> None:
    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver_with_both())
    disable_ipv6()

    statuses = address_statuses(domain.pk, MOMENT, DAY, skip_ipv6=True)
    current = fresh_set(domain.pk, MOMENT, DAY, skip_ipv6=True)

    assert [str(view.address) for view in statuses] == [IPV4]
    assert {str(address) for address in current} == {IPV4}


def test_d27_row_in_the_registry_stays_but_is_invisible(domain: Domain) -> None:
    """Запись не удаляется: включат флаг обратно — история адреса останется целой."""
    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver_with_both())
    disable_ipv6()

    assert AddressRegistryEntry.objects.filter(domain=domain).count() == 2
    assert address_statuses(domain.pk, MOMENT, DAY, skip_ipv6=True) != ()
    assert len(address_statuses(domain.pk, MOMENT, DAY, skip_ipv6=False)) == 2


def test_d27_flag_is_stronger_than_the_domains_own_aaaa(domain: Domain) -> None:
    """Общая настройка главнее типа записи у домена (4.3)."""
    Domain.objects.filter(pk=domain.pk).update(record_types=['a', 'aaaa'])
    disable_ipv6()
    resolver = resolver_with_both()

    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver)

    assert RecordType.AAAA not in resolver.calls[0][1]


def test_d27_ipv4_only_domain_is_unaffected(domain: Domain) -> None:
    resolver = FakeResolver({'google.com': [answered(IPV4)]})

    run_check(domain_id=domain.pk, at=MOMENT, resolver=resolver)

    assert AddressRegistryEntry.objects.count() == 1
