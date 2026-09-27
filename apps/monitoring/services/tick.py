"""
Один тик планировщика — ТЗ 11.1, plan.md этап 4.

Тик — обычная функция с явным моментом времени: её вызывают и `run_checker`, и тесты. Ради этого
момент передаётся параметром, а резолвер и хранилище состояния — портами.

Разделение двух механизмов взаимного исключения, которое легко перепутать:

- ``FOR UPDATE SKIP LOCKED`` в выборке снимает конкуренцию **экземпляров** сервиса: два процесса
  не заберут один домен (критерий 58);
- ключ ``sched:lock:domain:{id}`` снимает гонку **плановой проверки с ручной** (11.1, 11.6).

Падение одного домена не роняет тик: остальные домены обязаны быть проверены. В Sentry такие
падения уходят **одним событием на причину, а не одним на домен** (11.14, критерий 72): если
отказали все DNS-серверы, пятьсот доменов дают одно событие с числом затронутых, а не пятьсот
записей, за которыми не видно картины отказа. Поэтому отправка стоит не внутри обработчика
исключения, а в конце тика, когда известно, сколько доменов задел каждый отказ.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from django.db import connection

from apps.monitoring.services.checking import run_check
from apps.monitoring.services.scheduling import (
    ClaimedDomain,
    claim_due_domains,
    prune_check_history,
    reschedule_after_check,
)
from apps.notifications.services.dispatch import dispatch_event
from core.observability.logs import current_run, new_run
from core.observability.report import Failure, report
from core.ports.resolver import Resolver
from core.ports.state_store import StateStore, scheduler_lock

logger = logging.getLogger(__name__)

#: Сколько доменов забирать за один тик. Больше предела параллелизма смысла не имеет: они всё
#: равно будут ждать свободного потока, а резервирование тем временем расходуется.
CLAIM_FACTOR = 4


@dataclass(frozen=True, slots=True)
class TickReport:
    claimed: int
    checked: int
    #: Домен держал `sched:lock`: его уже проверяет кто-то другой (ручная проверка или сосед).
    skipped: int
    #: Проверка бросила исключение. Тик продолжается, домен вернётся по резервированию.
    failed: int


def tick(
    *,
    at: datetime,
    resolver: Resolver,
    state_store: StateStore,
    concurrency: int,
    limit: int | None = None,
) -> TickReport:
    # Обход получает идентификатор до первой записи в журнал: по нему потом собираются все
    # строки и все ошибки одного цикла проверок (11.14, «контекст события»).
    new_run()

    claimed = claim_due_domains(at=at, limit=limit or concurrency * CLAIM_FACTOR)
    if not claimed:
        return TickReport(claimed=0, checked=0, skipped=0, failed=0)

    failures: list[BaseException] = []
    if concurrency <= 1:
        outcomes = [_check_one(item, at, resolver, state_store, failures) for item in claimed]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(
                pool.map(
                    lambda item: _in_worker(item, at, resolver, state_store, failures), claimed
                )
            )

    report_failures(failures)
    return TickReport(
        claimed=len(claimed),
        checked=outcomes.count(_CHECKED),
        skipped=outcomes.count(_SKIPPED),
        failed=outcomes.count(_FAILED),
    )


_CHECKED = "checked"
_SKIPPED = "skipped"
_FAILED = "failed"


def report_failures(failures: list[BaseException]) -> None:
    """Одно событие на причину с числом затронутых доменов — ТЗ 11.14, критерий 72."""
    by_cause: dict[str, list[BaseException]] = defaultdict(list)
    for error in failures:
        by_cause[type(error).__name__].append(error)

    for cause, errors in by_cause.items():
        report(
            Failure(
                cause=f"check:{cause}",
                message=f"проверка домена упала: {cause}",
                affected=len(errors),
                tags={"run": current_run()},
                error=errors[0],
            )
        )


def _in_worker(
    item: ClaimedDomain,
    at: datetime,
    resolver: Resolver,
    state_store: StateStore,
    failures: list[BaseException],
) -> str:
    """Тот же путь, но в потоке: у потока своё соединение с БД, и его надо закрыть за собой."""
    try:
        return _check_one(item, at, resolver, state_store, failures)
    finally:
        connection.close()


def _check_one(
    item: ClaimedDomain,
    at: datetime,
    resolver: Resolver,
    state_store: StateStore,
    failures: list[BaseException],
) -> str:
    lock = scheduler_lock(item.domain_id)
    if not state_store.acquire(lock):
        # Домен уже проверяется — например, по кнопке «Проверить сейчас» (11.6).
        return _SKIPPED
    try:
        check = run_check(domain_id=item.domain_id, at=at, resolver=resolver)
        reschedule_after_check(domain_id=item.domain_id, at=at)
        prune_check_history(domain_id=item.domain_id)
        # Постановка в очередь идёт **после** проверки, а не внутри неё: `run_check` выполняется
        # в транзакции, а накопитель окна слияния живёт в Redis и вместе с ней не откатится.
        # Откат оставил бы счётчик завышенным, и порог слияния поехал бы (Q-D9).
        for event_id in check.event_ids:
            dispatch_event(event_id=event_id, at=at, state_store=state_store)
    except Exception as error:
        # Один домен не должен ронять обход. Резервирование вернёт его в выборку через полминуты.
        logger.exception("проверка домена упала", extra={"domain": item.name})
        # Список общий для потоков: `list.append` под GIL атомарен, отдельная блокировка здесь
        # была бы защитой от несуществующей гонки.
        failures.append(error)
        return _FAILED
    else:
        return _CHECKED
    finally:
        # Освобождается явно; срок жизни ключа — страховка на случай падения экземпляра (11.11).
        state_store.release(lock)
