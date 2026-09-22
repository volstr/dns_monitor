"""Белый список: тип колонки и вхождение адреса в подсеть — ТЗ 6.3, 10.2, Q-13.3.

Критерий 5 покрыт здесь же: он про отсутствие совпадения между семействами адресов, а это
свойство запроса, а не логики статусов.
"""

import ipaddress

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, connection, transaction

from apps.catalog.models import AllowlistEntry, Domain

pytestmark = pytest.mark.django_db

ENTRY_COUNT = 5000


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name="pay-gw.contoso.ru")


def test_entry_is_stored_as_network(domain: Domain) -> None:
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    entry = AllowlistEntry.objects.get()

    assert entry.entry == ipaddress.ip_network("10.20.0.0/22")


def test_single_address_is_an_entry_with_full_mask(domain: Domain) -> None:
    # ТЗ 12: отдельной сущности «известные адреса» нет, адрес — это запись с полной маской.
    AllowlistEntry.objects.create(domain=domain, entry="10.20.3.7", raw="10.20.3.7")

    entry = AllowlistEntry.objects.get()

    assert entry.entry.prefixlen == 32
    # В интерфейсе показывается то, что ввёл пользователь (ТЗ 6.3).
    assert entry.raw == "10.20.3.7"


def test_13_3_host_bits_are_rejected_by_the_field(domain: Domain) -> None:
    with pytest.raises(ValidationError):
        AllowlistEntry.objects.create(domain=domain, entry="10.20.1.5/24", raw="10.20.1.5/24")


def test_13_3_host_bits_are_rejected_by_the_column(domain: Domain) -> None:
    # Главный довод в пользу cidr: инвариант держится типом, а не дисциплиной вызывающего кода.
    with pytest.raises(DataError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO catalog_allowlistentry (domain_id, entry, raw) VALUES (%s, %s, %s)",
            [domain.pk, "10.20.1.5/24", "10.20.1.5/24"],
        )


def test_address_inside_subnet_matches(domain: Domain) -> None:
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    matched = AllowlistEntry.objects.filter(
        domain=domain,
        entry__net_contains_or_equals=ipaddress.ip_address("10.20.3.7"),
    )

    assert [entry.raw for entry in matched] == ["10.20.0.0/22"]


def test_address_outside_subnet_does_not_match(domain: Domain) -> None:
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    matched = AllowlistEntry.objects.filter(
        domain=domain,
        entry__net_contains_or_equals=ipaddress.ip_address("185.44.9.201"),
    )

    assert not matched.exists()


def test_5_ipv4_address_does_not_match_ipv6_entry(domain: Domain) -> None:
    AllowlistEntry.objects.create(domain=domain, entry="2a02:6b8::/32", raw="2a02:6b8::/32")

    matched = AllowlistEntry.objects.filter(
        domain=domain,
        entry__net_contains_or_equals=ipaddress.ip_address("10.20.3.7"),
    )

    assert not matched.exists()


def test_duplicate_entry_in_one_domain_is_rejected(domain: Domain) -> None:
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    with pytest.raises(Exception, match="allowlist_entry_unique"), transaction.atomic():
        AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")


def test_containment_is_one_query_over_gist_index(domain: Domain) -> None:
    """plan.md, этап 2: один запрос с `>>=` и GiST, а не выгрузка списка в Python."""
    AllowlistEntry.objects.bulk_create(
        AllowlistEntry(
            domain=domain,
            entry=f"10.{index // 256}.{index % 256}.0/24",
            raw=f"10.{index // 256}.{index % 256}.0/24",
        )
        for index in range(ENTRY_COUNT)
    )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE catalog_allowlistentry")

    queryset = AllowlistEntry.objects.filter(
        entry__net_contains_or_equals=ipaddress.ip_address("10.1.2.77")
    )
    plan = queryset.explain()

    assert "allowlist_entry_gist" in plan, plan
    assert "Seq Scan" not in plan, plan
