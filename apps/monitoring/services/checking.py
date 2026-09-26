"""Одна проверка одного домена — ТЗ 6.1-6.4, 7.1, 7.3, 7.4.

Плановая и ручная проверка вызывают **эту** функцию: требование 11.6 «ручная проверка — настоящая
проверка» должно выполняться по построению, а не дублированием кода.

Момент времени передаётся явно, резолвер — портом. Благодаря этому критерии 6-9 прогоняются за
секунды: сутки модельного времени стоят одного вызова с другим `at`.

Решения, которые здесь закодированы:

- Q-D6 — проверка базовая, если успешных ещё не было или пауза дольше порога актуальности;
  `NO_RECORDS` успешной не считается;
- Q-D5 — несостоявшаяся проверка пишется в историю проверок и в лог, но не в журнал событий;
- Q-C10 — события до N неудач подряд нет;
- Q-13.2 — инцидент закрывается, когда несовпавшие адреса отсутствуют в K подряд ответах;
- Q-13.4 — адрес семейства, для которого записей нет, считается несовпавшим по общему правилу 6.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F

from apps.catalog.models import Domain, GlobalSettings
from apps.catalog.selectors.settings import effective_settings
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    BucketSize,
    Check,
    Event,
    EventType,
    Incident,
)
from apps.monitoring.selectors.addresses import IPV4, address_statuses, fresh_set
from core.ports.resolver import ResolveOutcome, Resolver, ResolveResult
from core.types.checking import AddressStatus, AddressStatusView, CheckOutcome
from core.types.dns import IPAddress, RecordType

logger = logging.getLogger(__name__)

#: K из Q-13.2: столько подряд ответов без адреса закрывают инцидент, не дожидаясь порога
#: актуальности. Константа, а не настройка продукта — в 4.1 её нет.
INCIDENT_CLOSING_MISSES = 3


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Что сделала проверка. Возвращается наружу вместо словаря или queryset."""

    outcome: CheckOutcome
    is_baseline: bool
    events: tuple[EventType, ...]
    statuses: tuple[AddressStatusView, ...]
    incident_opened: bool
    incident_closed: bool
    #: Идентификаторы созданных событий. По ним тик ставит уведомления в очередь — **после**
    #: фиксации этой транзакции: накопитель окна живёт в Redis и вместе с ней не откатится.
    event_ids: tuple[int, ...] = ()


@transaction.atomic
def run_check(*, domain_id: int, at: datetime, resolver: Resolver) -> CheckReport:
    # Без select_related("group"): группа обнуляемая, и внешнее соединение несовместимо с
    # FOR UPDATE в PostgreSQL. Наследование настроек и так идёт отдельным запросом.
    domain = Domain.objects.select_for_update().get(pk=domain_id)
    threshold = effective_settings(domain_id).freshness_threshold.value

    settings_row = GlobalSettings.objects.get()
    failure_threshold = settings_row.resolve_failure_threshold
    skip_ipv6 = not settings_row.check_ipv6

    record_types = frozenset(RecordType(kind) for kind in domain.record_types)
    if skip_ipv6:
        # Q-D27: снятый общий флаг главнее флага AAAA у домена (4.3). «Полностью игнорируем»
        # значит и не спрашивать: иначе сервер отвечал бы, а мы бы ответ выбрасывали.
        record_types -= {RecordType.AAAA}

    result = resolver.resolve(domain.name, record_types)

    if result.outcome is ResolveOutcome.UNAVAILABLE:
        return _not_performed(domain, at, result)

    # Считается до обновления `last_successful_check_at`, иначе базовой не окажется ни одна.
    is_baseline = _is_baseline(domain, at, threshold)

    if result.outcome is ResolveOutcome.NO_RECORDS:
        return _no_records(domain, at, result, failure_threshold)

    return _answered(
        domain, at, result, threshold, is_baseline=is_baseline, skip_ipv6=skip_ipv6
    )


def _not_performed(domain: Domain, at: datetime, result: ResolveResult) -> CheckReport:
    """Ни один сервер не ответил — 6.1: реестр и статусы не меняются, событий нет."""
    error = result.error or "ни один сервер не ответил"
    Check.objects.create(
        domain=domain, checked_at=at, outcome=CheckOutcome.NOT_PERFORMED, error=error
    )
    logger.warning(
        "проверка не состоялась", extra={"domain_id": domain.pk, "domain": domain.name}
    )
    return CheckReport(
        outcome=CheckOutcome.NOT_PERFORMED,
        is_baseline=False,
        events=(),
        statuses=(),
        incident_opened=False,
        incident_closed=False,
    )


def _no_records(
    domain: Domain, at: datetime, result: ResolveResult, failure_threshold: int
) -> CheckReport:
    """Домен не резолвится — это наблюдение (6.1). Реестр не трогаем."""
    domain.consecutive_resolve_failures += 1
    _remember_cname(domain, result)
    domain.save(update_fields=["consecutive_resolve_failures", "cname_target"])

    events: list[EventType] = []
    event_ids: list[int] = []
    # Ровно на N-й неудаче: повторно не порождается, пока не было успешного резолва (7.1).
    # До N событий нет вовсе — Q-C10.
    if domain.consecutive_resolve_failures == failure_threshold:
        event = _event(domain, at, EventType.RESOLVE_FAILED, {"attempts": failure_threshold})
        events.append(EventType.RESOLVE_FAILED)
        event_ids.append(event.pk)

    Check.objects.create(
        domain=domain,
        checked_at=at,
        outcome=CheckOutcome.RESOLVE_ERROR,
        answered_by=result.answered_by or "",
    )
    return CheckReport(
        outcome=CheckOutcome.RESOLVE_ERROR,
        is_baseline=False,
        events=tuple(events),
        statuses=(),
        incident_opened=False,
        incident_closed=False,
        event_ids=tuple(event_ids),
    )


def _answered(
    domain: Domain,
    at: datetime,
    result: ResolveResult,
    threshold: timedelta,
    *,
    is_baseline: bool,
    skip_ipv6: bool = False,
) -> CheckReport:
    events: list[EventType] = []
    event_ids: list[int] = []

    if domain.consecutive_resolve_failures > 0:
        events.append(EventType.RESOLVE_RECOVERED)
        event_ids.append(_event(domain, at, EventType.RESOLVE_RECOVERED, {}).pk)

    # Набор «до» берётся на момент **предыдущей** проверки, а не на текущий. По 7.1 адрес
    # пропал, если он входил в актуальный набор и в момент этой проверки пересёк границу
    # `T - W`. Если оба набора считать на одном `at`, состарившийся адрес выпадает сразу из
    # обоих, разность оказывается пустой и `ADDRESS_GONE` не возникает никогда.
    previous_at = domain.last_successful_check_at
    before_set: frozenset[IPAddress] = (
        fresh_set(domain.pk, previous_at, threshold, skip_ipv6=skip_ipv6)
        if previous_at
        else frozenset()
    )
    before_unmatched: frozenset[IPAddress] = (
        _unmatched(address_statuses(domain.pk, previous_at, threshold, skip_ipv6=skip_ipv6))
        if previous_at
        else frozenset()
    )

    # Q-D27: адрес IPv6 при снятом флаге не попадает даже в реестр — «его не существует».
    answer = _without_ipv6(result.answer) if skip_ipv6 else result.answer
    _write_registry(domain, at, answer)

    after_set = fresh_set(domain.pk, at, threshold, skip_ipv6=skip_ipv6)
    statuses = address_statuses(domain.pk, at, threshold, skip_ipv6=skip_ipv6)

    appeared = after_set - before_set
    gone = before_set - after_set

    if is_baseline:
        # 7.4: базовая проверка отправную точку ставит молча — событий изменения нет.
        started = _event(
            domain,
            at,
            EventType.WATCH_STARTED,
            {"address_count": len(after_set), "unmatched_count": len(_unmatched(statuses))},
        )
        events.append(EventType.WATCH_STARTED)
        event_ids.append(started.pk)
    else:
        for address in sorted(appeared, key=str):
            event = _event(domain, at, EventType.ADDRESS_APPEARED, {"addresses": [str(address)]})
            events.append(EventType.ADDRESS_APPEARED)
            event_ids.append(event.pk)
        for address in sorted(gone, key=str):
            event = _event(domain, at, EventType.ADDRESS_GONE, {"addresses": [str(address)]})
            events.append(EventType.ADDRESS_GONE)
            event_ids.append(event.pk)

    incident_opened, incident_closed, incident_events, incident_event_ids = _incidents(
        domain, at, statuses, before_unmatched
    )
    events.extend(incident_events)
    event_ids.extend(incident_event_ids)

    domain.consecutive_resolve_failures = 0
    domain.last_successful_check_at = at
    _remember_cname(domain, result)
    domain.save(
        update_fields=["consecutive_resolve_failures", "last_successful_check_at", "cname_target"]
    )

    outcome = _outcome(statuses, changed=bool(appeared or gone))
    Check.objects.create(
        domain=domain, checked_at=at, outcome=outcome, answered_by=result.answered_by or ""
    )
    return CheckReport(
        outcome=outcome,
        is_baseline=is_baseline,
        events=tuple(events),
        statuses=statuses,
        incident_opened=incident_opened,
        incident_closed=incident_closed,
        event_ids=tuple(event_ids),
    )


def _is_baseline(domain: Domain, at: datetime, threshold: timedelta) -> bool:
    """Q-D6: одно правило на все три случая 7.4."""
    last = domain.last_successful_check_at
    return last is None or at - last > threshold


def _write_registry(domain: Domain, at: datetime, answer: frozenset[IPAddress]) -> None:
    """Адреса ответа попадают в реестр **до** вычисления актуального набора (6.2)."""
    for address in answer:
        entry, created = AddressRegistryEntry.objects.get_or_create(
            domain=domain,
            address=address,
            defaults={"first_seen_at": at, "last_seen_at": at},
        )
        if not created:
            entry.last_seen_at = at
            entry.consecutive_misses = 0
            entry.save(update_fields=["last_seen_at", "consecutive_misses"])
        for size, start in _buckets(at):
            AddressObservationBucket.objects.get_or_create(
                domain=domain, address=address, bucket_size=size, bucket_start=start
            )

    # Q-13.2: счётчик пропусков растёт у тех, кого в этом ответе не было.
    AddressRegistryEntry.objects.filter(domain=domain).exclude(
        address__in=[str(address) for address in answer]
    ).update(consecutive_misses=F("consecutive_misses") + 1)


def _without_ipv6(answer: frozenset[IPAddress]) -> frozenset[IPAddress]:
    """Подстраховка к запрету запрашивать AAAA — решение Q-D27.

    Запрос AAAA уже не уходит, но ответ на A теоретически может принести что угодно, а обещано
    было «делаем вид, что адресов IPv6 не существует». Дешевле отфильтровать, чем однажды
    обнаружить такой адрес в реестре.
    """
    return frozenset(address for address in answer if address.version == IPV4)


def _buckets(at: datetime) -> tuple[tuple[str, datetime], ...]:
    """Часовая и суточная корзины наблюдения — Q-E1."""
    return (
        (BucketSize.HOUR.value, at.replace(minute=0, second=0, microsecond=0)),
        (BucketSize.DAY.value, at.replace(hour=0, minute=0, second=0, microsecond=0)),
    )


def _unmatched(statuses: tuple[AddressStatusView, ...]) -> frozenset[IPAddress]:
    return frozenset(view.address for view in statuses if view.status is AddressStatus.UNMATCHED)


def _incidents(
    domain: Domain,
    at: datetime,
    statuses: tuple[AddressStatusView, ...],
    before_unmatched: frozenset[IPAddress],
) -> tuple[bool, bool, list[EventType], list[int]]:
    """Инциденты и дедупликация — ТЗ 7.3 с быстрым закрытием из Q-13.2."""
    events: list[EventType] = []
    event_ids: list[int] = []
    unmatched = _unmatched(statuses)
    # Адрес, отсутствующий в K подряд ответах, инцидент больше не держит (Q-13.2).
    holding = {
        address
        for address in unmatched
        if _misses(domain, address) < INCIDENT_CLOSING_MISSES
    }
    incident = Incident.objects.filter(domain=domain, closed_at__isnull=True).first()

    if holding and incident is None:
        incident = Incident.objects.create(domain=domain, opened_at=at)
        opened = _event(
            domain, at, EventType.NOT_ALLOWED, {"addresses": _texts(holding)}, incident
        )
        return True, False, [EventType.NOT_ALLOWED], [opened.pk]

    if holding and incident is not None:
        # Инцидент не дублируется, но о новом несовпавшем адресе сообщается (7.3).
        appeared = holding - before_unmatched
        if appeared:
            event = _event(
                domain, at, EventType.NOT_ALLOWED, {"addresses": _texts(appeared)}, incident
            )
            events.append(EventType.NOT_ALLOWED)
            event_ids.append(event.pk)
        return False, False, events, event_ids

    if incident is not None:
        incident.closed_at = at
        incident.save(update_fields=["closed_at"])
        closed = _event(
            domain,
            at,
            EventType.BACK_TO_NORMAL,
            {"duration_seconds": int((at - incident.opened_at).total_seconds())},
            incident,
        )
        return False, True, [EventType.BACK_TO_NORMAL], [closed.pk]

    return False, False, events, event_ids


def _misses(domain: Domain, address: IPAddress) -> int:
    entry = AddressRegistryEntry.objects.filter(domain=domain, address=address).only(
        "consecutive_misses"
    ).first()
    return 0 if entry is None else entry.consecutive_misses


def _texts(addresses: frozenset[IPAddress] | set[IPAddress]) -> list[str]:
    return sorted(str(address) for address in addresses)


def _outcome(statuses: tuple[AddressStatusView, ...], *, changed: bool) -> CheckOutcome:
    """Цвет отметки в колонке «История» — ТЗ 11.5."""
    if _unmatched(statuses):
        return CheckOutcome.HAS_UNMATCHED
    if changed:
        return CheckOutcome.ADDRESS_SET_CHANGED
    return CheckOutcome.NORMAL


def _remember_cname(domain: Domain, result: ResolveResult) -> None:
    """Q-D12: цель CNAME показывается, только если флаг включён. Событий не порождает."""
    if RecordType.CNAME.value in domain.record_types:
        domain.cname_target = result.canonical_name or ""


def _event(
    domain: Domain,
    at: datetime,
    event_type: EventType,
    payload: dict[str, object],
    incident: Incident | None = None,
) -> Event:
    return Event.objects.create(
        domain=domain,
        event_type=event_type,
        occurred_at=at,
        payload=payload,
        incident=incident,
    )
