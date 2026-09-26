"""Сообщение о результате сохранения — ТЗ 10.2.

Причина ошибки видна у самой строки, но факт «не сохранено» оттуда не читается: человек видит
форму и не понимает, применилось ли. Успех сообщают сверху — отказ обязан сообщаться там же.
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
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def test_10_2_failed_save_says_so_at_the_top(client: Client, admin: Member, domain: Domain) -> None:
    client.force_login(admin)

    body = client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": "10.20.999.0/24", "record_types": ["a"]},
    ).content.decode()

    assert "Не сохранено" in body
    # Сообщение стоит в полосе сообщений, а не только у строки списка.
    assert '<div class="msgs">' in body
    # Причина у самой строки при этом никуда не делась (10.2).
    assert "999 — недопустимый октет" in body


def test_10_2_message_counts_the_broken_lines(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": "10.20.999.0/24\n10.20.0.0/33", "record_types": ["a"]},
    ).content.decode()

    assert "2 ошибочные строки" in body


def test_4_3_form_level_refusal_is_reported_too(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Отказ не из-за списка — снятые типы записей — сообщается так же."""
    client.force_login(admin)

    body = client.post(
        reverse("domain-save", args=[domain.pk]), {"allowlist": "10.20.0.0/22"}
    ).content.decode()

    assert "Не сохранено" in body
    assert "хотя бы один тип записи" in body


def test_10_2_failed_add_says_so_at_the_top(client: Client, admin: Member) -> None:
    client.force_login(admin)

    body = client.post(
        reverse("domain-new"),
        {"names": "new.contoso.ru", "allowlist": "10.20.999.0/24"},
    ).content.decode()

    assert "Домены не добавлены" in body
    assert Domain.objects.count() == 0


def test_successful_save_still_reports_success(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    body = client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": "10.30.4.0/24", "record_types": ["a"]},
        follow=True,
    ).content.decode()

    assert "Сохранено." in body
    assert "Не сохранено" not in body


def test_messages_live_in_the_message_strip(client: Client, admin: Member, domain: Domain) -> None:
    """Плашка стоит в полосе сообщений, а не сама по себе.

    Что полоса действительно совпадает по отступам с содержимым страницы, проверяется в
    браузере: из разметки этого не видно.
    """
    client.force_login(admin)

    body = client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": "10.30.4.0/24", "record_types": ["a"]},
        follow=True,
    ).content.decode()

    assert body.index('<div class="msgs">') < body.index('class="ok"')
