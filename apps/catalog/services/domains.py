"""Правка домена — ТЗ 4.3, 6.3, 10.2; критерии 44, 45, 59.

Отказ — штатный исход: разобранный список возвращается результатом с причинами по строкам, а не
исключением (`CLAUDE.md`). Сохранение блокируется целиком, пока есть хотя бы одна ошибочная
строка (10.2), и корректные строки при этом не теряются — они возвращаются вместе с ошибками и
попадают обратно в поле.

Событие о включении наблюдения здесь не порождается намеренно. `WATCH_STARTED` уже пишет
`checking.py` на базовой проверке (7.4), а включённый домен ближайшим тиком как раз её и получит:
пауза дольше порога актуальности делает проверку базовой (Q-D6). Второе событие о том же было бы
дублем.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from apps.catalog.models import AllowlistEntry, Domain
from apps.catalog.services.allowlist import ParsedAllowlist, parse_allowlist


@dataclass(frozen=True, slots=True)
class DomainSaved:
    """Сохранено. ``normalized`` — записи, которые пришлось привести (Q-13.3)."""

    domain_id: int
    normalized: tuple[tuple[str, str], ...]
    parsed: ParsedAllowlist


@dataclass(frozen=True, slots=True)
class DomainNotSaved:
    """Не сохранено, ничего не записано.

    ``reasons`` — причины, не привязанные к строке списка. Сейчас такая одна: без единого типа
    записи домен нечего спрашивать, и это не ошибка ввода в поле, а ошибка всей формы.
    """

    parsed: ParsedAllowlist
    reasons: tuple[str, ...] = ()


def save_domain(
    *,
    domain_id: int,
    comment: str,
    record_types: tuple[str, ...],
    allowlist_text: str,
    watch_enabled: bool,
) -> DomainSaved | DomainNotSaved:
    """Сохранить правки карточки домена."""
    parsed = parse_allowlist(allowlist_text, record_types)
    reasons = _form_reasons(record_types)
    if not parsed.can_save or reasons:
        # Отказ целиком: писать половину правок хуже, чем не писать ничего.
        return DomainNotSaved(parsed=parsed, reasons=reasons)

    with transaction.atomic():
        Domain.objects.filter(pk=domain_id).update(
            comment=comment,
            record_types=list(record_types),
            watch_enabled=watch_enabled,
        )
        _replace_allowlist(domain_id, parsed)

    return DomainSaved(
        domain_id=domain_id,
        normalized=tuple(
            (line.normalized_from, line.raw)
            for line in parsed.entries
            if line.normalized_from is not None
        ),
        parsed=parsed,
    )


def _form_reasons(record_types: tuple[str, ...]) -> tuple[str, ...]:
    """Проверки, которые относятся к форме целиком, а не к строке белого списка.

    Требование 4.3: тип записи нужен хотя бы один. Инвариант держит и ограничение
    `domain_has_record_type` в базе, но пользователю нельзя показывать пятисотую там, где он
    просто снял обе галочки, — отказ обязан быть штатным (`CLAUDE.md`).
    """
    if not record_types:
        return ("Нужен хотя бы один тип записи: иначе домен не о чем спрашивать.",)
    return ()


def _replace_allowlist(domain_id: int, parsed: ParsedAllowlist) -> None:
    """Список заменяется целиком.

    Сравнивать построчно нечего: записи не имеют собственной истории, ссылок на них нет, а
    порядок ввода — часть того, что видит человек. Замена внутри транзакции, поэтому проверка,
    идущая параллельно, увидит либо старый список, либо новый, но не половину (критерий 59).
    """
    AllowlistEntry.objects.filter(domain_id=domain_id).delete()
    AllowlistEntry.objects.bulk_create(
        [
            AllowlistEntry(domain_id=domain_id, entry=line.network, raw=line.raw)
            for line in parsed.entries
        ]
    )
