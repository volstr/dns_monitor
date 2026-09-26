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
from datetime import datetime

from django.db import transaction

from apps.catalog.models import (
    DOMAIN_NAME_MAX_LENGTH,
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.catalog.selectors.settings import settings_of_group
from apps.catalog.services.allowlist import ParsedAllowlist, parse_allowlist


@dataclass(frozen=True, slots=True)
class DomainCreated:
    domain_id: int
    name: str
    #: Имя после приведения к punycode, если ввод был не-ASCII (10.1). Показывается человеку.
    converted_from: str | None


@dataclass(frozen=True, slots=True)
class DomainNotCreated:
    reason: str


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


@dataclass(frozen=True, slots=True)
class AddedLine:
    """Одна строка поля «Доменные имена». Ошибка в одной не отменяет остальных."""

    text: str
    name: str | None
    converted_from: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class DomainsAdded:
    """Итог добавления.

    Разбор списка адресов приходит целиком: экран показывает причины по строкам.
    """

    lines: tuple[AddedLine, ...]
    allowlist: ParsedAllowlist
    #: Ловушка 8.2: пустой список в режиме «При выходе из разрешённых» не пришлёт уведомление
    #: никогда, и ТЗ требует предупредить об этом при сохранении. Пусто — предупреждать не о чем.
    trap_warning: str

    @property
    def created(self) -> tuple[AddedLine, ...]:
        return tuple(line for line in self.lines if line.name is not None)

    @property
    def refused(self) -> tuple[AddedLine, ...]:
        return tuple(line for line in self.lines if line.reason is not None)


@dataclass(frozen=True, slots=True)
class DomainsNotAdded:
    """Ничего не добавлено: список адресов содержит ошибочные строки (10.2)."""

    allowlist: ParsedAllowlist


def add_domains(
    *, names: str, group_id: int | None, comment: str, allowlist_text: str, at: datetime
) -> DomainsAdded | DomainsNotAdded:
    """Добавить один или несколько доменов — ТЗ 10.1, 10.2, 8.2; макет окна «Добавить домен».

    Имена принимаются по одному в строке: так их и вводят в макете. Ошибка в одной строке не
    отменяет остальных — иначе одна опечатка в списке из двадцати имён заставляла бы набирать
    всё заново.

    Белый список — один на всё добавление и необязателен («можно заполнить позже, когда станет
    понятно, куда домен резолвится»). Но **ошибочная строка в нём блокирует всё**: по 10.2
    сохранение с ошибкой в списке недопустимо, а молча добавить домены без списка значило бы
    сделать не то, о чём просили.
    """
    parsed = parse_allowlist(allowlist_text)
    if not parsed.can_save:
        return DomainsNotAdded(allowlist=parsed)

    lines: list[AddedLine] = []
    for raw in names.splitlines():
        text = raw.strip()
        if not text:
            continue
        lines.append(_added_line(text, group_id, comment, at))

    if parsed.entries:
        for line in lines:
            if line.name is not None:
                _replace_allowlist(Domain.objects.get(name=line.name).pk, parsed)

    return DomainsAdded(
        lines=tuple(lines),
        allowlist=parsed,
        trap_warning=_trap_warning(parsed, group_id, lines),
    )


def _added_line(
    text: str, group_id: int | None, comment: str, at: datetime
) -> AddedLine:
    result = create_domain(name=text, group_id=group_id, comment=comment, at=at)
    if isinstance(result, DomainNotCreated):
        return AddedLine(text=text, name=None, converted_from=None, reason=result.reason)
    return AddedLine(
        text=text, name=result.name, converted_from=result.converted_from, reason=None
    )


def _trap_warning(
    parsed: ParsedAllowlist, group_id: int | None, lines: list[AddedLine]
) -> str:
    """ТЗ 8.2: предупредить, если добавленный домен не пришлёт уведомление никогда."""
    if parsed.entries or not any(line.name for line in lines):
        return ""

    group = Group.objects.filter(pk=group_id).first() if group_id else None
    effective = settings_of_group(group, GlobalSettings.objects.get())
    if effective.notification_mode.value != NotificationMode.NOT_ALLOWED:
        return ""
    return (
        "Разрешённые адреса не заданы, а режим уведомлений — «При выходе из разрешённых». "
        "По такому домену уведомлений не будет никогда: сравнивать не с чем. Задайте список "
        "на карточке домена."
    )


def create_domain(
    *, name: str, group_id: int | None, comment: str, at: datetime
) -> DomainCreated | DomainNotCreated:
    """Добавить домен — ТЗ 10.1, решение Q-E2.

    Имя приводится к нижнему регистру, конечная точка отбрасывается, не-ASCII переводится в
    punycode. Невозможность перевода — ошибка ввода с внятным текстом, а не исключение.

    **Расписание назначается сразу.** Домен с пустым `next_check_at` планировщик не заберёт
    никогда, и добавленный домен молча не проверялся бы. По Q-E2 один добавленный домен идёт в
    ближайший тик, а не ждёт случайного смещения: пик разбирается ограничением параллелизма, а
    со второй проверки домены расходятся сами — период отсчитывается от факта проверки.
    """
    normalized, reason = _normalized_name(name)
    if normalized is None:
        return DomainNotCreated(reason=reason or "Имя домена не подходит.")

    if Domain.objects.filter(name=normalized).exists():
        # 10.1: дубликаты внутри установки не допускаются.
        return DomainNotCreated(reason=f"Домен {normalized} уже есть в списке.")

    domain = Domain.objects.create(
        name=normalized,
        group_id=group_id,
        comment=comment.strip(),
        next_check_at=at,
    )
    stripped = name.strip().rstrip(".")
    return DomainCreated(
        domain_id=domain.pk,
        name=normalized,
        converted_from=stripped if stripped.lower() != normalized else None,
    )


def _normalized_name(name: str) -> tuple[str | None, str | None]:
    """Приведение имени по 10.1. Второе значение — причина отказа."""
    stripped = name.strip().rstrip(".")
    if not stripped:
        return None, "Имя домена обязательно."
    if " " in stripped:
        return None, "В имени домена не бывает пробелов."
    if "." not in stripped:
        return None, "Имя домена состоит хотя бы из двух частей, разделённых точкой."

    # Нижний регистр — явно, а не кодеком: `str.encode("idna")` пропускает нормализацию для
    # меток, которые уже ASCII, и «PAY-GW.contoso.ru» осталось бы с заглавными. Тогда 10.1
    # нарушалось бы дважды: имя не приведено и дубликат не распознан.
    lowered = stripped.lower()
    try:
        converted = lowered.encode("idna").decode("ascii")
    except UnicodeError:
        return None, (
            f"«{stripped}» не удаётся перевести в punycode. Проверьте, нет ли в имени "
            f"недопустимых символов и не длиннее ли часть имени 63 знаков."
        )


    if len(converted) > DOMAIN_NAME_MAX_LENGTH:
        return None, f"Имя домена длиннее {DOMAIN_NAME_MAX_LENGTH} символов."
    return converted, None


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
