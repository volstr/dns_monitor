"""Сроки хранения — ТЗ 11.3, plan.md этап 8.

11.3 называет два срока: журнал событий (настраивается, по умолчанию 90 суток) и реестр адресов
(настраивается, по умолчанию бессрочно). Оба хранятся в общих настройках и меняются в интерфейсе,
поэтому читаются на каждой уборке, а не при старте процесса.

Сверх двух названных сроков здесь убираются ещё две вещи, и обе — не расширение требования, а
его следствие:

- **Часовые корзины следа.** Модель `AddressObservationBucket` обещает это сама: «часовые корзины
  живут 30 суток, суточные — бессрочно». График за сутки и за неделю дальше этого срока часовые
  корзины не показывает, а таблица растёт с каждой проверкой каждого адреса.
- **Отработанные строки очереди доставки.** Очередь — таблица в PostgreSQL (`CLAUDE.md`), и
  доставленное сообщение живёт в ней вечно, хотя нужно ровно до доставки. Срок берётся тот же,
  что у журнала: строка очереди — это след того же события.

**Чего уборка не трогает.** Открытый инцидент и его события: инцидент, который держится дольше
срока хранения, — это не старьё, а незакрытая проблема, и стереть его историю значило бы стереть
ответ на вопрос «с какого момента». Строки очереди, которые ещё могут быть отправлены, тоже: у
них нет конечного состояния.

Запись реестра отбирается по `last_seen_at`, а не по `first_seen_at`. Адрес, который отдаётся
третий год, обязан остаться: «старая» у него только дата появления.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Model, QuerySet

from apps.accounts.models import AuditRecord
from apps.catalog.models import GlobalSettings
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    BucketSize,
    Event,
)
from apps.notifications.models import DeliveryStatus, Notification

#: Обещание модели `AddressObservationBucket`: часовые корзины живут 30 суток.
HOURLY_BUCKET_LIFETIME = timedelta(days=30)

#: Как часто уборка имеет смысл. Тик идёт раз в пять секунд, и гонять удаление на каждом — это
#: четыре холостых запроса в секунду ради работы, которая меняет что-то раз в сутки.
PURGE_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Purged:
    """Что удалено. Числа уходят в структурный лог: уборка обязана быть видимой."""

    events: int
    audit_records: int
    registry_entries: int
    hourly_buckets: int
    daily_buckets: int
    notifications: int

    @property
    def total(self) -> int:
        return (
            self.events
            + self.audit_records
            + self.registry_entries
            + self.hourly_buckets
            + self.daily_buckets
            + self.notifications
        )


def purge_due(*, at: datetime) -> Purged | None:
    """Убрать, если пришло время. ``None`` — уборка не понадобилась.

    Момент последней уборки лежит в общих настройках, а не в памяти процесса: перезапуск не
    должен ни пропускать уборку, ни запускать её заново каждые пять секунд.
    """
    settings_row = GlobalSettings.objects.get()
    last = settings_row.last_purge_at
    if last is not None and at - last < PURGE_INTERVAL:
        return None

    GlobalSettings.objects.filter(pk=settings_row.pk).update(last_purge_at=at)
    return purge_history(at=at)


def purge_history(*, at: datetime) -> Purged:
    """Удалить всё, что пережило свой срок — ТЗ 11.3."""
    settings_row = GlobalSettings.objects.get()
    journal_before = at - timedelta(seconds=settings_row.event_retention_seconds)
    registry_before = (
        at - timedelta(seconds=settings_row.registry_retention_seconds)
        if settings_row.registry_retention_seconds is not None
        else None
    )

    with transaction.atomic():
        return Purged(
            events=_purge_events(journal_before),
            audit_records=_deleted(AuditRecord.objects.filter(occurred_at__lt=journal_before)),
            registry_entries=_purge_registry(registry_before),
            hourly_buckets=_purge_buckets(BucketSize.HOUR, at - HOURLY_BUCKET_LIFETIME),
            daily_buckets=_purge_buckets(BucketSize.DAY, registry_before),
            notifications=_purge_notifications(journal_before),
        )


def _purge_events(before: datetime) -> int:
    """События журнала. События открытого инцидента остаются, сколько бы он ни держался."""
    stale = Event.objects.filter(occurred_at__lt=before).exclude(
        incident__isnull=False, incident__closed_at__isnull=True
    )
    return _deleted(stale)


def _purge_registry(before: datetime | None) -> int:
    """Записи реестра. ``None`` — «бессрочно», значение по умолчанию по 11.3."""
    if before is None:
        return 0
    return _deleted(AddressRegistryEntry.objects.filter(last_seen_at__lt=before))


def _purge_buckets(size: BucketSize, before: datetime | None) -> int:
    if before is None:
        return 0
    return _deleted(
        AddressObservationBucket.objects.filter(bucket_size=size.value, bucket_start__lt=before)
    )


def _purge_notifications(before: datetime) -> int:
    """Отработанные строки очереди: отправленные и те, по которым повторы исчерпаны.

    Ожидающие отправки не трогаются ни при каком сроке — иначе уборка стала бы способом
    потерять уведомление.
    """
    done = Notification.objects.filter(
        status__in=[DeliveryStatus.SENT, DeliveryStatus.FAILED], created_at__lt=before
    )
    return _deleted(done)


def _deleted(queryset: QuerySet[Model]) -> int:
    count, _ = queryset.delete()
    return int(count)
