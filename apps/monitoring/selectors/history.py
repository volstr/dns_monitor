"""
Колонка «История» в списке доменов — ТЗ 11.5.

«20 последних проверок домена, слева направо. Ось — последовательность проверок, а не время.
Пустая отметка означает, что проверки не было» — подпись прототипа, она же требование 11.5.

Колонка рисуется серверным SVG-подобной разметкой из `<i>`, клиентский код ей не нужен
(`CLAUDE.md`). Здесь готовятся только отметки: шаблон ничего не вычисляет.

**Один запрос на страницу, а не на домен.** Оконная функция нумерует проверки внутри каждого
домена и отсекает всё, что дальше двадцатой. Иначе список из полусотни доменов дал бы полсотни
запросов, а при хранении до 220 проверок на домен (Q-D23) — ещё и лишние тысячи строк.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.db.models import F, Window
from django.db.models.functions import RowNumber

from apps.monitoring.models import Check
from core.types.checking import CheckOutcome

#: 11.5: ровно двадцать отметок.
HISTORY_LENGTH = 20

logger = logging.getLogger(__name__)


class HistoryMark(StrEnum):
    """Класс отметки. Значения — классы из CSS прототипа, `.tape i` без класса — пустая."""

    NONE = ""
    OK = "e-ok"
    CHANGE = "e-change"
    BREACH = "e-breach"
    #: Q-D37: проверка прошла, но ни один адрес не подтверждён белым списком.
    NOT_CONFIRMED = "e-unconfirmed"
    FAIL = "e-fail"


#: Исход проверки → цвет отметки. `NOT_PERFORMED` даёт пустую: проверки не было (Q-D5).
MARK_OF_OUTCOME = {
    CheckOutcome.NORMAL: HistoryMark.OK,
    CheckOutcome.ADDRESS_SET_CHANGED: HistoryMark.CHANGE,
    CheckOutcome.HAS_UNMATCHED: HistoryMark.BREACH,
    CheckOutcome.NOT_CONFIRMED: HistoryMark.NOT_CONFIRMED,
    CheckOutcome.RESOLVE_ERROR: HistoryMark.FAIL,
    CheckOutcome.NOT_PERFORMED: HistoryMark.NONE,
}


@dataclass(frozen=True, slots=True)
class DomainHistory:
    """Готовая лента отметок и момент последней проверки."""

    #: Ровно `HISTORY_LENGTH` отметок, слева направо от старых к новым. Недостающие слева пустые.
    marks: tuple[HistoryMark, ...]
    last_checked_at: datetime | None


#: Лента домена, который ещё ни разу не проверялся: двадцать пустых отметок (11.5).
EMPTY_HISTORY = DomainHistory(marks=(HistoryMark.NONE,) * HISTORY_LENGTH, last_checked_at=None)


def history_of_domains(domain_ids: Sequence[int]) -> dict[int, DomainHistory]:
    """Ленты отметок для перечисленных доменов. Домен без проверок получает пустую ленту."""
    if not domain_ids:
        return {}

    numbered = (
        Check.objects.filter(domain_id__in=domain_ids)
        .annotate(
            position=Window(
                RowNumber(),
                partition_by=F("domain_id"),
                order_by=F("checked_at").desc(),
            )
        )
        .filter(position__lte=HISTORY_LENGTH)
        .values("domain_id", "checked_at", "outcome")
        .order_by("domain_id", "checked_at")
    )

    collected: dict[int, list[tuple[datetime, HistoryMark]]] = {}
    for row in numbered:
        collected.setdefault(row["domain_id"], []).append(
            (row["checked_at"], _mark(row["outcome"]))
        )

    return {
        domain_id: _padded(rows) for domain_id, rows in collected.items()
    }


def _mark(outcome: str) -> HistoryMark:
    """
    Отметка по исходу. Незнакомое значение даёт пустую, а не роняет экран.

    Строка приходит из базы и может быть записана прежней версией кода: долгоживущие команды
    `run_checker` и `run_notifier` не перезагружаются сами, и после переименования исхода они
    ещё какое-то время пишут старое значение — в том числе **после** миграции, которая перенесла
    прежние строки. Один такой ряд валил весь список доменов пятисотой ошибкой.

    Показать пустую отметку — честнее, чем упасть: список доменов главный экран продукта, и
    цена незнакомой строки не должна быть такой.
    """
    try:
        return MARK_OF_OUTCOME[CheckOutcome(outcome)]
    except ValueError:
        logger.warning("неизвестный исход проверки", extra={"outcome": outcome})
        return HistoryMark.NONE


def _padded(rows: list[tuple[datetime, HistoryMark]]) -> DomainHistory:
    """Дополнение слева: лента всегда одной длины, иначе колонка «поехала» бы по ширине."""
    marks = tuple(mark for _, mark in rows)
    missing = HISTORY_LENGTH - len(marks)
    return DomainHistory(
        marks=(HistoryMark.NONE,) * missing + marks,
        last_checked_at=rows[-1][0] if rows else None,
    )
