"""
Предупреждения об установке — ТЗ 4.1, 6.1, решение Q-D51.

Установка бывает цела и при этом не делает своего дела. Эти тесты закрепляют, что объяснение
доходит до экрана, а не остаётся в журнале процесса.

Самым грубым случаем был пустой список DNS-серверов. С Q-D51 он перестал быть бедой: пустой
означает «спрашивать у системы». Предупреждать осталось про один случай — когда серверов нет
**ни в настройках, ни у системы**; тогда спрашивать действительно некого.

Системный список приходит параметром, а не берётся из модуля: он читает настоящий
`/etc/resolv.conf`, а тесту нужно уметь сказать «серверов у системы нет». Подмена по пути
импорта в проекте запрещена (`CLAUDE.md`).
"""

from datetime import UTC, datetime

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain, GlobalSettings
from apps.catalog.selectors.domain_card import domain_card
from apps.catalog.selectors.health import InstallWarning, install_warnings

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


def no_system_servers() -> tuple[str, ...]:
    """Система без резолверов: `/etc/resolv.conf` пуст или его нет."""
    return ()


def system_has_one() -> tuple[str, ...]:
    return ("127.0.0.11",)


def test_4_1_no_servers_anywhere_is_reported() -> None:
    warnings = install_warnings(servers_of_system=no_system_servers)

    assert len(warnings) == 1
    assert "ни у системы" in warnings[0].text


def test_d51_empty_list_is_silent_when_the_system_has_servers() -> None:
    """Пустой список — рабочий режим, а не недоделка: спрашиваем у системы."""
    assert install_warnings(servers_of_system=system_has_one) == ()


def test_filled_dns_list_is_silent() -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])

    assert install_warnings(servers_of_system=no_system_servers) == ()


def test_8_3_unconfigured_channel_is_not_a_warning() -> None:
    """Пустой telegram_chat_id — нормальный режим (8.3), а не недоделка."""
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"], telegram_chat_id="")

    assert install_warnings(servers_of_system=no_system_servers) == ()


def test_warning_is_shown_on_every_screen(client: Client, admin: Member) -> None:
    """
    Предупреждение живёт в общем каркасе, поэтому видно на каждом экране.

    Проверяется отрисовкой каркаса напрямую, а не через запрос: предупреждение зависит от
    настоящего `/etc/resolv.conf`, а в контейнере сборки серверы у системы есть. Запрос
    показал бы отсутствие предупреждения — верно, но не то, что здесь нужно проверить.
    """
    warning = InstallWarning(
        text="Ни в настройках, ни у системы нет ни одного DNS-сервера — проверки не идут.",
        action="Заполнить в настройках",
        url_name="settings",
    )

    body = render_to_string(
        "base.html", {"install_warnings": [warning], "is_admin": True, "screen": "overview"}
    )

    assert "Проверки не идут" in body
    assert "Заполнить в настройках" in body


def test_warning_offers_the_fix_only_to_an_admin() -> None:
    """Наблюдателю говорят о причине, но чинить не предлагают: настройки он не меняет (ТЗ 3)."""
    warning = InstallWarning(
        text="Ни в настройках, ни у системы нет ни одного DNS-сервера — проверки не идут.",
        action="Заполнить в настройках",
        url_name="settings",
    )

    body = render_to_string("base.html", {"install_warnings": [warning], "is_admin": False})

    assert "Проверки не идут" in body
    assert "Заполнить в настройках" not in body


def test_warning_disappears_once_the_list_is_filled(client: Client, admin: Member) -> None:
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["10.0.0.1"])
    client.force_login(admin)

    assert "Проверки не идут" not in client.get(reverse("overview")).content.decode()


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
