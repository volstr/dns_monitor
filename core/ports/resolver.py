"""
Порт резолва — ТЗ 6.1.

Три исхода различаются **типом**, а не дисциплиной вызывающего кода: требование 6.1 отличать
«домен не резолвится» от «мы не смогли спросить» — ключевое, потому что второе не должно
порождать ни событий, ни уведомлений, ни изменений реестра.

Перебор серверов, таймаут на сервер и разбор ошибок живут внутри адаптера: доменный код не знает,
что серверов несколько.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from core.types.dns import IPAddress, RecordType


class ResolveOutcome(StrEnum):
    """
    Исход резолва — ТЗ 6.1. Имя по Q-E3: не просто ``Outcome``, чтобы не путать с исходом
    проверки в колонке «История»."""

    #: Есть записи.
    ANSWERED = 'answered'
    #: NXDOMAIN или NOERROR без записей — это результат наблюдения, а не ошибка сети (6.1).
    NO_RECORDS = 'no_records'
    #: Ни один сервер не ответил. Реестр и статусы не трогаются, событий нет (6.1).
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True, slots=True)
class ResolveResult:
    outcome: ResolveOutcome
    #: Множество адресов из одного ответа — термин «Ответ» раздела 2 (Q-E3).
    answer: frozenset[IPAddress] = field(default_factory=frozenset)
    #: Цель цепочки CNAME, если она пройдена (Q-D12). Приходит вместе с ответом, даром.
    canonical_name: str | None = None
    #: Какой сервер ответил — нужен критерию 13 и разбору инцидентов.
    answered_by: str | None = None
    #: Причина недоступности. Только для UNAVAILABLE, уходит в структурный лог (Q-D5).
    error: str | None = None


class Resolver(Protocol):
    def resolve(self, name: str, record_types: frozenset[RecordType]) -> ResolveResult:
        """Один резолв одного имени. Исключений не бросает: отказ — штатный исход."""
        ...
