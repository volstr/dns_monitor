"""
Перекрытие записей белого списка — ТЗ 10.2, критерий 45.

10.2 относит перекрытие к предупреждениям, которые **не блокируют** сохранение, и критерий 45
проверяет это дословно. Но предупреждение обязано дойти до человека: у строки оно живёт только
до отправки формы, а после сохранения страница перерисовывается из базы.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name="github.com")


def save(client: Client, domain: Domain, allowlist: str) -> str:
    return client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": allowlist, "record_types": ["a"]},
        follow=True,
    ).content.decode()


def test_45_address_inside_a_subnet_warns_and_still_saves(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Критерий 45 дословно: предупреждение о перекрытии, сохранение разрешено."""
    client.force_login(admin)

    body = save(client, domain, "140.82.121.0/24\n140.82.121.3")

    assert "Сохранено." in body
    assert AllowlistEntry.objects.filter(domain=domain).count() == 2


def test_10_2_overlap_warning_survives_the_save(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Раньше предупреждение исчезало вместе с перерисовкой страницы."""
    client.force_login(admin)

    body = save(client, domain, "140.82.121.0/24\n140.82.121.3")

    assert "140.82.121.3 уже входит в 140.82.121.0/24 из строки 1" in body
    assert "скорее всего, лишняя" in body


def test_10_2_subnet_inside_a_wider_subnet_warns_too(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Правило то же и для сетей: 140.82.121.0/24 целиком внутри 140.82.0.0/16."""
    client.force_login(admin)

    body = save(client, domain, "140.82.0.0/16\n140.82.121.0/24")

    assert "140.82.121.0/24 уже входит в 140.82.0.0/16 из строки 1" in body
    assert AllowlistEntry.objects.filter(domain=domain).count() == 2


def test_10_2_duplicate_warning_survives_the_save(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = save(client, domain, "140.82.0.0/16\n140.82.0.0/16")

    assert "дубликат строки 1" in body
    # Дубликат в базу не попадает, но сказать о нём нужно.
    assert AllowlistEntry.objects.filter(domain=domain).count() == 1


def test_10_2_clean_list_saves_without_warnings(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = save(client, domain, "140.82.0.0/16\n10.20.0.0/22")

    assert "Сохранено." in body
    assert "лишняя" not in body
