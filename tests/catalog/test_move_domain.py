"""
Перенос домена в другую группу — ТЗ 10.4, критерий 28.

«Перенос молча меняет расписание и режим уведомлений» — 10.4 требует, чтобы молчания не было.
Проверяется и сводка до подтверждения, и то, что сам перенос не трогает ничего, что принадлежит
домену, а не группе.
"""

from datetime import UTC, datetime, timedelta

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
from apps.catalog.selectors.move import move_page
from apps.catalog.services.move import DomainMoved, MoveRefused, move_domain
from apps.monitoring.models import AddressRegistryEntry, Incident

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _settings_row() -> GlobalSettings:
    return GlobalSettings.objects.get_or_create()[0]


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def payments() -> Group:
    return Group.objects.create(
        name="Платёжный контур",
        check_period_seconds=5 * 60,
        notification_mode=NotificationMode.NOT_ALLOWED,
    )


@pytest.fixture
def contractors() -> Group:
    return Group.objects.create(
        name="Внешние подрядчики",
        check_period_seconds=15 * 60,
        notification_mode=NotificationMode.ADDRESS_CHANGE,
    )


@pytest.fixture
def domain(payments: Group) -> Domain:
    domain = Domain.objects.create(
        name="pay-gw.contoso.ru",
        group=payments,
        comment="шлюз",
        next_check_at=MOMENT + timedelta(minutes=5),
    )
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    AddressRegistryEntry.objects.create(
        domain=domain, address="10.20.3.7", first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    return domain


def test_28_summary_shows_period_and_mode_before_confirmation(
    domain: Domain, contractors: Group
) -> None:
    """Критерий 28 дословно: сводка изменений — до подтверждения."""
    page = move_page(domain_id=domain.pk, target_id=contractors.pk)
    period, mode = page.rows

    assert (period.label, period.was, period.becomes) == ("Период проверки", "5 мин", "15 мин")
    assert mode.was == "При выходе из разрешённых"
    assert mode.becomes == "При изменении адресов"
    assert period.changed
    assert mode.changed
    # Ничего не изменилось: сводка — это показ.
    domain.refresh_from_db()
    assert domain.group_id != contractors.pk


def test_28_the_screen_shows_the_summary(
    client: Client, admin: Member, domain: Domain, contractors: Group
) -> None:
    client.force_login(admin)

    body = client.get(
        reverse("domain-move", args=[domain.pk]), {"group": contractors.pk}
    ).content.decode()

    assert "Что изменится после переноса" in body
    assert "При выходе из разрешённых" in body
    assert "При изменении адресов" in body


def test_10_4_summary_lists_what_stays(domain: Domain, contractors: Group) -> None:
    """10.4: «явное перечисление того, что не изменится»."""
    page = move_page(domain_id=domain.pk, target_id=contractors.pk)

    assert "Разрешённые адреса, комментарий и реестр" in page.keeps


def test_10_4_unchanged_values_are_still_shown_but_not_marked(
    domain: Domain, payments: Group
) -> None:
    """Строка есть всегда: «не изменится» человек должен видеть, а не выводить из молчания."""
    same = Group.objects.create(
        name="Соседняя",
        check_period_seconds=5 * 60,
        notification_mode=NotificationMode.NOT_ALLOWED,
    )

    page = move_page(domain_id=domain.pk, target_id=same.pk)

    assert len(page.rows) == 2
    assert not page.changes


def test_10_4_open_incident_plus_blind_mode_warns(domain: Domain, contractors: Group) -> None:
    """10.4: отдельное предупреждение, если открытый инцидент перестанет быть слышен."""
    Incident.objects.create(domain=domain, opened_at=MOMENT)

    page = move_page(domain_id=domain.pk, target_id=contractors.pk)

    assert "перестанет присылать уведомление о непрошеном адресе" in page.incident_warning


def test_10_4_no_incident_no_warning(domain: Domain, contractors: Group) -> None:
    page = move_page(domain_id=domain.pk, target_id=contractors.pk)

    assert page.incident_warning == ""


def test_10_4_target_that_watches_the_incident_does_not_warn(domain: Domain) -> None:
    Incident.objects.create(domain=domain, opened_at=MOMENT)
    watchful = Group.objects.create(
        name="Тоже следит", notification_mode=NotificationMode.NOT_ALLOWED
    )

    page = move_page(domain_id=domain.pk, target_id=watchful.pk)

    assert page.incident_warning == ""


def test_10_4_move_changes_the_group_and_nothing_else(
    domain: Domain, contractors: Group
) -> None:
    result = move_domain(domain_id=domain.pk, group_id=contractors.pk, at=MOMENT)

    domain.refresh_from_db()
    assert isinstance(result, DomainMoved)
    assert domain.group_id == contractors.pk
    assert domain.comment == "шлюз"
    assert list(domain.allowlist.values_list("raw", flat=True)) == ["10.20.0.0/22"]
    assert domain.address_registry.count() == 1


def test_10_4_shorter_period_pulls_the_next_check_closer(domain: Domain) -> None:
    """
    Расписание живёт в `next_check_at`: без пересчёта новый период включился бы со второй
    проверки."""
    minute = Group.objects.create(name="Раз в минуту", check_period_seconds=60)

    move_domain(domain_id=domain.pk, group_id=minute.pk, at=MOMENT)

    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT + timedelta(minutes=1)


def test_10_4_longer_period_does_not_postpone_a_scheduled_check(
    domain: Domain, contractors: Group
) -> None:
    move_domain(domain_id=domain.pk, group_id=contractors.pk, at=MOMENT)

    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT + timedelta(minutes=5)


def test_10_4_move_to_no_group_is_a_move(domain: Domain) -> None:
    """«Без группы» — такая же цель переноса: домен берёт всё из общих настроек (раздел 5)."""
    result = move_domain(domain_id=domain.pk, group_id=None, at=MOMENT)

    domain.refresh_from_db()
    assert isinstance(result, DomainMoved)
    assert result.group_name == "Без группы"
    assert domain.group_id is None


def test_10_4_move_into_the_same_group_is_refused(domain: Domain, payments: Group) -> None:
    result = move_domain(domain_id=domain.pk, group_id=payments.pk, at=MOMENT)

    assert isinstance(result, MoveRefused)
    assert "уже в этой группе" in result.reason


def test_10_4_group_is_created_from_the_typed_name(domain: Domain) -> None:
    """10.4: «при отсутствии совпадений предлагается создать группу с введённым названием»."""
    result = move_domain(
        domain_id=domain.pk, group_id=None, new_group_name="Свежая группа", at=MOMENT
    )

    domain.refresh_from_db()
    assert isinstance(result, DomainMoved)
    assert domain.group is not None
    assert domain.group.name == "Свежая группа"
    # Новая группа ничего своего не задаёт: все пять параметров остаются «по умолчанию».
    assert domain.group.check_period_seconds is None
    assert domain.group.notification_mode is None


def test_10_4_a_named_group_that_exists_is_reused(domain: Domain, contractors: Group) -> None:
    move_domain(
        domain_id=domain.pk, group_id=None, new_group_name="Внешние подрядчики", at=MOMENT
    )

    domain.refresh_from_db()
    assert domain.group_id == contractors.pk
    assert Group.objects.filter(name="Внешние подрядчики").count() == 1


def test_10_4_observer_cannot_move(client: Client, domain: Domain, contractors: Group) -> None:
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    response = client.post(
        reverse("domain-move-apply", args=[domain.pk]), {"group": contractors.pk}
    )

    domain.refresh_from_db()
    assert response.status_code == 403
    assert domain.group_id != contractors.pk


def test_10_4_observer_may_look_at_the_summary(
    client: Client, domain: Domain, contractors: Group
) -> None:
    """Сводка ничего не меняет — смотреть её наблюдателю можно (ТЗ 3)."""
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    response = client.get(reverse("domain-move", args=[domain.pk]), {"group": contractors.pk})

    assert response.status_code == 200
