"""
Ручная проверка домена и группы — ТЗ 11.6, Q-D18.

Резолв здесь **не выполняется**. По Q-D18 ручная проверка лишь ставит `next_check_at = now`, а
резолвит ближайший тик планировщика: синхронный путь занимал бы воркер gunicorn на время
«таймаут × число серверов», а на кнопке «проверить домены группы» — столько воркеров, сколько
доменов.

Отсюда же само собой выполняются два требования. Ручная проверка идёт через ту же `run_check`,
что и плановая (11.6 «ручная проверка — настоящая проверка»), и она сдвигает время следующей
плановой проверки: тик, разобрав домен, запишет `момент проверки + период группы` (критерий 48).

Глобальной кнопки «проверить все домены» нет и не будет — раздел 12.

Отказ — штатный исход и возвращается результатом с причиной, а не исключением (`CLAUDE.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.catalog.models import Domain
from core.ports.state_store import StateStore, domain_cooldown, group_cooldown

#: 11.6: запуск проверки группы больше этого числа доменов требует подтверждения.
CONFIRMATION_THRESHOLD = 200


class RejectionReason(StrEnum):
    #: Кулдаун ещё не истёк: 30 секунд у домена, 60 у группы (11.11).
    COOLDOWN = 'cooldown'
    #: Доменов в группе больше порога — нужно подтверждение (11.6).
    NEEDS_CONFIRMATION = 'needs_confirmation'
    #: Проверять нечего: домен выключен или в группе нет включённых доменов (11.1).
    NOTHING_TO_CHECK = 'nothing_to_check'


@dataclass(frozen=True, slots=True)
class CheckRequested:
    """Домены поставлены в очередь: их разберёт ближайший тик."""

    domains: int


@dataclass(frozen=True, slots=True)
class CheckRejected:
    reason: RejectionReason
    #: Сколько доменов затронет запуск. Нужно тексту подтверждения (11.6).
    domains: int = 0


def request_domain_check(
    *, domain_id: int, at: datetime, state_store: StateStore
) -> CheckRequested | CheckRejected:
    """«Проверить сейчас» на карточке домена. Кулдаун 30 секунд."""
    domain = Domain.objects.filter(pk=domain_id, watch_enabled=True).first()
    if domain is None:
        # Выключенный домен не проверяется, но его реестр сохраняется (11.1).
        return CheckRejected(reason=RejectionReason.NOTHING_TO_CHECK)

    if not state_store.acquire(domain_cooldown(domain_id)):
        return CheckRejected(reason=RejectionReason.COOLDOWN, domains=1)

    Domain.objects.filter(pk=domain_id).update(next_check_at=at)
    return CheckRequested(domains=1)


def request_group_check(
    *, group_id: int, at: datetime, state_store: StateStore, confirmed: bool = False
) -> CheckRequested | CheckRejected:
    """«Проверить домены группы» на экране группы. Кулдаун 60 секунд."""
    domains = Domain.objects.filter(group_id=group_id, watch_enabled=True)
    count = domains.count()
    if count == 0:
        return CheckRejected(reason=RejectionReason.NOTHING_TO_CHECK)

    if count > CONFIRMATION_THRESHOLD and not confirmed:
        # Кулдаун не расходуется: подтверждение — это ещё не запуск.
        return CheckRejected(reason=RejectionReason.NEEDS_CONFIRMATION, domains=count)

    if not state_store.acquire(group_cooldown(group_id)):
        return CheckRejected(reason=RejectionReason.COOLDOWN, domains=count)

    domains.update(next_check_at=at)
    return CheckRequested(domains=count)
