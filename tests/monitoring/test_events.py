"""События журнала — ТЗ 4.5, 7.1, 11.3, Q-D4, Q-D17."""

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Member
from apps.catalog.models import Domain
from apps.monitoring.models import Event, EventType, Incident

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name='pay-gw.contoso.ru')


def test_d4_event_without_a_domain_is_impossible() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.create(
            domain=None, event_type=EventType.NOT_ALLOWED, occurred_at=MOMENT
        )


def test_4_5_payload_carries_the_affected_addresses(domain: Domain) -> None:
    Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=MOMENT,
        payload={'addresses': ['185.44.9.201']},
    )

    event = Event.objects.get()
    assert event.payload == {'addresses': ['185.44.9.201']}


def test_4_5_payload_defaults_to_empty(domain: Domain) -> None:
    Event.objects.create(
        domain=domain, event_type=EventType.RESOLVE_FAILED, occurred_at=MOMENT
    )

    assert Event.objects.get().payload == {}


def test_4_5_event_may_reference_an_incident(domain: Domain) -> None:
    incident = Incident.objects.create(domain=domain, opened_at=MOMENT)

    Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=MOMENT,
        incident=incident,
    )

    assert Event.objects.get().incident == incident


def test_checker_events_have_no_author(domain: Domain) -> None:
    Event.objects.create(
        domain=domain, event_type=EventType.ADDRESS_APPEARED, occurred_at=MOMENT
    )

    event = Event.objects.get()
    assert event.author is None
    assert event.author_name == ''


def test_d17_deleting_the_author_keeps_the_name_snapshot(domain: Domain) -> None:
    # Прототип показывает «Наблюдение включено · Анна Кирилова» — автор нужен и доменным событиям.
    author = Member.objects.create_member(
        email='a.kirilova@contoso.ru', name='Анна Кирилова', created_at=MOMENT
    )
    Event.objects.create(
        domain=domain,
        event_type=EventType.WATCH_STARTED,
        occurred_at=MOMENT,
        author=author,
        author_name=author.name,
        payload={'address_count': 3},
    )

    author.delete()

    event = Event.objects.get()
    assert event.author is None
    assert event.author_name == 'Анна Кирилова'


def test_11_3_deleting_a_domain_deletes_its_events(domain: Domain) -> None:
    Event.objects.create(
        domain=domain, event_type=EventType.WATCH_STARTED, occurred_at=MOMENT
    )

    domain.delete()

    assert Event.objects.count() == 0
