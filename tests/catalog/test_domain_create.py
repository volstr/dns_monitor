"""Добавление домена — ТЗ 10.1, решение Q-E2."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain, Group
from apps.catalog.services.domains import DomainCreated, DomainNotCreated, create_domain

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 25, 14, 38, tzinfo=UTC)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


def add(name: str, **extra: object) -> object:
    return create_domain(
        name=name,
        group_id=extra.get("group_id"),  # type: ignore[arg-type]
        comment=str(extra.get("comment", "")),
        at=MOMENT,
    )


def test_10_1_name_is_lowercased_and_the_trailing_dot_is_dropped() -> None:
    result = add("  PAY-GW.Contoso.RU.  ")

    assert isinstance(result, DomainCreated)
    assert result.name == "pay-gw.contoso.ru"


def test_10_1_non_ascii_name_becomes_punycode() -> None:
    result = add("пример.рф")

    assert isinstance(result, DomainCreated)
    assert result.name == "xn--e1afmkfd.xn--p1ai"
    # 10.1 требует внятности: перевод показывается человеку, а не происходит молча.
    assert result.converted_from == "пример.рф"


def test_10_1_ascii_name_is_not_reported_as_converted() -> None:
    result = add("pay-gw.contoso.ru")

    assert isinstance(result, DomainCreated)
    assert result.converted_from is None


def test_10_1_impossible_conversion_is_explained() -> None:
    result = add("ф" * 80 + ".рф")

    assert isinstance(result, DomainNotCreated)
    assert "punycode" in result.reason


def test_10_1_duplicates_are_refused() -> None:
    add("pay-gw.contoso.ru")

    result = add("PAY-GW.contoso.ru.")

    assert isinstance(result, DomainNotCreated)
    assert "уже есть" in result.reason
    assert Domain.objects.count() == 1


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "обязательно"),
        ("   ", "обязательно"),
        ("два слова.ru", "пробелов"),
        ("localhost", "двух частей"),
    ],
)
def test_10_1_bad_input_is_explained_in_words(name: str, fragment: str) -> None:
    result = add(name)

    assert isinstance(result, DomainNotCreated)
    assert fragment in result.reason


def test_e2_new_domain_is_scheduled_at_once() -> None:
    """Q-E2: один добавленный домен идёт в ближайший тик.

    Домен с пустым `next_check_at` планировщик не заберёт никогда — он молча не проверялся бы.
    """
    result = add("pay-gw.contoso.ru")

    assert isinstance(result, DomainCreated)
    assert Domain.objects.get(pk=result.domain_id).next_check_at == MOMENT


def test_domain_can_be_added_to_a_group() -> None:
    group = Group.objects.create(name="Платёжный контур")

    result = add("pay-gw.contoso.ru", group_id=group.pk)

    assert isinstance(result, DomainCreated)
    assert Domain.objects.get(pk=result.domain_id).group_id == group.pk


def test_3_observer_cannot_add_a_domain(client: Client) -> None:
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    assert client.get(reverse("domain-new")).status_code == 403
    assert client.post(reverse("domain-new"), {"name": "x.contoso.ru"}).status_code == 403


def test_10_1_admin_adds_a_domain_through_the_screen(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(reverse("domain-new"), {"name": "pay-gw.contoso.ru"}, follow=True)

    assert response.status_code == 200
    assert Domain.objects.filter(name="pay-gw.contoso.ru").exists()
    assert "Домен добавлен" in response.content.decode()


def test_10_1_screen_reports_the_punycode_conversion(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(reverse("domain-new"), {"name": "пример.рф"}, follow=True)

    assert "xn--e1afmkfd.xn--p1ai" in response.content.decode()


def test_the_list_offers_the_button_to_an_admin_only(client: Client, admin: Member) -> None:
    client.force_login(admin)
    assert reverse("domain-new") in client.get(reverse("domain-list")).content.decode()

    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)
    assert reverse("domain-new") not in client.get(reverse("domain-list")).content.decode()
