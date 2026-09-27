"""
Тестовый резолвер: заранее заданная последовательность ответов на каждое имя.

Последовательность повторяется по кругу, поэтому сценарий «домен отдаёт по кругу два адреса из
трёх» (критерий 6) — это список из трёх результатов плюс подкрученные часы, а не хитрая
подстановка. ``mock.patch`` по путям импорта не нужен и не используется: зависимость вынесена
в порт.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence

from core.ports.resolver import ResolveOutcome, Resolver, ResolveResult
from core.types.dns import RecordType


def answered(
    *addresses: str, canonical_name: str | None = None, answered_by: str | None = None
) -> ResolveResult:
    return ResolveResult(
        outcome=ResolveOutcome.ANSWERED,
        answer=frozenset(ipaddress.ip_address(address) for address in addresses),
        canonical_name=canonical_name,
        answered_by=answered_by,
    )


def no_records(answered_by: str | None = None) -> ResolveResult:
    return ResolveResult(outcome=ResolveOutcome.NO_RECORDS, answered_by=answered_by)


def unavailable(error: str = "ни один сервер не ответил") -> ResolveResult:
    return ResolveResult(outcome=ResolveOutcome.UNAVAILABLE, error=error)


class FakeResolver(Resolver):
    def __init__(self, answers: Mapping[str, Sequence[ResolveResult]] | None = None) -> None:
        self._answers: dict[str, list[ResolveResult]] = {}
        self._position: dict[str, int] = {}
        #: Что и с какими типами записей спрашивали — для проверок в тестах.
        self.calls: list[tuple[str, frozenset[RecordType]]] = []
        for name, results in (answers or {}).items():
            self.set(name, results)

    def set(self, name: str, results: Sequence[ResolveResult]) -> None:
        """Задать последовательность ответов для имени. Повторяется по кругу."""
        if not results:
            message = f"Для имени {name} нужен хотя бы один ответ"
            raise ValueError(message)
        self._answers[name] = list(results)
        self._position[name] = 0

    def resolve(self, name: str, record_types: frozenset[RecordType]) -> ResolveResult:
        self.calls.append((name, record_types))
        results = self._answers.get(name)
        if results is None:
            # Молчаливый UNAVAILABLE спрятал бы опечатку в имени и сделал бы тест зелёным зря.
            message = f"Для имени {name} ответы не заданы"
            raise LookupError(message)
        position = self._position[name]
        self._position[name] = (position + 1) % len(results)
        return results[position]
