"""Общие настройки и обзор — ТЗ 4.1, 8.3, 11.9; решения Q-13.1, Q-D19, Q-C12; критерий 29."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group, NotificationMode
from apps.catalog.selectors.global_settings import settings_page
from apps.catalog.services.settings import (
    SettingsInput,
    SettingsNotSaved,
    SettingsSaved,
    save_global_settings,
)
from apps.monitoring.models import AddressRegistryEntry, Incident
from apps.monitoring.selectors.overview import overview

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
HOUR = 60 * 60


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


def values(**extra: object) -> SettingsInput:
    fields: dict[str, object] = {
        "check_period_seconds": 5 * 60,
        "freshness_threshold_seconds": 24 * HOUR,
        "notification_mode": NotificationMode.NOT_ALLOWED.value,
        "notify_back_to_normal": True,
        "reminder_interval_seconds": 6 * HOUR,
        "merge_window_seconds": 60,
        "merge_threshold": 5,
        "dns_servers": ("10.0.0.1", "10.0.0.2"),
        "check_ipv6": True,
        "resolve_failure_threshold": 3,
        "event_retention_seconds": 90 * 24 * HOUR,
        "registry_retention_seconds": None,
        "message_include_full_set": True,
        "message_include_comment": True,
        "message_include_link": True,
        "session_lifetime_seconds": 7 * 24 * HOUR,
        "login_failure_threshold": 5,
        "telegram_chat_id": "-1001234567890",
        "email_recipients": (),
        "webhook_url": "",
    }
    fields.update(extra)
    return SettingsInput(**fields)  # type: ignore[arg-type]


def test_4_1_settings_are_saved() -> None:
    result = save_global_settings(values(check_period_seconds=15 * 60))

    assert isinstance(result, SettingsSaved)
    assert GlobalSettings.objects.get().check_period_seconds == 15 * 60


def test_11_1_period_below_a_minute_is_refused() -> None:
    result = save_global_settings(values(check_period_seconds=30))

    assert isinstance(result, SettingsNotSaved)
    assert "меньше минуты" in result.reasons[0]
    assert GlobalSettings.objects.get().check_period_seconds == 5 * 60


def test_4_1_at_least_one_dns_server_is_required() -> None:
    result = save_global_settings(values(dns_servers=()))

    assert isinstance(result, SettingsNotSaved)
    assert "хотя бы один DNS-сервер" in result.reasons[0]


def test_4_1_dns_server_must_be_an_address() -> None:
    result = save_global_settings(values(dns_servers=("не адрес",)))

    assert isinstance(result, SettingsNotSaved)
    assert "не похоже на адрес DNS-сервера" in result.reasons[0]


def test_8_3_bad_email_recipient_is_refused() -> None:
    result = save_global_settings(values(email_recipients=("не почта",)))

    assert isinstance(result, SettingsNotSaved)
    assert "не похоже на почтовый адрес" in result.reasons[0]


def test_13_1_saving_globals_warns_about_the_groups_it_breaks() -> None:
    """Решение Q-13.1: пересчитываются все группы, сломавшиеся называются поимённо."""
    Group.objects.create(name="Платёжный контур", freshness_threshold_seconds=HOUR)

    result = save_global_settings(values(check_period_seconds=6 * HOUR))

    assert isinstance(result, SettingsSaved)
    assert any("«Платёжный контур»" in warning for warning in result.warnings)
    # Предупреждение, а не запрет: значение сохранено.
    assert GlobalSettings.objects.get().check_period_seconds == 6 * HOUR


def test_8_5_zero_reminder_survives_the_round_trip() -> None:
    """Ноль — «не напоминать» (8.5). Он не должен потеряться при пересчёте в часы."""
    save_global_settings(values(reminder_interval_seconds=0))

    assert settings_page().reminder_interval_hours == 0


def test_4_1_registry_retention_may_be_unset() -> None:
    save_global_settings(values(registry_retention_seconds=None))

    assert settings_page().registry_retention_days is None


def test_d19_observer_reads_settings_without_recipients(
    client: Client, observer: Member
) -> None:
    """Q-D19: наблюдателю экран на чтение, но блок адресатов не показывается."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="-1001234567890")
    client.force_login(observer)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Общие настройки" in body
    assert "Каналы доставки" not in body
    assert "-1001234567890" not in body


def test_29_observer_cannot_save_settings(client: Client, observer: Member) -> None:
    """Критерий 29: наблюдатель пытается изменить настройки → отказ."""
    client.force_login(observer)

    response = client.post(reverse("settings-save"), {"check_period_minutes": "15"})

    assert response.status_code == 403
    assert GlobalSettings.objects.get().check_period_seconds == 5 * 60


def test_admin_sees_recipients(client: Client, admin: Member) -> None:
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="-1001234567890")
    client.force_login(admin)

    body = client.get(reverse("settings")).content.decode()

    assert "Каналы доставки" in body
    assert "-1001234567890" in body


def test_8_3_settings_screen_has_no_field_for_the_bot_token(
    client: Client, admin: Member
) -> None:
    """
    8.3: подключение задаётся в окружении, поля ввода для него в интерфейсе быть не должно.

    Проверяется отсутствие именно **поля**, а не слов: подсказка «токен бота задаётся в
    окружении» на экране нужна — она отвечает администратору на вопрос «а где же он».
    """
    client.force_login(admin)

    body = client.get(reverse("settings")).content.decode()

    assert 'name="telegram_bot_token"' not in body
    assert 'name="smtp_password"' not in body
    assert 'type="password"' not in body
    # Адресат при этом задаётся здесь и полем быть обязан (Q-D11).
    assert 'name="telegram_chat_id"' in body


def test_d27_ipv6_switch_is_saved_from_the_screen(client: Client, admin: Member) -> None:
    """Q-D27: снятый флажок означает «адресов IPv6 не существует»."""
    client.force_login(admin)
    fields = {
        "check_period_minutes": "5",
        "freshness_threshold_hours": "24",
        "notification_mode": NotificationMode.NOT_ALLOWED.value,
        "reminder_interval_hours": "6",
        "merge_window_seconds": "60",
        "merge_threshold": "5",
        "dns_servers": "10.0.0.1",
        "resolve_failure_threshold": "3",
        "event_retention_days": "90",
        "session_lifetime_hours": "168",
        "login_failure_threshold": "5",
    }

    client.post(reverse("settings-save"), fields)
    assert GlobalSettings.objects.get().check_ipv6 is False

    client.post(reverse("settings-save"), fields | {"check_ipv6": "on"})
    assert GlobalSettings.objects.get().check_ipv6 is True


def test_d27_screen_offers_the_ipv6_switch(client: Client, admin: Member) -> None:
    client.force_login(admin)

    body = client.get(reverse("settings")).content.decode()

    assert "Проверять адреса IPv6" in body
    assert 'name="check_ipv6"' in body


def test_4_1_admin_saves_through_the_form(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("settings-save"),
        {
            "check_period_minutes": "15",
            "freshness_threshold_hours": "48",
            "notification_mode": NotificationMode.ADDRESS_CHANGE.value,
            "reminder_interval_hours": "0",
            "merge_window_seconds": "90",
            "merge_threshold": "4",
            "dns_servers": "10.0.0.1\n10.0.0.2",
            "resolve_failure_threshold": "2",
            "event_retention_days": "30",
            "session_lifetime_hours": "8",
            "login_failure_threshold": "4",
        },
    )

    assert response.status_code == 302
    row = GlobalSettings.objects.get()
    assert (row.check_period_seconds, row.freshness_threshold_seconds) == (15 * 60, 48 * HOUR)
    assert row.reminder_interval_seconds == 0
    assert row.dns_servers == ["10.0.0.1", "10.0.0.2"]
    # Снятые флажки означают «не включать»: форма присылает их отсутствием.
    assert row.notify_back_to_normal is False


def test_11_9_overview_lists_open_incidents_oldest_first() -> None:
    # Оба домена должны иметь **настоящее** расхождение: блок показывает нерешённые адреса, а
    # не открытые записи в таблице (Q-D39). Список задан у обоих, и отданный адрес в него не
    # входит — без списка адрес получил бы статус «не проверяется» (Q-D25).
    old = Domain.objects.create(name="old.contoso.ru")
    new = Domain.objects.create(name="new.contoso.ru")
    for domain, address in ((old, "185.44.9.201"), (new, "185.44.9.202")):
        AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
        AddressRegistryEntry.objects.create(
            domain=domain, address=address, first_seen_at=MOMENT, last_seen_at=MOMENT
        )
    Incident.objects.create(domain=old, opened_at=MOMENT - timedelta(hours=3, minutes=12))
    Incident.objects.create(domain=new, opened_at=MOMENT - timedelta(minutes=5))

    page = overview(at=MOMENT)

    assert [item.domain_name for item in page.open_items] == ["old.contoso.ru", "new.contoso.ru"]
    assert page.open_items[0].age_text == "держится 3 ч 12 мин"
    assert page.open_items[0].addresses == ("185.44.9.201",)


def test_11_9_no_open_incidents_names_the_last_closed_one() -> None:
    """11.9: явная строка, а не пустое место."""
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    Incident.objects.create(
        domain=domain, opened_at=MOMENT - timedelta(days=1), closed_at=MOMENT - timedelta(hours=2)
    )

    page = overview(at=MOMENT)

    assert page.open_items == ()
    assert page.last_closed_at == MOMENT - timedelta(hours=2)


def test_c12_overview_reports_last_and_next_check_not_a_round() -> None:
    Domain.objects.create(
        name="pay-gw.contoso.ru",
        last_successful_check_at=MOMENT - timedelta(minutes=2),
        next_check_at=MOMENT + timedelta(minutes=3),
    )

    page = overview(at=MOMENT)

    assert page.last_check_at == MOMENT - timedelta(minutes=2)
    assert page.next_check_at == MOMENT + timedelta(minutes=3)


def test_c12_overview_screen_does_not_promise_a_round(client: Client, observer: Member) -> None:
    client.force_login(observer)

    body = client.get(reverse("overview")).content.decode()

    assert "круг проверок" not in body.lower()


def test_d40_a_bot_token_in_the_chat_id_field_is_refused() -> None:
    """
    Q-D40: токен бота в поле адресата — отказ, а не предупреждение.

    Ошибка лёгкая: и то, и другое выдаёт @BotFather. Но токен по 8.3 живёт только в окружении, а
    это поле уходит в базу, в резервные копии, на экран администратору и снимком в каждую строку
    очереди. Предупреждение человек прочёл бы уже после того, как секрет попал в базу.
    """
    token = "8155827613:" + "A" * 35  # вид токена, не настоящий
    values_with_token = values(telegram_chat_id=token)

    result = save_global_settings(values_with_token)

    assert isinstance(result, SettingsNotSaved)
    assert any("токен бота" in reason for reason in result.reasons)
    assert GlobalSettings.objects.get().telegram_chat_id == ""


def test_d40_a_real_chat_id_is_accepted() -> None:
    """Обратная сторона: обычный идентификатор чата — в том числе отрицательный — проходит."""
    result = save_global_settings(values(telegram_chat_id="-1001234567890"))

    assert isinstance(result, SettingsSaved)
    assert GlobalSettings.objects.get().telegram_chat_id == "-1001234567890"


def test_channel_buttons_are_on_the_screen(client: Client, admin: Member) -> None:
    """Кнопки из макета, блок «Каналы доставки»."""
    client.force_login(admin)

    body = client.get(reverse("settings")).content.decode()

    assert "Отправить тестовое" in body
    assert "Отключить" in body
    # Через formaction, а не вложенной формой: форма настроек одна на весь экран.
    assert f'formaction="{reverse("channel-probe")}"' in body
    assert f'formaction="{reverse("channel-disconnect")}"' in body


def test_disconnect_button_clears_the_recipient(client: Client, admin: Member) -> None:
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="-1001234567890")
    client.force_login(admin)

    client.post(reverse("channel-disconnect"), follow=True)

    assert GlobalSettings.objects.get().telegram_chat_id == ""


def test_29_observer_cannot_touch_the_channel(client: Client, observer: Member) -> None:
    """Критерий 29: наблюдатель настройки не меняет, и каналы — тоже настройки."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="-1001234567890")
    client.force_login(observer)

    assert client.post(reverse("channel-probe")).status_code == 403
    assert client.post(reverse("channel-disconnect")).status_code == 403
    assert GlobalSettings.objects.get().telegram_chat_id == "-1001234567890"


def test_79_connection_settings_of_mail_are_not_part_of_the_api(
    client: Client, admin: Member
) -> None:
    """
    Критерий 79: попытка изменить параметры подключения почты через API → таких полей нет.

    Разграничение 8.3: адресат задаётся в интерфейсе, **подключение** — в окружении. Иначе
    администратор приложения получил бы возможность направить исходящую почту через свой
    сервер, а вместе с ней — чужие учётные данные в поле ввода.

    Проверяется не отказ, а именно отсутствие: поля нет ни в типе, который принимает сервис,
    ни среди столбцов таблицы. Отказ можно однажды ослабить, поля — нельзя.
    """
    client.force_login(admin)
    forbidden = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_tls")

    fields = {
        "check_period_minutes": "5",
        "freshness_threshold_hours": "24",
        "notification_mode": NotificationMode.NOT_ALLOWED.value,
        "reminder_interval_hours": "6",
        "merge_window_seconds": "60",
        "merge_threshold": "5",
        "dns_servers": "10.0.0.1",
        "resolve_failure_threshold": "3",
        "event_retention_days": "90",
        "session_lifetime_hours": "168",
        "login_failure_threshold": "5",
    }

    client.post(reverse("settings-save"), fields | dict.fromkeys(forbidden, "злоумышленник"))

    row = GlobalSettings.objects.get()
    columns = {field.name for field in GlobalSettings._meta.get_fields()}
    input_fields = {field.name for field in dataclasses.fields(SettingsInput)}
    for name in forbidden:
        assert name not in columns, f"{name} не должен существовать в общих настройках"
        assert name not in input_fields, f"{name} не должен приниматься сервисом"
        assert not hasattr(row, name)
    # Подключение по-прежнему читается только из окружения.
    assert django_settings.ENV.smtp_host is None
