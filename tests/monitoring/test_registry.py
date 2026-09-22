"""Реестр адресов, корзины наблюдений и инциденты — ТЗ 4.4, 4.6, 9.2, 11.3."""

import ipaddress
from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.catalog.models import Domain
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    BucketSize,
    Incident,
)

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name="pay-gw.contoso.ru")


def make_entry(domain: Domain, address: str) -> AddressRegistryEntry:
    return AddressRegistryEntry.objects.create(
        domain=domain, address=address, first_seen_at=MOMENT, last_seen_at=MOMENT
    )


def test_4_4_entry_is_unique_per_domain_and_address(domain: Domain) -> None:
    make_entry(domain, "10.20.1.14")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_entry(domain, "10.20.1.14")


def test_4_4_same_address_belongs_to_different_domains_separately(domain: Domain) -> None:
    other = Domain.objects.create(name="api.contoso.ru")

    make_entry(domain, "10.20.1.14")
    make_entry(other, "10.20.1.14")

    assert AddressRegistryEntry.objects.count() == 2


def test_address_is_stored_as_ip_address(domain: Domain) -> None:
    make_entry(domain, "2a02:6b8::1")

    entry = AddressRegistryEntry.objects.get()

    assert entry.address == ipaddress.ip_address("2a02:6b8::1")


def test_13_2_miss_counter_starts_at_zero(domain: Domain) -> None:
    entry = make_entry(domain, "185.44.9.201")

    assert entry.consecutive_misses == 0


def test_11_3_deleting_a_domain_deletes_its_registry(domain: Domain) -> None:
    make_entry(domain, "10.20.1.14")

    domain.delete()

    assert AddressRegistryEntry.objects.count() == 0


def test_e1_bucket_is_unique_per_size_and_start(domain: Domain) -> None:
    AddressObservationBucket.objects.create(
        domain=domain, address="10.20.1.14", bucket_size=BucketSize.HOUR, bucket_start=MOMENT
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        AddressObservationBucket.objects.create(
            domain=domain, address="10.20.1.14", bucket_size=BucketSize.HOUR, bucket_start=MOMENT
        )


def test_e1_hour_and_day_buckets_coexist(domain: Domain) -> None:
    # Часовые живут 30 суток, суточные бессрочно — это разные строки, а не конкуренция.
    AddressObservationBucket.objects.create(
        domain=domain, address="10.20.1.14", bucket_size=BucketSize.HOUR, bucket_start=MOMENT
    )
    AddressObservationBucket.objects.create(
        domain=domain, address="10.20.1.14", bucket_size=BucketSize.DAY, bucket_start=MOMENT
    )

    assert AddressObservationBucket.objects.count() == 2


def test_7_3_domain_cannot_have_two_open_incidents(domain: Domain) -> None:
    Incident.objects.create(domain=domain, opened_at=MOMENT)

    with pytest.raises(IntegrityError), transaction.atomic():
        Incident.objects.create(domain=domain, opened_at=MOMENT + timedelta(minutes=5))


def test_7_3_closed_incident_does_not_block_the_next_one(domain: Domain) -> None:
    Incident.objects.create(
        domain=domain, opened_at=MOMENT, closed_at=MOMENT + timedelta(minutes=26)
    )

    Incident.objects.create(domain=domain, opened_at=MOMENT + timedelta(hours=3))

    assert Incident.objects.filter(closed_at__isnull=True).count() == 1
