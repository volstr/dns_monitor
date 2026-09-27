"""
Обработчик списка доменов — ТЗ 11.5, 3, критерий 61.

Здесь проверяется путь через HTTP: доступ, выбор шаблона и то, что страница действительно
собирается на реальных данных.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
PASSWORD = "tuman-9-krisa-vody"


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru",
        name="Наблюдатель",
        created_at=MOMENT,
        role=Role.OBSERVER,
        password=PASSWORD,
    )


def test_3_anonymous_visitor_is_sent_to_the_login_screen(client: Client) -> None:
    response = client.get(reverse("domain-list"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_11_5_screen_renders_domains_on_real_data(client: Client, observer: Member) -> None:
    Domain.objects.create(name="pay-gw.contoso.ru")
    client.force_login(observer)

    response = client.get(reverse("domain-list"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "pay-gw.contoso.ru" in body
    # Каркас страницы на месте: это полный экран, а не фрагмент.
    assert "<nav" in body
    # Комментарии шаблонов не утекают в разметку. `{# … #}` не умеет занимать несколько строк:
    # такой блок молча выводится как текст, и заметить это можно только проверкой.
    assert "разметка прототипа" not in body
    assert "Пункты зависят от роли" not in body


def test_11_4_layout_container_is_visible(client: Client, observer: Member) -> None:
    """
    В CSS прототипа `.app{display:none}`, показывает блок только `.app.on`.

    Без модификатора страница приходит целиком и не отображается — пустой экран при полностью
    исправном ответе. Ни один тест на содержимое такого не ловит, поэтому проверка отдельная.
    """
    client.force_login(observer)

    body = client.get(reverse("domain-list")).content.decode()

    assert 'class="app on"' in body


def test_11_5_htmx_request_gets_only_the_rows(client: Client, observer: Member) -> None:
    """Обновление фильтра не перерисовывает страницу: приходят только строки таблицы."""
    Domain.objects.create(name="pay-gw.contoso.ru")
    client.force_login(observer)

    response = client.get(reverse("domain-list"), headers={"HX-Request": "true"})

    body = response.content.decode()
    assert "pay-gw.contoso.ru" in body
    assert "<nav" not in body
    assert "<html" not in body


def test_61_disabled_member_loses_access_at_once(client: Client, observer: Member) -> None:
    """
    Критерий 61: доступ прекращается не позже чем через 30 секунд — здесь немедленно.

    Роль и признак активности читаются из участника, а не из кэша, поэтому отзыв доступа не
    переживает своё отзывание (11.12).
    """
    client.force_login(observer)
    assert client.get(reverse("domain-list")).status_code == 200

    Member.objects.filter(pk=observer.pk).update(is_active=False)

    # Отключённый перестаёт быть участником, поэтому попадает на вход — но не на экран.
    response = client.get(reverse("domain-list"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_11_5_filter_reaches_the_selector(client: Client, observer: Member) -> None:
    Domain.objects.create(name="on.contoso.ru")
    Domain.objects.create(name="off.contoso.ru", watch_enabled=False)
    client.force_login(observer)

    body = client.get(reverse("domain-list"), {"chosen": "disabled"}).content.decode()

    assert "off.contoso.ru" in body
    assert "on.contoso.ru" not in body


def test_broken_filter_value_is_not_an_error(client: Client, observer: Member) -> None:
    """Мусор в параметре — это «Все», а не пятисотая: адрес страницы правят руками."""
    Domain.objects.create(name="pay-gw.contoso.ru")
    client.force_login(observer)

    response = client.get(reverse("domain-list"), {"chosen": "нет-такого"})

    assert response.status_code == 200
    assert "pay-gw.contoso.ru" in response.content.decode()
