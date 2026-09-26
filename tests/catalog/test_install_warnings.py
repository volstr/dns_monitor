"""Предупреждения об установке — ТЗ 4.1, 6.1.

Установка бывает цела и при этом не делает своего дела. Самый грубый случай — пустой список
DNS-серверов: `run_checker` отказывается работать и объясняет причину в журнале процесса, а
человек за экраном видит пустой реестр и ничего больше. Эти тесты закрепляют, что объяснение
доходит до экрана.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain, GlobalSettings
from apps.catalog.selectors.domain_card import domain_card
from apps.catalog.selectors.health import install_warnings

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )


def test_empty_dns_list_is_reported() -> None:
    warnings = install_warnings()

    assert len(warnings) == 1
    assert "DNS-серверов пуст" in warnings[0].text


def test_filled_dns_list_is_silent() -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])

    assert install_warnings() == ()


def test_8_3_unconfigured_channel_is_not_a_warning() -> None:
    """Пустой telegram_chat_id — нормальный режим (8.3), а не недоделка."""
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"], telegram_chat_id="")

    assert install_warnings() == ()


def test_warning_is_shown_on_every_screen(client: Client, admin: Member) -> None:
    client.force_login(admin)

    for name in ("overview", "domain-list", "group-list", "event-list"):
        body = client.get(reverse(name)).content.decode()
        assert "Проверки не идут" in body, name


def test_warning_offers_the_fix_only_to_an_admin(
    client: Client, admin: Member, observer: Member
) -> None:
    client.force_login(admin)
    assert "Заполнить в настройках" in client.get(reverse("overview")).content.decode()

    client.force_login(observer)
    body = client.get(reverse("overview")).content.decode()
    # Наблюдателю говорят о причине, но чинить её не предлагают: настройки он не меняет (ТЗ 3).
    assert "Проверки не идут" in body
    assert "Заполнить в настройках" not in body


def test_warning_disappears_once_the_list_is_filled(client: Client, admin: Member) -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])
    client.force_login(admin)

    assert "Проверки не идут" not in client.get(reverse("overview")).content.decode()


def test_empty_registry_explains_itself_on_the_card() -> None:
    """Пустая таблица сама по себе ничего не объясняет."""
    domain = Domain.objects.create(name="google.com", next_check_at=MOMENT)

    card = domain_card(domain_id=domain.pk, at=MOMENT)

    assert card is not None
    assert "список DNS-серверов" in card.empty_reason


def test_card_explains_a_paused_domain() -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])
    domain = Domain.objects.create(name="google.com", watch_enabled=False)

    card = domain_card(domain_id=domain.pk, at=MOMENT)

    assert card is not None
    assert "приостановлено" in card.empty_reason


def test_card_explains_a_domain_without_a_schedule() -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])
    domain = Domain.objects.create(name="google.com", next_check_at=None)

    card = domain_card(domain_id=domain.pk, at=MOMENT)

    assert card is not None
    assert "Проверить сейчас" in card.empty_reason
