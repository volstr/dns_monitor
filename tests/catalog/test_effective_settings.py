"""Наследование настроек — ТЗ 5, критерии приёмки 21–24."""

from datetime import timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group, NotificationMode
from apps.catalog.selectors.settings import effective_settings
from core.types.settings import SettingOrigin

pytestmark = pytest.mark.django_db


def make_domain(name: str, group: Group | None = None) -> Domain:
    return Domain.objects.create(name=name, group=group)


def test_21_group_with_default_period_takes_the_global_one() -> None:
    settings = GlobalSettings.objects.get()
    settings.check_period_seconds = 15 * 60
    settings.save()
    group = Group.objects.create(name="Продакшн-периметр", check_period_seconds=None)
    domain = make_domain("api.contoso.ru", group)

    resolved = effective_settings(domain.pk)

    assert resolved.check_period.value == timedelta(minutes=15)
    assert resolved.check_period.origin is SettingOrigin.GLOBAL


def test_22_changing_the_global_period_moves_only_inheriting_groups() -> None:
    inheriting = Group.objects.create(name="Продакшн-периметр", check_period_seconds=None)
    overriding = Group.objects.create(name="Внешние подрядчики", check_period_seconds=15 * 60)
    inheriting_domain = make_domain("api.contoso.ru", inheriting)
    overriding_domain = make_domain("cdn.contoso.ru", overriding)

    settings = GlobalSettings.objects.get()
    settings.check_period_seconds = 60 * 60
    settings.save()

    inherited = effective_settings(inheriting_domain.pk).check_period
    assert inherited.value == timedelta(hours=1)
    assert inherited.origin is SettingOrigin.GLOBAL

    overridden = effective_settings(overriding_domain.pk).check_period
    assert overridden.value == timedelta(minutes=15)
    assert overridden.origin is SettingOrigin.GROUP


def test_23_domain_without_group_takes_everything_from_global_settings() -> None:
    settings = GlobalSettings.objects.get()
    settings.check_period_seconds = 60
    settings.freshness_threshold_seconds = 7 * 24 * 60 * 60
    settings.notification_mode = NotificationMode.ADDRESS_CHANGE
    settings.notify_back_to_normal = False
    settings.reminder_interval_seconds = 60 * 60
    settings.save()
    domain = make_domain("status.contoso.ru", group=None)

    resolved = effective_settings(domain.pk)

    assert resolved.check_period.value == timedelta(minutes=1)
    assert resolved.freshness_threshold.value == timedelta(days=7)
    assert resolved.notification_mode.value is NotificationMode.ADDRESS_CHANGE
    assert resolved.notify_back_to_normal.value is False
    assert resolved.reminder_interval.value == timedelta(hours=1)
    origins = {
        resolved.check_period.origin,
        resolved.freshness_threshold.origin,
        resolved.notification_mode.origin,
        resolved.notify_back_to_normal.origin,
        resolved.reminder_interval.origin,
    }
    assert origins == {SettingOrigin.GLOBAL}


def test_24_deleting_a_group_keeps_domains_and_their_allowlists() -> None:
    group = Group.objects.create(name="Устаревшее, под снос", check_period_seconds=6 * 60 * 60)
    domain = make_domain("legacy.contoso.ru", group)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    group.delete()

    domain.refresh_from_db()
    assert domain.group is None
    assert [entry.raw for entry in domain.allowlist.all()] == ["10.20.0.0/22"]
    # Домен без группы наследует общие настройки (ТЗ 5).
    assert effective_settings(domain.pk).check_period.origin is SettingOrigin.GLOBAL


def test_group_value_of_false_is_not_mistaken_for_default() -> None:
    # Ловушка обнуляемого булева поля: False — заданное значение, NULL — «по умолчанию».
    settings = GlobalSettings.objects.get()
    settings.notify_back_to_normal = True
    settings.save()
    group = Group.objects.create(name="Платёжный контур", notify_back_to_normal=False)
    domain = make_domain("pay-gw.contoso.ru", group)

    resolved = effective_settings(domain.pk).notify_back_to_normal

    assert resolved.value is False
    assert resolved.origin is SettingOrigin.GROUP


def test_reminder_interval_zero_means_do_not_remind() -> None:
    group = Group.objects.create(name="Тестовые стенды", reminder_interval_seconds=0)
    domain = make_domain("stage.contoso.ru", group)

    resolved = effective_settings(domain.pk).reminder_interval

    assert resolved.value is None
    assert resolved.origin is SettingOrigin.GROUP
