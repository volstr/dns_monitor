"""
Сроки хранения — ТЗ 11.3, plan.md этап 8.

11.3 называет два срока и одно значение по умолчанию, которого легко не заметить: реестр
адресов хранится **бессрочно**. Поэтому половина проверок здесь о том, что уборка чего-то
не сделала.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.models import AuditAction, AuditRecord, Member, Role
from apps.catalog.models import Domain, GlobalSettings
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    BucketSize,
    Event,
    EventType,
    Incident,
)
from apps.monitoring.services.retention import (
    HOURLY_BUCKET_LIFETIME,
    PURGE_INTERVAL,
    purge_due,
    purge_history,
)
from apps.notifications.models import ChannelKind, DeliveryStatus, Notification

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=120)
RECENTLY = NOW - timedelta(days=2)


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name='pay-gw.contoso.ru')


def event(domain: Domain, at: datetime, incident: Incident | None = None) -> Event:
    return Event.objects.create(
        domain=domain,
        event_type=EventType.ADDRESS_APPEARED,
        occurred_at=at,
        incident=incident,
    )


def registry(domain: Domain, address: str, last_seen_at: datetime) -> AddressRegistryEntry:
    return AddressRegistryEntry.objects.create(
        domain=domain,
        address=address,
        first_seen_at=LONG_AGO,
        last_seen_at=last_seen_at,
    )


def notification(at: datetime, status: str) -> Notification:
    return Notification.objects.create(
        channel=ChannelKind.TELEGRAM,
        recipient='-100500',
        text='сообщение',
        status=status,
        created_at=at,
        next_attempt_at=at,
    )


# ─────────────── журнал событий: 90 суток по умолчанию ───────────────


def test_11_3_old_events_are_deleted(domain: Domain) -> None:
    event(domain, LONG_AGO)
    event(domain, RECENTLY)

    purged = purge_history(at=NOW)

    assert purged.events == 1
    assert Event.objects.count() == 1


def test_11_3_retention_is_configurable(domain: Domain) -> None:
    """«Срок настраивается»: суточный срок удаляет то, что при 90-дневном оставалось."""
    GlobalSettings.objects.filter(pk=1).update(event_retention_seconds=24 * 60 * 60)
    event(domain, RECENTLY)

    purge_history(at=NOW)

    assert Event.objects.count() == 0


def test_11_3_audit_records_follow_the_same_term(domain: Domain) -> None:
    """Журнал в интерфейсе один (Q-D4), и срок хранения у него обязан быть один."""
    member = Member.objects.create_member(
        email='boss@contoso.ru', name='Анна Кирилова', created_at=LONG_AGO, role=Role.ADMIN
    )
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_INVITED, occurred_at=LONG_AGO, author=member
    )
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_INVITED, occurred_at=RECENTLY, author=member
    )

    purged = purge_history(at=NOW)

    assert purged.audit_records == 1
    assert AuditRecord.objects.count() == 1


def test_11_3_events_of_an_open_incident_survive_their_term(domain: Domain) -> None:
    """Инцидент, который держится дольше срока, — не старьё, а незакрытая проблема."""
    incident = Incident.objects.create(domain=domain, opened_at=LONG_AGO)
    event(domain, LONG_AGO, incident)

    purged = purge_history(at=NOW)

    assert purged.events == 0
    assert Event.objects.count() == 1


def test_11_3_events_of_a_closed_incident_go_as_usual(domain: Domain) -> None:
    incident = Incident.objects.create(
        domain=domain, opened_at=LONG_AGO, closed_at=LONG_AGO + timedelta(hours=1)
    )
    event(domain, LONG_AGO, incident)

    purge_history(at=NOW)

    assert Event.objects.count() == 0


# ─────────────── реестр адресов: бессрочно по умолчанию ───────────────


def test_11_3_registry_is_kept_forever_by_default(domain: Domain) -> None:
    """Значение по умолчанию из 11.3 — «бессрочно». Его легко не заметить и стереть реестр."""
    registry(domain, '10.20.3.7', LONG_AGO)

    purged = purge_history(at=NOW)

    assert purged.registry_entries == 0
    assert AddressRegistryEntry.objects.count() == 1


def test_11_3_registry_term_deletes_what_stopped_being_answered(domain: Domain) -> None:
    GlobalSettings.objects.filter(pk=1).update(registry_retention_seconds=30 * 24 * 60 * 60)
    registry(domain, '10.20.3.7', LONG_AGO)

    purged = purge_history(at=NOW)

    assert purged.registry_entries == 1


def test_11_3_an_address_still_answered_survives_any_term(domain: Domain) -> None:
    """
    Отбор по `last_seen_at`: у адреса, который отдаётся третий год, стара только дата
    появления."""
    GlobalSettings.objects.filter(pk=1).update(registry_retention_seconds=30 * 24 * 60 * 60)
    registry(domain, '10.20.3.7', NOW)

    purge_history(at=NOW)

    assert AddressRegistryEntry.objects.count() == 1


# ─────────────── корзины следа ───────────────


def test_hourly_buckets_live_thirty_days_as_the_model_promises(domain: Domain) -> None:
    AddressObservationBucket.objects.create(
        domain=domain,
        address='10.20.3.7',
        bucket_size=BucketSize.HOUR,
        bucket_start=NOW - HOURLY_BUCKET_LIFETIME - timedelta(hours=1),
    )
    AddressObservationBucket.objects.create(
        domain=domain,
        address='10.20.3.7',
        bucket_size=BucketSize.HOUR,
        bucket_start=NOW - timedelta(hours=1),
    )

    purged = purge_history(at=NOW)

    assert purged.hourly_buckets == 1
    assert AddressObservationBucket.objects.count() == 1


def test_daily_buckets_are_kept_forever_by_default(domain: Domain) -> None:
    AddressObservationBucket.objects.create(
        domain=domain,
        address='10.20.3.7',
        bucket_size=BucketSize.DAY,
        bucket_start=LONG_AGO,
    )

    purged = purge_history(at=NOW)

    assert purged.daily_buckets == 0


# ─────────────── очередь доставки ───────────────


def test_delivered_notifications_do_not_live_forever() -> None:
    """Очередь — таблица, и доставленное сообщение нужно ровно до доставки."""
    notification(LONG_AGO, DeliveryStatus.SENT)
    notification(LONG_AGO, DeliveryStatus.FAILED)

    purged = purge_history(at=NOW)

    assert purged.notifications == 2


def test_pending_notification_is_never_purged() -> None:
    """Иначе уборка стала бы способом потерять уведомление."""
    notification(LONG_AGO, DeliveryStatus.PENDING)

    purged = purge_history(at=NOW)

    assert purged.notifications == 0
    assert Notification.objects.count() == 1


# ─────────────── расписание уборки ───────────────


def test_purge_runs_at_most_once_an_hour(domain: Domain) -> None:
    """Тик идёт раз в пять секунд: уборка на каждом — это холостые запросы в секунду."""
    event(domain, LONG_AGO)

    assert purge_due(at=NOW) is not None
    assert purge_due(at=NOW + PURGE_INTERVAL / 2) is None
    assert purge_due(at=NOW + PURGE_INTERVAL) is not None


def test_purge_moment_survives_a_restart(domain: Domain) -> None:
    """Момент лежит в базе, а не в памяти процесса."""
    purge_due(at=NOW)

    assert GlobalSettings.objects.get().last_purge_at == NOW


def test_11_3_deleting_a_domain_takes_its_journal_and_registry(domain: Domain) -> None:
    """11.3 прямо: «удаление домена удаляет его реестр и события»."""
    event(domain, RECENTLY)
    registry(domain, '10.20.3.7', NOW)

    domain.delete()

    assert Event.objects.count() == 0
    assert AddressRegistryEntry.objects.count() == 0
