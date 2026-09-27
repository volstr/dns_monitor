"""Кнопки «Разрешить адрес» и «Добавить подсеть» — Q-C15, ТЗ 6.3, 10.2.

Кнопка **дописывает строку в поле и не сохраняет**: дальше идёт обычный путь — построчная
проверка и «Сохранить». Так человек видит, что именно добавится, и успевает поправить длину
префикса, которую ТЗ не задаёт.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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
    """Адрес должен быть **актуальным**, иначе его статус — «не актуален», а не «не решён».

    Отметки времени здесь привязаны к настоящему моменту, а не к фиксированному `MOMENT`, и это
    не небрежность. Тест идёт через обработчик, а тот берёт время у `SystemClock` — свежесть
    считается от реальных часов. С константой тест жил ровно сутки: 2026-09-27 в 14:38 адрес
    пересёк порог актуальности, статус сменился на `STALE`, и кнопки честно исчезли.
    """
    seen = timezone.now() - timedelta(minutes=5)
    domain = Domain.objects.create(name="pay-gw.contoso.ru", last_successful_check_at=seen)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    AddressRegistryEntry.objects.create(
        domain=domain, address=OUTSIDER, first_seen_at=seen, last_seen_at=seen
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


def test_d38_an_entry_nothing_ever_matched_is_highlighted(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Q-D38: под запись не пришло ни одного адреса, хотя домен отвечал.

    Это подозрительнее устаревания: там адреса были и перестали приходить, а тут их не было ни
    разу — чаще всего опечатка в подсети или запись, скопированная с чужого домена. Раньше
    строка была самой бледной на экране.
    """
    AllowlistEntry.objects.create(domain=domain, entry="192.0.2.0/24", raw="192.0.2.0/24")
    client.force_login(admin)

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()

    assert '<span class="state change" title="Домен отвечал' in body
    assert "не опечатка ли в ней" in body


def test_d38_an_unchecked_domain_is_not_accused(client: Client, admin: Member) -> None:
    """У домена, который ещё ни разу не ответил, попадать было нечему.

    Подсвечивать тут нечего: запись не виновата, что проверок не было. Иначе каждый только что
    добавленный домен встречал бы человека жёлтой строкой.
    """
    fresh = Domain.objects.create(name="new.contoso.ru")
    AllowlistEntry.objects.create(domain=fresh, entry="192.0.2.0/24", raw="192.0.2.0/24")
    client.force_login(admin)

    body = client.get(reverse("domain-detail", args=[fresh.pk])).content.decode()

    assert '<span class="sub">пусто</span>' in body
    assert "не опечатка ли в ней" not in body


def test_d38_an_entry_with_addresses_stays_green(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Обратная сторона: запись, которая делает свою работу, остаётся зелёной."""
    seen = timezone.now() - timedelta(minutes=5)
    AddressRegistryEntry.objects.create(
        domain=domain, address="10.20.1.14", first_seen_at=seen, last_seen_at=seen
    )
    client.force_login(admin)

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()

    assert '<span class="state ok">1 внутри</span>' in body
