"""
Правка домена — ТЗ 4.3, 6.3, 10.2; критерии 44, 45, 59.

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

import re
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
    """
    Не сохранено, ничего не записано.

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
    """
    Итог добавления.

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
    """
    Добавить один или несколько доменов — ТЗ 10.1, 10.2, 8.2; макет окна «Добавить домен».

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
    """
    Добавить домен — ТЗ 10.1, решение Q-E2.

    Имя приводится к нижнему регистру, конечная точка отбрасывается, не-ASCII переводится в
    punycode. Невозможность перевода — ошибка ввода с внятным текстом, а не исключение.

    **Расписание назначается сразу.** Домен с пустым `next_check_at` планировщик не заберёт
    никогда, и добавленный домен молча не проверялся бы. По Q-E2 один добавленный домен идёт в
    ближайший тик, а не ждёт случайного смещения: пик разбирается ограничением параллелизма, а
    со второй проверки домены расходятся сами — период отсчитывается от факта проверки.
    """
    normalized, reason = normalized_name(name)
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


#: Из чего состоит метка имени. Буквы, цифры, дефис — правило имени узла (RFC 1123); дефис не
#: может стоять с краю. Подчёркивание добавлено сверх него сознательно: в самом DNS оно
#: допустимо, а имена вида `_acme-challenge.example.com` — обычный CNAME, и запрещать их,
#: когда CNAME есть в списке типов записей (4.3), значило бы запрещать рабочий случай.
_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")

#: Длина метки. Ограничение самого DNS, не наше.
LABEL_MAX_LENGTH = 63

#: Признаки того, что вместо имени домена вставили ссылку. Проверяются **до** разбора, чтобы
#: сказать человеку, что именно не так: «недопустимый символ» в такой ситуации бесполезен —
#: он верен и ничего не объясняет.
_URL_MARKS = ("://", "/", "?", "#", "@", ":")


def normalized_name(name: str) -> tuple[str | None, str | None]:
    """
    Приведение имени по 10.1. Второе значение — причина отказа.

    Сверх 10.1 здесь проверяется **синтаксис имени**, и это не перестраховка. Кодек `idna`
    из стандартной библиотеки делает только нормализацию и проверку длины: он спокойно
    пропускает `https://www.jetbrains.com/`, `a.com:8080`, `<script>.com` и `-lead.com`.
    Такой домен заводится, попадает в расписание и потом молча не резолвится — ошибка ввода
    превращается в наблюдение за несуществующим именем (Q-D48).
    """
    stripped = name.strip().rstrip(".")
    if not stripped:
        return None, "Имя домена обязательно."
    if " " in stripped:
        return None, "В имени домена не бывает пробелов."
    if "." not in stripped:
        return None, "Имя домена состоит хотя бы из двух частей, разделённых точкой."

    url_hint = _url_refusal(stripped)
    if url_hint:
        return None, url_hint

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

    labels = converted.split(".")
    for label in labels:
        if len(label) > LABEL_MAX_LENGTH:
            return None, f"Часть имени «{label[:20]}…» длиннее {LABEL_MAX_LENGTH} символов."
        if not _LABEL.match(label):
            return None, (
                f"«{label}» не может быть частью имени домена. Части состоят из букв, цифр и "
                f"дефиса, и дефис не может стоять первым или последним."
            )

    # Числовая последняя часть означает, что ввели адрес, а не имя. Домен с таким окончанием
    # не существует — верхнего уровня из одних цифр в DNS нет.
    if labels[-1].isdigit():
        return None, (
            f"«{converted}» похоже на IP-адрес, а не на имя домена. Наблюдение ведётся за "
            f"именем: адреса — это то, что оно отдаёт."
        )

    return converted, None


def _url_refusal(value: str) -> str | None:
    """
    Ссылку или адрес почты вместо имени домена узнаём и говорим, что оставить.

    Отказ с подсказкой, а не молчаливое отсечение схемы и пути: 10.1 разрешает приводить имя
    только к нижнему регистру и отбрасывать конечную точку. Вырезать из ссылки узел значило бы
    завести домен, которого человек не вводил, — а вводят ссылку обычно не глядя, и заметить
    подмену будет негде.
    """
    if not any(mark in value for mark in _URL_MARKS):
        return None
    host = value.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    if host and "." in host:
        return f"Похоже на ссылку или адрес почты. Введите только имя домена: {host.lower()}"
    return "Похоже на ссылку. Введите только имя домена — без схемы, порта и пути."


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
    """
    Проверки, которые относятся к форме целиком, а не к строке белого списка.

    Требование 4.3: тип записи нужен хотя бы один. Инвариант держит и ограничение
    `domain_has_record_type` в базе, но пользователю нельзя показывать пятисотую там, где он
    просто снял обе галочки, — отказ обязан быть штатным (`CLAUDE.md`).
    """
    if not record_types:
        return ("Нужен хотя бы один тип записи: иначе домен не о чем спрашивать.",)
    return ()


@dataclass(frozen=True, slots=True)
class WatchSwitched:
    domain_id: int
    watch_enabled: bool


def switch_watch(
    *, domain_id: int, enabled: bool, at: datetime
) -> WatchSwitched | DomainNotCreated:
    """
    Приостановить или возобновить наблюдение — ТЗ 11.1, макет («Приостановить»).

    Выключенный домен не проверяется и не шлёт уведомлений, но реестр адресов сохраняется.

    При возобновлении назначается время проверки, если его нет: домен с пустым `next_check_at`
    планировщик не заберёт никогда, и возобновлённое наблюдение осталось бы наблюдением только
    на словах.

    События здесь не порождаются: `WATCH_STARTED` пишет `checking.py` на базовой проверке (7.4),
    а возобновлённый домен её и получит — пауза дольше порога актуальности делает проверку
    базовой (Q-D6).
    """
    domain = Domain.objects.filter(pk=domain_id).first()
    if domain is None:
        return DomainNotCreated(reason="Домен не найден.")

    fields: dict[str, object] = {"watch_enabled": enabled}
    if enabled and domain.next_check_at is None:
        fields["next_check_at"] = at
    Domain.objects.filter(pk=domain_id).update(**fields)

    return WatchSwitched(domain_id=domain_id, watch_enabled=enabled)


@dataclass(frozen=True, slots=True)
class DomainDeleted:
    name: str
    #: Сколько записей журнала уходит вместе с доменом. Человек должен знать цену до нажатия.
    events: int


def delete_domain(*, domain_id: int) -> DomainDeleted | DomainNotCreated:
    """
    Удалить домен вместе с его историей.

    Удаление необратимо и уносит реестр адресов, историю проверок, инциденты и записи журнала:
    по Q-D4 события без домена не существует, поэтому сохранить их отдельно нельзя. Отсюда и
    подтверждение на экране — решение принимает человек, зная цену.
    """
    domain = Domain.objects.filter(pk=domain_id).first()
    if domain is None:
        return DomainNotCreated(reason="Домен не найден.")

    name = domain.name
    events = domain.events.count()
    Domain.objects.filter(pk=domain_id).delete()
    return DomainDeleted(name=name, events=events)


def _replace_allowlist(domain_id: int, parsed: ParsedAllowlist) -> None:
    """
    Список заменяется целиком.

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
