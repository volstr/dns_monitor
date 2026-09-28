"""
Импорт списком — ТЗ 10.3, критерии 25, 26, 27.

Главное требование 10.3 — «импорт **не выполняется вслепую**». Поэтому проверяется не только
итог, но и то, что шаг разбора ничего не меняет: между вставкой и подтверждением человек может
уйти со страницы, и после этого в базе не должно появиться ни домена.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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
from apps.catalog.services.import_list import apply_import, preview_import
from apps.monitoring.models import Event, EventType
from apps.monitoring.services.tick import tick
from apps.notifications.models import Notification
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)

LIST_WITH_A_BAD_LINE = "\n".join(
    [
        "api-v2.contoso.ru; 10.20.0.0/22; боевой API",
        "billing.contoso.ru; 10.20.1.0/24 10.20.3.7",
        "docs.contoso.ru",
        "cdn-2.contoso.ru; 10.20.999.0/24",
    ]
)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture(autouse=True)
def _settings_row() -> GlobalSettings:
    return GlobalSettings.objects.get_or_create()[0]


def test_10_3_parsing_changes_nothing() -> None:
    """Шаг разбора — только показ: ушёл со страницы, и в базе пусто."""
    preview = preview_import(text=LIST_WITH_A_BAD_LINE)

    assert preview.to_add == 3
    assert preview.broken == 1
    assert Domain.objects.count() == 0


def test_25_good_lines_are_added_and_bad_ones_come_back() -> None:
    """Критерий 25 дословно."""
    result = apply_import(
        text=LIST_WITH_A_BAD_LINE,
        group_id=None,
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )

    assert result.added == 3
    assert sorted(Domain.objects.values_list("name", flat=True)) == [
        "api-v2.contoso.ru",
        "billing.contoso.ru",
        "docs.contoso.ru",
    ]
    # Ошибочная строка вернулась целиком, вместе с тем, что человек набрал после точки с запятой.
    assert result.failed_text == "cdn-2.contoso.ru; 10.20.999.0/24"


def test_25_error_line_stays_in_the_field_on_the_screen(client: Client, admin: Member) -> None:
    """Тот же критерий с экрана: строка возвращается в поле ввода, а не пропадает."""
    client.force_login(admin)

    body = client.post(
        reverse("domain-import-apply"),
        {"text": LIST_WITH_A_BAD_LINE, "group": "", "check_now": "yes"},
    ).content.decode()

    assert "cdn-2.contoso.ru; 10.20.999.0/24" in body
    assert "исправьте их и повторите импорт" in body


def test_26_existing_domain_is_skipped_and_untouched() -> None:
    """Критерий 26: флаг обновления выключен — настройки домена не изменились."""
    group = Group.objects.create(name="Платёжный контур")
    existing = Domain.objects.create(
        name="billing.contoso.ru", group=group, comment="не трогать"
    )
    AllowlistEntry.objects.create(domain=existing, entry="10.9.9.0/24", raw="10.9.9.0/24")

    result = apply_import(
        text="billing.contoso.ru; 10.20.1.0/24; переписанный комментарий",
        group_id=None,
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )

    existing.refresh_from_db()
    assert result.skipped == 1
    assert result.added == 0
    assert existing.comment == "не трогать"
    assert existing.group_id == group.pk
    assert list(existing.allowlist.values_list("raw", flat=True)) == ["10.9.9.0/24"]


def test_26_update_flag_replaces_comment_and_allowlist() -> None:
    """Включённый флаг делает ровно то, что обещает, — и только это."""
    group = Group.objects.create(name="Платёжный контур")
    existing = Domain.objects.create(name="billing.contoso.ru", group=group, comment="было")
    AllowlistEntry.objects.create(domain=existing, entry="10.9.9.0/24", raw="10.9.9.0/24")
    other = Group.objects.create(name="Продакшн-периметр")

    apply_import(
        text="billing.contoso.ru; 10.20.1.0/24; стало",
        group_id=other.pk,
        update_existing=True,
        check_now=True,
        at=MOMENT,
    )

    existing.refresh_from_db()
    assert existing.comment == "стало"
    assert list(existing.allowlist.values_list("raw", flat=True)) == ["10.20.1.0/24"]
    # Группа не меняется даже при обновлении: перенос — отдельная операция со своей сводкой.
    assert existing.group_id == group.pk


def test_27_cyrillic_domain_is_shown_in_punycode_before_confirmation() -> None:
    """Критерий 27: молчаливого искажения нет — перевод виден в разборе."""
    preview = preview_import(text="контосо.рф; 10.20.0.0/22")
    line = preview.lines[0]

    assert line.converted_from == "контосо.рф"
    assert line.name == "xn--j1agcbarf.xn--p1ai"
    assert line.error is None


def test_27_the_screen_shows_the_punycode(client: Client, admin: Member) -> None:
    client.force_login(admin)

    body = client.post(
        reverse("domain-import"), {"text": "контосо.рф; 10.20.0.0/22"}
    ).content.decode()

    assert "контосо.рф → xn--j1agcbarf.xn--p1ai" in body


def test_27_undecodable_name_is_an_error_not_a_silent_mangling() -> None:
    """Перевод невозможен — строка отказана с причиной, а не записана как попало."""
    preview = preview_import(text="-" * 70 + ".рф")

    assert preview.broken == 1
    assert preview.lines[0].error


def test_10_3_domain_without_allowlist_is_accepted_with_a_warning() -> None:
    preview = preview_import(text="docs.contoso.ru")

    assert preview.to_add == 1
    assert "следить получится только за изменением" in preview.lines[0].warning


def test_10_3_summary_counts_three_buckets() -> None:
    Domain.objects.create(name="docs.contoso.ru")

    preview = preview_import(text=LIST_WITH_A_BAD_LINE)

    assert (preview.to_add, preview.already_here, preview.broken) == (2, 1, 1)


def test_10_3_imported_domains_land_in_the_chosen_group() -> None:
    group = Group.objects.create(name="Продакшн-периметр")

    apply_import(
        text="docs.contoso.ru",
        group_id=group.pk,
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )

    assert Domain.objects.get(name="docs.contoso.ru").group_id == group.pk


def test_10_4_import_can_create_the_group_it_names() -> None:
    """10.4: «при отсутствии совпадений предлагается создать группу с введённым названием»."""
    result = apply_import(
        text="docs.contoso.ru",
        group_id=None,
        new_group_name="Свежая группа",
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )

    assert result.group_name == "Свежая группа"
    assert Domain.objects.get(name="docs.contoso.ru").group.name == "Свежая группа"


def test_10_3_check_now_puts_the_first_check_at_once() -> None:
    apply_import(
        text="docs.contoso.ru",
        group_id=None,
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )

    assert Domain.objects.get(name="docs.contoso.ru").next_check_at == MOMENT


def test_10_3_without_the_flag_checks_are_spread_inside_the_period() -> None:
    """11.1: двести импортированных доменов не должны уйти в резолв одной пачкой."""
    period = GlobalSettings.objects.get().check_period_seconds
    names = "\n".join(f"host-{number}.contoso.ru" for number in range(40))

    apply_import(text=names, group_id=None, update_existing=False, check_now=False, at=MOMENT)

    scheduled = set(Domain.objects.values_list("next_check_at", flat=True))
    assert len(scheduled) > 1
    assert all(MOMENT <= moment <= MOMENT + timedelta(seconds=period) for moment in scheduled)


def test_10_3_file_upload_is_read_as_a_list(client: Client, admin: Member) -> None:
    """10.3: источников два — вставка и файл. Файл побеждает пустую вставку."""
    client.force_login(admin)
    upload = _file("docs.contoso.ru; 10.20.0.0/22\n")

    body = client.post(reverse("domain-import"), {"text": "", "source": upload}).content.decode()

    assert "docs.contoso.ru" in body
    assert "Добавим" in body


def test_10_3_first_step_offers_both_sources_and_the_flags(
    client: Client, admin: Member
) -> None:
    """Экран вставки: оба источника из 10.3 и флаг проверки, включённый по умолчанию."""
    Group.objects.create(name="Продакшн-периметр")
    client.force_login(admin)

    body = client.get(reverse("domain-import")).content.decode()

    assert 'name="text"' in body
    assert 'accept=".txt,.csv' in body
    assert 'name="check_now" value="yes" checked' in body
    assert "Продакшн-периметр" in body
    # Многострочные комментарии шаблона не должны утекать в разметку.
    assert "клиентский код разрешён" not in body


def test_10_3_confirmation_offers_the_update_flag_switched_off(
    client: Client, admin: Member
) -> None:
    """10.3: «перезапись чужих настроек не должна происходить сама собой»."""
    client.force_login(admin)

    body = client.post(reverse("domain-import"), {"text": "docs.contoso.ru"}).content.decode()

    assert 'name="update_existing" value="yes">' in body
    assert "checked>\n          Обновить" not in body


def test_10_3_observer_cannot_open_the_import(client: Client) -> None:
    observer = Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )
    client.force_login(observer)

    assert client.get(reverse("domain-import")).status_code == 403


def _file(text: str) -> SimpleUploadedFile:
    return SimpleUploadedFile("domains.txt", text.encode(), content_type="text/plain")


def test_34_two_hundred_domains_checked_at_once_produce_no_change_notifications() -> None:
    """
    Критерий 34: импорт 200 доменов с флагом «проверить сразу» → 200 `WATCH_STARTED`, ни одного
    уведомления об изменении адресов.

    Это тот самый случай, ради которого 7.4 и написано (ТЗ, разбор перед 7.4): при первой в
    жизни проверке в актуальном наборе нет ничего, поэтому новыми оказываются **все** адреса.
    Без правила о базовой проверке импорт двухсот доменов породил бы больше двухсот событий и
    столько же сообщений — «инструмент будет отключён в первый же день».

    Режим уведомлений здесь самый разговорчивый из имеющихся: по умолчанию установка сообщает
    только о выходе за список разрешённых, и тогда тишина ничего не доказывала бы.
    """
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.ADDRESS_CHANGE)
    names = [f"host-{index:03d}.contoso.ru" for index in range(200)]

    result = apply_import(
        text="\n".join(names),
        group_id=None,
        update_existing=False,
        check_now=True,
        at=MOMENT,
    )
    assert result.added == 200

    resolver = FakeResolver({name: [answered("10.20.1.14", "10.20.2.60")] for name in names})
    store = FakeStateStore(FakeClock(MOMENT))
    report = tick(at=MOMENT, resolver=resolver, state_store=store, concurrency=1, limit=200)

    assert report.checked == 200
    assert Event.objects.filter(event_type=EventType.WATCH_STARTED).count() == 200
    assert (
        Event.objects.filter(
            event_type__in=[EventType.ADDRESS_APPEARED, EventType.ADDRESS_GONE]
        ).count()
        == 0
    )
    assert Notification.objects.count() == 0
