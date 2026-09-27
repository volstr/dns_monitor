"""Добавление домена — ТЗ 10.1, решение Q-E2."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import (
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.catalog.services.domains import (
    DomainCreated,
    DomainNotCreated,
    DomainsAdded,
    DomainsNotAdded,
    add_domains,
    create_domain,
)

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
    """
    Q-E2: один добавленный домен идёт в ближайший тик.

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


def add_many(names: str, **extra: object) -> object:
    return add_domains(
        names=names,
        group_id=extra.get("group_id"),  # type: ignore[arg-type]
        comment=str(extra.get("comment", "")),
        allowlist_text=str(extra.get("allowlist_text", "")),
        at=MOMENT,
    )


def test_10_1_several_names_are_added_at_once() -> None:
    """Макет: «По одному в строке — можно добавить сразу несколько»."""
    result = add_many("pay-gw.contoso.ru\napi.contoso.ru\n\nПРИМЕР.РФ")

    assert isinstance(result, DomainsAdded)
    assert [line.name for line in result.created] == [
        "pay-gw.contoso.ru",
        "api.contoso.ru",
        "xn--e1afmkfd.xn--p1ai",
    ]


def test_10_1_one_bad_line_does_not_cancel_the_others() -> None:
    """Иначе одна опечатка в списке из двадцати имён заставляла бы набирать всё заново."""
    result = add_many("pay-gw.contoso.ru\nlocalhost\napi.contoso.ru")

    assert isinstance(result, DomainsAdded)
    assert [line.name for line in result.created] == ["pay-gw.contoso.ru", "api.contoso.ru"]
    assert [line.text for line in result.refused] == ["localhost"]


def test_10_2_allowlist_is_applied_to_every_added_domain() -> None:
    result = add_many(
        "pay-gw.contoso.ru\napi.contoso.ru", allowlist_text="10.20.0.0/22\n10.20.3.7"
    )

    assert isinstance(result, DomainsAdded)
    for name in ("pay-gw.contoso.ru", "api.contoso.ru"):
        entries = AllowlistEntry.objects.filter(domain__name=name).values_list("raw", flat=True)
        assert set(entries) == {"10.20.0.0/22", "10.20.3.7"}


def test_10_2_broken_allowlist_adds_nothing() -> None:
    """10.2: пока в списке есть ошибочная строка, сохранять нельзя — и домены не создаются."""
    result = add_many("pay-gw.contoso.ru", allowlist_text="10.20.999.0/24")

    assert isinstance(result, DomainsNotAdded)
    assert result.allowlist.errors[0].error is not None
    assert Domain.objects.count() == 0


def test_10_2_empty_allowlist_is_allowed() -> None:
    """Макет: «Необязательно — можно заполнить позже»."""
    result = add_many("pay-gw.contoso.ru")

    assert isinstance(result, DomainsAdded)
    assert AllowlistEntry.objects.count() == 0


def test_8_2_empty_allowlist_in_the_trap_mode_warns() -> None:
    """ТЗ 8.2: при сохранении домена с пустым списком в таком режиме показывается предупреждение."""
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NOT_ALLOWED)

    result = add_many("pay-gw.contoso.ru")

    assert isinstance(result, DomainsAdded)
    assert "уведомлений не будет никогда" in result.trap_warning


def test_8_2_no_warning_when_the_list_is_filled() -> None:
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NOT_ALLOWED)

    result = add_many("pay-gw.contoso.ru", allowlist_text="10.20.0.0/22")

    assert isinstance(result, DomainsAdded)
    assert result.trap_warning == ""


def test_8_2_no_warning_in_the_change_mode() -> None:
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.ADDRESS_CHANGE)

    result = add_many("pay-gw.contoso.ru")

    assert isinstance(result, DomainsAdded)
    assert result.trap_warning == ""


def test_3_observer_cannot_add_a_domain(client: Client) -> None:
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    assert client.get(reverse("domain-new")).status_code == 403
    assert client.post(reverse("domain-new"), {"names": "x.contoso.ru"}).status_code == 403


def test_10_1_admin_adds_a_domain_through_the_screen(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(reverse("domain-new"), {"names": "pay-gw.contoso.ru"}, follow=True)

    assert response.status_code == 200
    assert Domain.objects.filter(name="pay-gw.contoso.ru").exists()
    assert "Добавлено доменов: 1" in response.content.decode()


def test_10_2_screen_keeps_the_input_when_the_allowlist_is_broken(
    client: Client, admin: Member
) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("domain-new"),
        {"names": "pay-gw.contoso.ru", "allowlist": "10.20.999.0/24"},
    )

    body = response.content.decode()
    assert "999 — недопустимый октет" in body
    # Введённое не потеряно: человек правит свой текст, а не набирает заново.
    assert "pay-gw.contoso.ru" in body
    assert Domain.objects.count() == 0


def test_10_1_screen_reports_the_punycode_conversion(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(reverse("domain-new"), {"names": "пример.рф"}, follow=True)

    assert "xn--e1afmkfd.xn--p1ai" in response.content.decode()


def test_the_list_offers_the_button_to_an_admin_only(client: Client, admin: Member) -> None:
    client.force_login(admin)
    assert reverse("domain-new") in client.get(reverse("domain-list")).content.decode()

    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)
    assert reverse("domain-new") not in client.get(reverse("domain-list")).content.decode()
