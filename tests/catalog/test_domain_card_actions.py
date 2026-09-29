"""Фильтры карточки, приостановка и удаление домена — ТЗ 9.1, 11.1, 7.1, макет карточки."""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings
from apps.catalog.selectors.domain_card import HistoryFilter, RegistryFilter, domain_card
from apps.catalog.services.domains import DomainDeleted, WatchSwitched, delete_domain, switch_watch
from apps.monitoring.models import AddressRegistryEntry, Check, Event, EventType, Incident
from core.types.checking import CheckOutcome

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name="pay-gw.contoso.ru", last_successful_check_at=MOMENT)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    for address, last in (
        ("10.20.1.14", timedelta(minutes=1)),
        ("185.44.9.201", timedelta(minutes=1)),
        ("10.20.9.9", timedelta(days=10)),
    ):
        AddressRegistryEntry.objects.create(
            domain=domain,
            address=address,
            first_seen_at=MOMENT - timedelta(days=30),
            last_seen_at=MOMENT - last,
        )
    return domain


def card(domain: Domain, **extra: object) -> object:
    result = domain_card(domain_id=domain.pk, at=MOMENT, **extra)  # type: ignore[arg-type]
    assert result is not None
    return result


@pytest.fixture(autouse=True)
def _threshold_of_one_day() -> None:
    """
    Порог актуальности прибит к суткам, а не взят из умолчания.

    Умолчание — 14 суток (Q-D47). Тест, который строит неактуальный адрес или показывает
    порог на экране, обязан называть своё значение сам: иначе он молча меняет смысл вместе с
    настройкой продукта, а падает совсем в другом месте.
    """
    GlobalSettings.objects.filter(pk=1).update(freshness_threshold_seconds=24 * 60 * 60)


def test_9_1_registry_segments_narrow_the_list(domain: Domain) -> None:
    assert [line.address for line in card(domain).registry] == [
        "185.44.9.201",
        "10.20.1.14",
        "10.20.9.9",
    ]
    assert [
        line.address for line in card(domain, registry=RegistryFilter.UNMATCHED).registry
    ] == ["185.44.9.201"]
    assert [
        line.address for line in card(domain, registry=RegistryFilter.MATCHED).registry
    ] == ["10.20.1.14"]
    assert [line.address for line in card(domain, registry=RegistryFilter.STALE).registry] == [
        "10.20.9.9"
    ]


def test_9_1_summary_is_counted_over_the_whole_registry(domain: Domain) -> None:
    """«1 из 2 адресов не решён» обязано означать одно и то же при любом выбранном сегменте."""
    everything = card(domain)
    narrowed = card(domain, registry=RegistryFilter.STALE)

    assert narrowed.summary_label == everything.summary_label
    assert [line.address for line in narrowed.unmatched] == ["185.44.9.201"]


def test_7_1_history_segments_narrow_the_list(domain: Domain) -> None:
    Event.objects.create(domain=domain, event_type=EventType.NOT_ALLOWED, occurred_at=MOMENT)
    Event.objects.create(
        domain=domain,
        event_type=EventType.ADDRESS_APPEARED,
        occurred_at=MOMENT - timedelta(hours=1),
    )
    Event.objects.create(
        domain=domain,
        event_type=EventType.RESOLVE_FAILED,
        occurred_at=MOMENT - timedelta(hours=2),
    )

    assert len(card(domain).events) == 3
    assert [event.label for event in card(domain, history=HistoryFilter.CHANGES).events] == [
        "Появился адрес"
    ]
    assert [event.label for event in card(domain, history=HistoryFilter.NOT_ALLOWED).events] == [
        "Адрес вне разрешённых"
    ]
    assert [
        event.label for event in card(domain, history=HistoryFilter.RESOLVE_ERRORS).events
    ] == ["Домен не резолвится"]


def test_11_1_pausing_stops_the_domain_from_being_claimed(domain: Domain) -> None:
    result = switch_watch(domain_id=domain.pk, enabled=False, at=MOMENT)

    assert isinstance(result, WatchSwitched)
    domain.refresh_from_db()
    assert domain.watch_enabled is False
    # Реестр адресов при этом сохраняется (11.1).
    assert AddressRegistryEntry.objects.filter(domain=domain).count() == 3


def test_11_1_resuming_schedules_a_check_when_there_was_none(domain: Domain) -> None:
    """Домен с пустым next_check_at планировщик не заберёт — возобновление было бы на словах."""
    Domain.objects.filter(pk=domain.pk).update(watch_enabled=False, next_check_at=None)

    switch_watch(domain_id=domain.pk, enabled=True, at=MOMENT)

    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT


def test_11_1_resuming_keeps_an_existing_schedule(domain: Domain) -> None:
    later = MOMENT + timedelta(minutes=5)
    Domain.objects.filter(pk=domain.pk).update(watch_enabled=False, next_check_at=later)

    switch_watch(domain_id=domain.pk, enabled=True, at=MOMENT)

    domain.refresh_from_db()
    assert domain.next_check_at == later


def test_deleting_a_domain_takes_its_history_and_says_how_much(domain: Domain) -> None:
    Event.objects.create(domain=domain, event_type=EventType.NOT_ALLOWED, occurred_at=MOMENT)
    Check.objects.create(domain=domain, checked_at=MOMENT, outcome=CheckOutcome.NORMAL)
    Incident.objects.create(domain=domain, opened_at=MOMENT)

    result = delete_domain(domain_id=domain.pk)

    assert isinstance(result, DomainDeleted)
    assert (result.name, result.events) == ("pay-gw.contoso.ru", 1)
    assert Domain.objects.count() == 0
    assert Event.objects.count() == 0
    assert AddressRegistryEntry.objects.count() == 0


def test_3_observer_cannot_pause_or_delete(client: Client, domain: Domain) -> None:
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    assert client.post(reverse("domain-watch", args=[domain.pk])).status_code == 403
    assert client.get(reverse("domain-delete", args=[domain.pk])).status_code == 403
    assert client.post(reverse("domain-delete", args=[domain.pk])).status_code == 403
    assert Domain.objects.count() == 1


def test_deletion_asks_before_doing(client: Client, admin: Member, domain: Domain) -> None:
    """Необратимое действие не делается по одному нажатию: сначала называется цена."""
    client.force_login(admin)

    body = client.get(reverse("domain-delete", args=[domain.pk])).content.decode()

    assert "Это необратимо" in body
    assert "Приостановить вместо удаления" in body
    assert Domain.objects.count() == 1


def test_admin_pauses_and_resumes_from_the_card(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    client.post(reverse("domain-watch", args=[domain.pk]), {"enabled": "no"})
    domain.refresh_from_db()
    assert domain.watch_enabled is False

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()
    assert "Возобновить" in body

    client.post(reverse("domain-watch", args=[domain.pk]), {"enabled": "yes"})
    domain.refresh_from_db()
    assert domain.watch_enabled is True


def test_card_segments_survive_each_other(client: Client, admin: Member, domain: Domain) -> None:
    """Выбор сегмента реестра не сбрасывает фильтр истории: оба живут в адресе страницы."""
    client.force_login(admin)

    body = client.get(
        reverse("domain-detail", args=[domain.pk]), {"registry": "stale", "history": "changes"}
    ).content.decode()

    assert "?registry=stale&history=all" in body
    assert "?registry=all&history=changes" in body
