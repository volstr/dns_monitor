"""Журнал и выгрузка — ТЗ 11.7, 4.5; решения Q-D4, Q-C11, Q-C21; критерий 49."""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

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
    return Event.objects.create(
        domain=domain,
        event_type=kind,
        occurred_at=extra.pop("occurred_at", MOMENT),
        payload=payload,
        **extra,
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
    notification = Notification.objects.create(
        channel=ChannelKind.TELEGRAM,
        recipient="-1001234567890",
        text="Адрес вне разрешённых",
        status=DeliveryStatus.SENT,
        created_at=MOMENT,
        next_attempt_at=MOMENT,
        sent_at=MOMENT,
    )
    event(domain, notification=notification)

    row = journal_page(at=MOMENT).rows[0]

    assert row.channel == "Telegram"
    assert row.delivery == "доставлено 14:38:00"


def test_4_5_event_without_a_notification_says_nothing_about_delivery(domain: Domain) -> None:
    event(domain)

    row = journal_page(at=MOMENT).rows[0]

    assert (row.channel, row.delivery) == ("", "")


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
    assert columns[0] == MOMENT.isoformat()
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
