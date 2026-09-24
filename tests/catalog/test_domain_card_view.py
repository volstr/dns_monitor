"""Обработчик карточки домена — ТЗ 9.1, 3."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain
from apps.monitoring.models import AddressRegistryEntry

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name="pay-gw.contoso.ru", last_successful_check_at=MOMENT)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    AddressRegistryEntry.objects.create(
        domain=domain, address="10.20.1.14", first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    return domain


def test_3_anonymous_visitor_is_sent_to_the_login_screen(client: Client, domain: Domain) -> None:
    response = client.get(reverse("domain-detail", args=[domain.pk]))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_unknown_domain_answers_404(client: Client, observer: Member) -> None:
    client.force_login(observer)

    assert client.get(reverse("domain-detail", args=[10_000])).status_code == 404


def test_9_1_card_renders_on_real_data(client: Client, observer: Member, domain: Domain) -> None:
    client.force_login(observer)

    response = client.get(reverse("domain-detail", args=[domain.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "pay-gw.contoso.ru" in body
    assert "входит в 10.20.0.0/22" in body
    assert "10.20.0.0 — 10.20.3.255 · 1024 адреса" in body
    # Комментарии шаблона не утекают в разметку.
    assert "разметка прототипа" not in body


def test_11_5_domain_name_in_the_list_links_to_its_card(
    client: Client, observer: Member, domain: Domain
) -> None:
    client.force_login(observer)

    body = client.get(reverse("domain-list")).content.decode()

    assert reverse("domain-detail", args=[domain.pk]) in body
