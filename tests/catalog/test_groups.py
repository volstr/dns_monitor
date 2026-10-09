"""Группы — ТЗ 5, 7.2, 11.3, 11.6; решения Q-13.1, Q-C1; критерий 47."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from apps.catalog.selectors.groups import group_edit_page, group_list_page
from apps.catalog.services.groups import GroupNotSaved, GroupSaved, save_group
from apps.catalog.services.intervals import freshness_warning, warnings_after_global_change
from apps.monitoring.models import Incident

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
HOUR = 60 * 60


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email='boss@contoso.ru', name='Анна Кирилова', created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email='watch@contoso.ru', name='Наблюдатель', created_at=MOMENT, role=Role.OBSERVER
    )


def save(**extra: object) -> object:
    fields: dict[str, object] = {
        'group_id': None,
        'name': 'Платёжный контур',
        'comment': '',
        'check_period_seconds': None,
        'freshness_threshold_seconds': None,
        'notification_mode': None,
        'notify_back_to_normal': None,
        'reminder_interval_seconds': None,
    }
    fields.update(extra)
    return save_group(**fields)  # type: ignore[arg-type]


def test_5_group_without_own_values_inherits_everything() -> None:
    result = save()

    assert isinstance(result, GroupSaved)
    group = Group.objects.get(pk=result.group_id)
    assert group.check_period_seconds is None
    assert group.notification_mode == ''


def test_8_5_zero_reminder_is_not_the_same_as_default() -> None:
    """Ноль — «не напоминать» (8.5), NULL — «по умолчанию». Сливать нельзя."""
    result = save(reminder_interval_seconds=0)

    assert isinstance(result, GroupSaved)
    assert Group.objects.get(pk=result.group_id).reminder_interval_seconds == 0

    page = group_edit_page(result.group_id)
    assert page is not None
    chosen = [choice for choice in page.reminders if choice.selected]
    assert [choice.label for choice in chosen] == ['не напоминать']


def test_group_needs_a_name() -> None:
    result = save(name='   ')

    assert isinstance(result, GroupNotSaved)
    assert result.reasons == ('У группы должно быть название.',)


def test_group_name_is_unique() -> None:
    save()

    result = save()

    assert isinstance(result, GroupNotSaved)
    assert 'уже есть' in result.reasons[0]


def test_11_1_period_below_a_minute_is_refused() -> None:
    result = save(check_period_seconds=30)

    assert isinstance(result, GroupNotSaved)
    assert result.reasons == ('Период проверки не может быть меньше минуты (ТЗ 11.1).',)


def test_13_1_threshold_not_greater_than_period_warns_hard() -> None:
    """Q-13.1: предупреждение, а не запрет. Сохранение проходит."""
    result = save(check_period_seconds=6 * HOUR, freshness_threshold_seconds=HOUR)

    assert isinstance(result, GroupSaved)
    assert 'не больше периода проверки' in result.warnings[0]


def test_13_1_threshold_close_to_period_warns_softly() -> None:
    result = save(check_period_seconds=HOUR, freshness_threshold_seconds=2 * HOUR)

    assert isinstance(result, GroupSaved)
    assert 'близок к периоду проверки' in result.warnings[0]


def test_13_1_comfortable_ratio_is_silent() -> None:
    result = save(check_period_seconds=HOUR, freshness_threshold_seconds=24 * HOUR)

    assert isinstance(result, GroupSaved)
    assert result.warnings == ()


def test_13_1_warning_is_computed_on_effective_values() -> None:
    """Группа наследует период, но задала свой порог — сочетание всё равно проверяется."""
    GlobalSettings.objects.filter(pk=1).update(check_period_seconds=6 * HOUR)

    result = save(check_period_seconds=None, freshness_threshold_seconds=HOUR)

    assert isinstance(result, GroupSaved)
    assert 'не больше периода проверки' in result.warnings[0]


def test_13_1_changing_global_period_names_the_groups_it_breaks() -> None:
    """Решение Q-13.1: сломавшиеся группы называются поимённо."""
    Group.objects.create(name='Платёжный контур', freshness_threshold_seconds=HOUR)
    Group.objects.create(name='Спокойные', freshness_threshold_seconds=48 * HOUR)
    GlobalSettings.objects.filter(pk=1).update(check_period_seconds=6 * HOUR)

    warnings = warnings_after_global_change(GlobalSettings.objects.get())

    assert any('«Платёжный контур»' in warning for warning in warnings)
    assert not any('«Спокойные»' in warning for warning in warnings)


def test_13_1_warning_text_names_both_numbers() -> None:
    warning = freshness_warning(period_seconds=6 * HOUR, threshold_seconds=HOUR)

    assert '6 ч 0 мин' in warning
    assert '1 ч 0 мин' in warning


def test_11_3_list_counts_domains_and_open_incidents() -> None:
    group = Group.objects.create(name='Платёжный контур')
    broken = Domain.objects.create(name='pay-gw.contoso.ru', group=group)
    Domain.objects.create(name='api.contoso.ru', group=group)
    Incident.objects.create(domain=broken, opened_at=MOMENT)
    Domain.objects.create(name='lonely.contoso.ru')

    page = group_list_page()

    assert (page.cards[0].domain_count, page.cards[0].unresolved) == (2, 1)
    assert page.ungrouped.domain_count == 1


def test_5_list_shows_whether_a_value_is_the_groups_own() -> None:
    Group.objects.create(name='Своё', check_period_seconds=15 * 60)
    Group.objects.create(name='Унаследованное')

    cards = {card.name: card for card in group_list_page().cards}

    assert (cards['Своё'].period, cards['Своё'].period_is_own) == ('15 мин', True)
    assert cards['Унаследованное'].period_is_own is False


def test_new_group_page_has_no_id_and_all_defaults() -> None:
    page = group_edit_page(None)

    assert page is not None
    assert page.id is None
    assert [choice.label for choice in page.periods if choice.selected] == ['По умолчанию']


def test_missing_group_is_not_an_error() -> None:
    assert group_edit_page(10_000) is None


def test_edit_page_keeps_a_custom_value_visible() -> None:
    """Своё значение вне списка не должно исчезать из выбора."""
    group = Group.objects.create(name='Странная', check_period_seconds=7 * 60)

    page = group_edit_page(group.pk)

    assert page is not None
    assert [choice.label for choice in page.periods if choice.selected] == ['7 мин']


def test_3_observer_cannot_open_group_settings(client: Client, observer: Member) -> None:
    group = Group.objects.create(name='Платёжный контур')
    client.force_login(observer)

    assert client.get(reverse('group-edit', args=[group.pk])).status_code == 403


def test_3_observer_sees_the_group_list(client: Client, observer: Member) -> None:
    Group.objects.create(name='Платёжный контур')
    client.force_login(observer)

    response = client.get(reverse('group-list'))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'Платёжный контур' in body
    # Q-C1: складывания белых списков в интерфейсе нет — по ТЗ 5 его не существует.
    assert 'плюс список группы' not in body


def test_5_admin_saves_a_group_through_the_form(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(
        reverse('group-create'),
        {
            'name': 'Платёжный контур',
            'comment': 'Эквайринг',
            'check_period': str(5 * 60),
            'notification_mode': NotificationMode.NOT_ALLOWED.value,
            'notify_back_to_normal': 'yes',
        },
    )

    assert response.status_code == 302
    group = Group.objects.get(name='Платёжный контур')
    assert group.check_period_seconds == 5 * 60
    assert group.notify_back_to_normal is True


def test_custom_minutes_beat_the_chosen_preset(client: Client, admin: Member) -> None:
    client.force_login(admin)

    client.post(
        reverse('group-create'),
        {'name': 'Своя', 'check_period': str(5 * 60), 'check_period_minutes': '7'},
    )

    assert Group.objects.get(name='Своя').check_period_seconds == 7 * 60


def test_47_second_group_check_hits_the_cooldown(client: Client, admin: Member) -> None:
    group = Group.objects.create(name='Платёжный контур')
    Domain.objects.create(name='pay-gw.contoso.ru', group=group)
    client.force_login(admin)
    url = reverse('group-check', args=[group.pk])

    client.post(url, {'confirmed': 'yes'})
    response = client.post(url, {'confirmed': 'yes'}, follow=True)

    assert 'проверяли только что' in response.content.decode()


def test_11_3_group_domains_screen_shows_only_that_group(client: Client, admin: Member) -> None:
    group = Group.objects.create(name='Платёжный контур')
    Domain.objects.create(name='pay-gw.contoso.ru', group=group)
    Domain.objects.create(name='other.contoso.ru')
    client.force_login(admin)

    body = client.get(reverse('group-domains', args=[group.pk])).content.decode()

    assert 'pay-gw.contoso.ru' in body
    assert 'other.contoso.ru' not in body
