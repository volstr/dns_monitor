"""Журнал и выгрузка — ТЗ 11.7, 4.5; решения Q-D4, Q-C11, Q-C21; критерий 49."""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import localtime

from apps.accounts.models import AuditAction, AuditRecord, Member, Role
from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from apps.monitoring.models import Event, EventType
from apps.monitoring.selectors.journal import (
    BOM,
    CSV_DELIMITER,
    EXPORT_LIMIT,
    ExportReady,
    ExportRefused,
    JournalFilter,
    JournalPeriod,
    journal_export,
    journal_page,
)
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )


@pytest.fixture
def domain() -> Domain:
    group = Group.objects.create(name="Платёжный контур")
    return Domain.objects.create(name="pay-gw.contoso.ru", group=group)


def event(domain: Domain, kind: str = EventType.NOT_ALLOWED, **extra: object) -> Event:
    payload = extra.pop("payload", {"addresses": ["185.44.9.201"]})
    notifications = extra.pop("notifications", ())
    row = Event.objects.create(
        domain=domain,
        event_type=kind,
        occurred_at=extra.pop("occurred_at", MOMENT),
        payload=payload,
        **extra,
    )
    for notification in notifications:
        row.notifications.add(notification)
    return row


def delivered(channel: ChannelKind, message_id: str = "1") -> Notification:
    return Notification.objects.create(
        channel=channel,
        recipient="кому-нибудь",
        text="Адрес вне разрешённых",
        status=DeliveryStatus.SENT,
        created_at=MOMENT,
        next_attempt_at=MOMENT,
        sent_at=MOMENT,
        channel_message_id=message_id,
    )


def test_d4_journal_merges_events_and_member_actions(domain: Domain) -> None:
    """Q-D4: журнал один, источников два. Пользователю про это знать незачем."""
    event(domain, occurred_at=MOMENT - timedelta(minutes=5))
    AuditRecord.objects.create(
        action=AuditAction.ROLE_CHANGED,
        occurred_at=MOMENT - timedelta(minutes=1),
        author_name="Анна Кирилова",
    )

    rows = journal_page(at=MOMENT).rows

    assert [row.label for row in rows] == ["Роль изменена", "Адрес вне разрешённых"]
    assert rows[0].domain_id is None
    assert rows[0].author_name == "Анна Кирилова"


def test_c11_journal_uses_the_approved_event_names(domain: Domain) -> None:
    event(domain, EventType.ADDRESS_APPEARED)

    assert journal_page(at=MOMENT).rows[0].label == "Появился адрес"


def test_11_7_filter_by_kind(domain: Domain) -> None:
    event(domain, EventType.NOT_ALLOWED)
    event(domain, EventType.ADDRESS_APPEARED)
    event(domain, EventType.RESOLVE_FAILED, payload={})

    changes = journal_page(at=MOMENT, chosen=JournalFilter.CHANGES).rows
    failures = journal_page(at=MOMENT, chosen=JournalFilter.RESOLVE_ERRORS).rows

    assert [row.label for row in changes] == ["Появился адрес"]
    assert [row.label for row in failures] == ["Домен не резолвится"]


def test_c21_period_narrows_the_selection(domain: Domain) -> None:
    event(domain, occurred_at=MOMENT - timedelta(hours=2))
    event(domain, occurred_at=MOMENT - timedelta(days=10))

    assert len(journal_page(at=MOMENT, period=JournalPeriod.TODAY).rows) == 1
    assert len(journal_page(at=MOMENT, period=JournalPeriod.MONTH).rows) == 2
    assert len(journal_page(at=MOMENT, period=JournalPeriod.ALL).rows) == 2


def test_11_7_search_finds_by_domain_and_by_address(domain: Domain) -> None:
    event(domain)
    other = Domain.objects.create(name="api.contoso.ru")
    event(other, payload={"addresses": ["10.20.1.14"]})

    by_name = journal_page(at=MOMENT, search="pay-gw").rows
    by_address = journal_page(at=MOMENT, search="10.20.1.14").rows

    assert [row.domain_name for row in by_name] == ["pay-gw.contoso.ru"]
    assert [row.domain_name for row in by_address] == ["api.contoso.ru"]


def test_4_5_delivery_result_is_shown_next_to_the_event(domain: Domain) -> None:
    event(domain, notifications=(delivered(ChannelKind.TELEGRAM),))

    row = journal_page(at=MOMENT).rows[0]

    assert [item.channel for item in row.deliveries] == ["Telegram"]
    # 14:38 UTC в поясе установки — 17:38 (ТЗ 4.0).
    assert row.deliveries[0].result == "доставлено 17:38:00"


def test_d11_every_channel_gets_its_own_line(domain: Domain) -> None:
    """
    Событие уходит во все настроенные каналы, и у каждого своя судьба (Q-D11, вариант 1).

    Показывать это одной строкой нельзя: «Telegram — доставлено, Почта — не доставлено» в одну
    строку читается как одно сообщение с двумя исходами. Дублировать событие в ленте — тоже:
    событие одно.
    """
    telegram = delivered(ChannelKind.TELEGRAM)
    email = Notification.objects.create(
        channel=ChannelKind.EMAIL,
        recipient="duty@contoso.ru",
        text="Адрес вне разрешённых",
        status=DeliveryStatus.FAILED,
        created_at=MOMENT,
        next_attempt_at=MOMENT,
        last_error="сервер не отвечает",
    )
    event(domain, notifications=(telegram, email))

    rows = journal_page(at=MOMENT).rows

    assert len(rows) == 1, "событие одно — строка журнала тоже одна"
    assert [(item.channel, item.result) for item in rows[0].deliveries] == [
        ("Telegram", "доставлено 17:38:00"),
        ("Почта", "не доставлено: сервер не отвечает"),
    ]


def test_4_5_event_without_a_notification_says_nothing_about_delivery(domain: Domain) -> None:
    event(domain)

    row = journal_page(at=MOMENT).rows[0]

    assert row.deliveries == ()


def test_49_export_is_utf8_with_bom_and_semicolons(domain: Domain) -> None:
    """Критерий 49: файл открывается в Excel без ручного импорта."""
    event(domain)

    result = journal_export(at=MOMENT)

    assert isinstance(result, ExportReady)
    text = result.content.decode("utf-8")
    assert text.startswith(BOM)
    header = text.splitlines()[0]
    assert CSV_DELIMITER in header
    assert "Момент" in header


def test_49_export_carries_the_current_selection_only(domain: Domain) -> None:
    """11.7: выгружается текущая выборка с фильтрами, а не весь журнал."""
    event(domain, EventType.NOT_ALLOWED)
    event(domain, EventType.ADDRESS_APPEARED)

    result = journal_export(at=MOMENT, chosen=JournalFilter.CHANGES)

    assert isinstance(result, ExportReady)
    assert result.rows == 1
    text = result.content.decode("utf-8")
    assert "Появился адрес" in text
    assert "Адрес вне разрешённых" not in text


def test_11_7_export_columns_follow_the_spec(domain: Domain) -> None:
    event(domain, payload={"addresses": ["185.44.9.201"], "notification_mode": "not_allowed"})

    result = journal_export(at=MOMENT)

    assert isinstance(result, ExportReady)
    lines = result.content.decode("utf-8").splitlines()
    columns = lines[1].split(CSV_DELIMITER)
    # Выгрузку открывают в Excel рядом с другими отчётами, поэтому время местное — но со
    # смещением, иначе столбец нельзя ни отсортировать вместе с чужим, ни прочитать через год.
    assert columns[0] == localtime(MOMENT).isoformat()
    assert columns[0].endswith("+03:00")
    assert columns[1] == "pay-gw.contoso.ru"
    assert columns[2] == "Платёжный контур"
    assert columns[3] == "Адрес вне разрешённых"
    assert columns[4] == "185.44.9.201"
    assert columns[5] == str(NotificationMode.NOT_ALLOWED.label)


def test_11_7_mode_is_the_one_recorded_at_the_time(domain: Domain) -> None:
    """Режим берётся из события, а не из сегодняшних настроек: их могли поменять."""
    event(domain, payload={"notification_mode": NotificationMode.ADDRESS_CHANGE.value})
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NONE)

    row = journal_page(at=MOMENT).rows[0]

    assert row.notification_mode == str(NotificationMode.ADDRESS_CHANGE.label)


def test_11_7_export_refuses_above_the_limit(
    domain: Domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """11.7: при превышении выгрузка не выполняется, предлагается сузить период."""
    event(domain)
    monkeypatch.setattr("apps.monitoring.selectors.journal.EXPORT_LIMIT", 0)

    result = journal_export(at=MOMENT)

    assert isinstance(result, ExportRefused)
    assert "Сузьте период" in result.reason


def test_export_limit_is_the_number_from_the_spec() -> None:
    assert EXPORT_LIMIT == 100_000


def test_3_observer_reads_the_journal_and_exports_it(
    client: Client, observer: Member, domain: Domain
) -> None:
    """ТЗ 3: журнал входит в то, что наблюдатель читает."""
    event(domain)
    client.force_login(observer)

    assert client.get(reverse("event-list")).status_code == 200

    response = client.get(reverse("event-export"))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert response.content.decode("utf-8").startswith(BOM)


def test_49_export_keeps_the_filter_from_the_query(
    client: Client, observer: Member, domain: Domain
) -> None:
    event(domain, EventType.NOT_ALLOWED)
    event(domain, EventType.ADDRESS_APPEARED)
    client.force_login(observer)

    body = client.get(reverse("event-export"), {"chosen": "changes"}).content.decode("utf-8")

    assert "Появился адрес" in body
    assert "Адрес вне разрешённых" not in body


# ─────────────── «кто, кого» в записи об участнике — 3.1, Q-D43 ───────────────


def test_3_1_a_member_record_names_both_people(client: Client, observer: Member) -> None:
    """
    3.1: журнал показывает «кто, кого, что изменил и когда». Имён здесь два, и оба нужны.

    Прежде селектор сводил их к одному через `author_name or target_name`. Автор есть у каждой
    такой записи, поэтому второе имя не показывалось никогда: строка «Участник отключён»
    называла администратора и читалась так, будто отключили его.
    """
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_DEACTIVATED,
        occurred_at=MOMENT,
        author_name="Анна Кирилова",
        target_name="test",
    )

    row = journal_page(at=MOMENT).rows[0]

    assert row.label == "Участник отключён"
    assert row.target_name == "test"
    assert row.author_name == "Анна Кирилова"


def test_3_1_the_screen_shows_who_was_switched_off(client: Client, observer: Member) -> None:
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_DEACTIVATED,
        occurred_at=MOMENT,
        author_name="Анна Кирилова",
        target_name="test",
    )
    client.force_login(observer)

    page = client.get(reverse("event-list")).content.decode()

    assert "Участник отключён" in page
    assert "test" in page
    assert "Выполнил: Анна Кирилова" in page


def test_3_1_search_finds_a_member_record_by_either_name() -> None:
    """
    Поиск по записям об участниках отбрасывал их целиком.

    Пока имён на экране не было, искать в них было нечего; теперь они видны, и человек будет
    искать именно по ним.
    """
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_DEACTIVATED,
        occurred_at=MOMENT,
        author_name="Анна Кирилова",
        target_name="test",
    )

    assert len(journal_page(at=MOMENT, search="test").rows) == 1
    assert len(journal_page(at=MOMENT, search="Кирилова").rows) == 1
    assert len(journal_page(at=MOMENT, search="pay-gw").rows) == 0


def test_d43_export_names_the_member(domain: Domain) -> None:
    """
    Q-D43: девятая колонка сверх перечня 11.7 — иначе запись об участнике безымянна.

    По выгрузке разбирают инцидент; строка «Участник отключён» без имени для разбора
    бесполезна, а 3.1 требует от журнала «кто, кого, что изменил и когда».
    """
    event(domain)
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_DEACTIVATED,
        occurred_at=MOMENT,
        author_name="Анна Кирилова",
        target_name="test",
    )

    result = journal_export(at=MOMENT)

    assert isinstance(result, ExportReady)
    lines = result.content.decode("utf-8").splitlines()
    assert lines[0].split(CSV_DELIMITER)[8] == "Участник"
    member_row = next(line for line in lines if "Участник отключён" in line)
    assert member_row.split(CSV_DELIMITER)[8] == "test"
    # Восемь колонок 11.7 остались на своих местах: доменная строка не сдвинулась.
    domain_row = next(line for line in lines if "pay-gw.contoso.ru" in line)
    assert domain_row.split(CSV_DELIMITER)[1] == "pay-gw.contoso.ru"


def test_d11_export_pairs_channels_with_their_results(domain: Domain) -> None:
    """
    Колонок 11.7 по-прежнему две, а каналов может быть несколько (Q-D11).

    Значения перечисляются через «; » **попарно**: третий канал напротив третьего результата.
    Разносить событие на несколько строк файла было бы хуже — сводная таблица по типам событий
    начала бы считать каждое событие дважды.
    """
    telegram = delivered(ChannelKind.TELEGRAM)
    email = Notification.objects.create(
        channel=ChannelKind.EMAIL,
        recipient="duty@contoso.ru",
        text="Адрес вне разрешённых",
        status=DeliveryStatus.FAILED,
        created_at=MOMENT,
        next_attempt_at=MOMENT,
        last_error="сервер не отвечает",
    )
    event(domain, notifications=(telegram, email))

    result = journal_export(at=MOMENT)

    assert isinstance(result, ExportReady)
    assert result.rows == 1, "событие одно — строка файла тоже одна"
    columns = result.content.decode("utf-8").splitlines()[1].split(CSV_DELIMITER)
    assert columns[6] == "Telegram | Почта"
    assert columns[7] == "доставлено 17:38:00 | не доставлено: сервер не отвечает"
    # Ячейку не пришлось брать в кавычки: разделитель каналов не совпадает с разделителем
    # колонок, и файл режется простым `split`.
    assert '"' not in result.content.decode("utf-8")
