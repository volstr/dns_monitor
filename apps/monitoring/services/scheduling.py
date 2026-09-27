"""Выборка наступивших доменов, перепланирование и чистка истории — ТЗ 11.1, Q-D23.

Расписание живёт **только** в ``Domain.next_check_at``: второго места нет, периодических задач на
домен нет (`CLAUDE.md`).

Тик забирает домены одним атомарным запросом с ``FOR UPDATE SKIP LOCKED`` и сразу сдвигает
``next_check_at`` на короткое резервирование. Смысл резервирования: упавший посреди проверки
процесс не подвешивает домен навсегда — тот вернётся в выборку сам через полминуты. Настоящее
время следующей проверки пишется уже по её завершении.

``SKIP LOCKED`` снимает конкуренцию **экземпляров** сервиса (критерий 58). Ключ
``sched:lock:domain:{id}`` нужен для другого — гонки плановой проверки с ручной (11.1, 11.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction

from apps.catalog.models import Domain
from apps.catalog.selectors.settings import effective_settings
from apps.monitoring.models import Check
from core.types.checking import CheckOutcome

#: Короткое резервирование из plan.md: столько домен считается «в работе» до записи настоящего
#: времени следующей проверки.
RESERVATION = timedelta(seconds=30)

#: Q-D23: сколько проверок на домен переживает чистку.
SUCCESSFUL_HISTORY = 20
FAILED_HISTORY = 200

#: Проверка состоялась и дала результат.
SUCCESSFUL_OUTCOMES = (
    CheckOutcome.NORMAL.value,
    CheckOutcome.ADDRESS_SET_CHANGED.value,
    CheckOutcome.HAS_UNMATCHED.value,
    CheckOutcome.NOT_CONFIRMED.value,
)
#: Домен не отрезолвился или спросить не удалось — это и есть материал для разбора.
FAILED_OUTCOMES = (CheckOutcome.RESOLVE_ERROR.value, CheckOutcome.NOT_PERFORMED.value)


@dataclass(frozen=True, slots=True)
class ClaimedDomain:
    domain_id: int
    name: str


def claim_due_domains(*, at: datetime, limit: int) -> tuple[ClaimedDomain, ...]:
    """Забрать наступившие домены и зарезервировать их за собой.

    Выключенный домен не проверяется, но его реестр сохраняется (11.1), поэтому он просто не
    попадает в выборку. Домен без расписания (``next_check_at is NULL``) тоже: его время ещё не
    назначено.
    """
    with transaction.atomic():
        rows = list(
            Domain.objects.filter(watch_enabled=True, next_check_at__lte=at)
            .order_by("next_check_at")
            .select_for_update(skip_locked=True)
            .values_list("pk", "name")[:limit]
        )
        if rows:
            Domain.objects.filter(pk__in=[pk for pk, _ in rows]).update(
                next_check_at=at + RESERVATION
            )
    return tuple(ClaimedDomain(domain_id=pk, name=name) for pk, name in rows)


def reschedule_after_check(*, domain_id: int, at: datetime) -> datetime:
    """Настоящее время следующей проверки — момент проверки плюс период группы (11.1).

    Период берётся по цепочке наследования: у домена своего периода нет и быть не может (4.3, 12).
    """
    period = effective_settings(domain_id).check_period.value
    next_check_at = at + period
    Domain.objects.filter(pk=domain_id).update(next_check_at=next_check_at)
    return next_check_at


def prune_check_history(*, domain_id: int) -> int:
    """Оставить 20 последних успешных проверок и 200 последних неудачных — Q-D23.

    Двадцать — ровно столько, сколько требует колонка «История» (11.5). Поскольку неудачные
    хранятся сверх этого, последние двадцать проверок **любого рода** всегда на месте.

    Возвращает число удалённых строк.
    """
    keep: list[int] = []
    for outcomes, limit in (
        (SUCCESSFUL_OUTCOMES, SUCCESSFUL_HISTORY),
        (FAILED_OUTCOMES, FAILED_HISTORY),
    ):
        keep.extend(
            Check.objects.filter(domain_id=domain_id, outcome__in=outcomes)
            .order_by("-checked_at")
            .values_list("pk", flat=True)[:limit]
        )
    deleted, _ = Check.objects.filter(domain_id=domain_id).exclude(pk__in=keep).delete()
    return deleted
