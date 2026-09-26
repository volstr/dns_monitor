"""Кнопки «Разрешить адрес» и «Добавить подсеть» — Q-C15, ТЗ 6.3, 10.2.

Кнопка **дописывает строку в поле и не сохраняет**: дальше идёт обычный путь — построчная
проверка и «Сохранить». Так человек видит, что именно добавится, и успевает поправить длину
префикса, которую ТЗ не задаёт.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain
from apps.catalog.services.allowlist import suggested_line, with_suggestion
from apps.monitoring.models import AddressRegistryEntry

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)
OUTSIDER = "185.44.9.201"


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name="pay-gw.contoso.ru", last_successful_check_at=MOMENT)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    AddressRegistryEntry.objects.create(
        domain=domain, address=OUTSIDER, first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    return domain


def test_c15_allow_address_offers_the_address_itself() -> None:
    """6.3: запись без маски сравнивается точно — это ровно то, о чём просят."""
    assert suggested_line(OUTSIDER, as_network=False) == OUTSIDER


def test_c15_add_subnet_offers_the_containing_network() -> None:
    """Подставить «185.44.9.201/24» значило бы сразу внести ошибку ввода."""
    assert suggested_line(OUTSIDER, as_network=True) == "185.44.9.0/24"


def test_c15_ipv6_subnet_uses_its_own_prefix() -> None:
    assert suggested_line("2a00:1450:400e:809::200e", as_network=True) == "2a00:1450:400e:809::/64"


def test_c15_repeated_press_does_not_duplicate_the_line() -> None:
    once = with_suggestion("10.20.0.0/22", OUTSIDER)

    assert with_suggestion(once, OUTSIDER) == once


def test_c15_button_fills_the_field_without_saving(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = client.get(
        reverse("domain-detail", args=[domain.pk]), {"allow": OUTSIDER}
    ).content.decode()

    # Строка появилась в поле вместе с прежними записями.
    assert "10.20.0.0/22\n185.44.9.201" in body
    assert "ещё не сохранена" in body
    # И действительно не сохранена: список в базе прежний.
    assert list(AllowlistEntry.objects.values_list("raw", flat=True)) == ["10.20.0.0/22"]


def test_c15_subnet_button_fills_the_network(client: Client, admin: Member, domain: Domain) -> None:
    client.force_login(admin)

    body = client.get(
        reverse("domain-detail", args=[domain.pk]), {"allow_net": OUTSIDER}
    ).content.decode()

    assert "185.44.9.0/24" in body
    assert AllowlistEntry.objects.count() == 1


def test_c15_buttons_are_shown_next_to_an_unmatched_address(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()

    assert "Разрешить адрес" in body
    assert "Добавить подсеть" in body


def test_c15_observer_does_not_get_the_buttons(client: Client, domain: Domain) -> None:
    """Наблюдатель список не меняет (ТЗ 3), поэтому и предлагать ему нечего."""
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()

    assert "Разрешить адрес" not in body


def test_c15_garbage_in_the_parameter_is_ignored(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Адрес страницы правят руками: мусор не должен ронять карточку."""
    client.force_login(admin)

    response = client.get(reverse("domain-detail", args=[domain.pk]), {"allow": "не-адрес"})

    assert response.status_code == 200
    assert "ещё не сохранена" not in response.content.decode()
