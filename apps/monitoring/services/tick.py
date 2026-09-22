"""Один тик планировщика — ТЗ 11.1, plan.md этап 4.

Тик — обычная функция с явным моментом времени: её вызывают и `run_checker`, и тесты. Ради этого
момент передаётся параметром, а резолвер и хранилище состояния — портами.

Разделение двух механизмов взаимного исключения, которое легко перепутать:

- ``FOR UPDATE SKIP LOCKED`` в выборке снимает конкуренцию **экземпляров** сервиса: два процесса
  не заберут один домен (критерий 58);
- ключ ``sched:lock:domain:{id}`` снимает гонку **плановой проверки с ручной** (11.1, 11.6).

Падение одного домена не роняет тик: остальные домены обязаны быть проверены. TODO(ТЗ 11.14):
отправка таких исключений в Sentry с группировкой по причине — этап 8.
"""

from __future__ import annotations

import logging
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
    claimed = claim_due_domains(at=at, limit=limit or concurrency * CLAIM_FACTOR)
    if not claimed:
        return TickReport(claimed=0, checked=0, skipped=0, failed=0)

    if concurrency <= 1:
        outcomes = [_check_one(item, at, resolver, state_store) for item in claimed]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(
                pool.map(lambda item: _in_worker(item, at, resolver, state_store), claimed)
            )

    return TickReport(
        claimed=len(claimed),
        checked=outcomes.count(_CHECKED),
        skipped=outcomes.count(_SKIPPED),
        failed=outcomes.count(_FAILED),
    )


_CHECKED = "checked"
_SKIPPED = "skipped"
_FAILED = "failed"


def _in_worker(
    item: ClaimedDomain, at: datetime, resolver: Resolver, state_store: StateStore
) -> str:
    """Тот же путь, но в потоке: у потока своё соединение с БД, и его надо закрыть за собой."""
    try:
        return _check_one(item, at, resolver, state_store)
    finally:
        connection.close()


def _check_one(
    item: ClaimedDomain, at: datetime, resolver: Resolver, state_store: StateStore
) -> str:
    lock = scheduler_lock(item.domain_id)
    if not state_store.acquire(lock):
        # Домен уже проверяется — например, по кнопке «Проверить сейчас» (11.6).
        return _SKIPPED
    try:
        run_check(domain_id=item.domain_id, at=at, resolver=resolver)
        reschedule_after_check(domain_id=item.domain_id, at=at)
        prune_check_history(domain_id=item.domain_id)
    except Exception:
        # Один домен не должен ронять обход. Резервирование вернёт его в выборку через полминуты.
        logger.exception("проверка домена упала", extra={"domain": item.name})
        return _FAILED
    else:
        return _CHECKED
    finally:
        # Освобождается явно; срок жизни ключа — страховка на случай падения экземпляра (11.11).
        state_store.release(lock)
