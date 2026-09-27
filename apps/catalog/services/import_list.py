"""
Импорт списком — ТЗ 10.3, 10.1, 10.2; критерии 25, 26, 27.

«Импорт **не выполняется вслепую**»: сначала разбор с построчной таблицей и сводкой, и только
потом подтверждение. Поэтому здесь две операции — `preview_import` ничего не меняет, а
`apply_import` применяет ровно то, что было показано.

Формат строки из 10.3: ``домен; разрешённые записи через пробел; комментарий``. Второе и третье
поля необязательны.

Разбор имени и белого списка не дублируется: имя идёт через ту же нормализацию, что и одиночное
добавление (10.1, punycode), список — через тот же разбор, что и поле на карточке (10.2). Вторая
реализация здесь означала бы, что импорт однажды примет то, что форма отвергает.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group
from apps.catalog.selectors.settings import settings_of_group
from apps.catalog.services.allowlist import ParsedAllowlist, parse_allowlist
from apps.catalog.services.domains import normalized_name

#: 10.3: поля строки разделяются точкой с запятой.
FIELD_SEPARATOR = ";"

#: Сколько строк разбираем за раз. Вставка на миллион строк — не рабочий случай, а способ
#: положить процесс; предел назван явно, чтобы отказ был внятным.
MAX_LINES = 5000


@dataclass(frozen=True, slots=True)
class ImportLine:
    """Одна строка вставки после разбора."""

    number: int
    text: str
    name: str | None
    #: Имя до перевода в punycode, если перевод был (10.1, критерий 27).
    converted_from: str | None
    comment: str
    allowlist: ParsedAllowlist
    #: Домен с таким именем уже есть. Что с ним делать, решает флаг обновления (критерий 26).
    exists: bool
    #: Группа, в которой домен уже лежит. Пусто, если домена ещё нет.
    exists_in: str
    error: str | None

    @property
    def warning(self) -> str:
        """10.3: домен без разрешённых записей принимается, но помечается предупреждением."""
        if self.error or self.allowlist.entries:
            return ""
        return "разрешённые записи не заданы — следить получится только за изменением набора"


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """Шаг разбора. Ничего не изменено — это только показ."""

    lines: tuple[ImportLine, ...]
    #: Строки, которые не разобрались. Возвращаются в поле ввода (критерий 25).
    failed_text: str
    #: Куда попадут новые домены. Группа ещё не создана — это только подпись.
    group_name: str

    @property
    def to_add(self) -> int:
        return sum(1 for line in self.lines if not line.error and not line.exists)

    @property
    def already_here(self) -> int:
        return sum(1 for line in self.lines if not line.error and line.exists)

    @property
    def broken(self) -> int:
        return sum(1 for line in self.lines if line.error)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Что сделано подтверждением."""

    added: int
    updated: int
    skipped: int
    #: Ошибочные строки возвращаются в поле, чтобы их исправили и повторили (критерий 25).
    failed_text: str
    group_name: str


def preview_import(
    *, text: str, group_id: int | None = None, new_group_name: str = ""
) -> ImportPreview:
    """
    Разобрать вставку, ничего не меняя — ТЗ 10.3.

    Названная в поле новая группа здесь **не создаётся**: разбор — это показ, и отменённый
    импорт не должен оставлять за собой пустую группу. Имя нужно только подписи.
    """
    lines: list[ImportLine] = []
    for number, raw in enumerate(text.splitlines()[:MAX_LINES], start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        lines.append(_line(number, stripped))

    return ImportPreview(
        lines=tuple(lines),
        failed_text=_failed_text(lines),
        group_name=_group_name(group_id, new_group_name),
    )


def _group_name(group_id: int | None, new_group_name: str) -> str:
    if new_group_name.strip():
        return new_group_name.strip()
    group = Group.objects.filter(pk=group_id).first() if group_id else None
    return group.name if group else "Без группы"


def apply_import(
    *,
    text: str,
    group_id: int | None,
    new_group_name: str = "",
    update_existing: bool,
    check_now: bool,
    at: datetime,
) -> ImportResult:
    """
    Применить разобранное — ТЗ 10.3, критерии 25, 26.

    Флаг обновления по умолчанию выключен у формы, и это не случайность: «перезапись чужих
    настроек не должна происходить сама собой». Здесь он просто передаётся честно.

    Группа задаётся на всё добавление (макет: «Группа для всех строк») и применяется только к
    новым доменам: перенос уже существующего домена в другую группу — отдельная операция со
    своей сводкой (10.4), и делать его побочным эффектом импорта нельзя.
    """
    preview = preview_import(text=text, group_id=group_id, new_group_name=new_group_name)
    added = updated = skipped = 0

    with transaction.atomic():
        group = _target_group(group_id, new_group_name)
        target_id = group.pk if group else None
        # Период читается один раз на весь импорт: он одинаков для всех строк, а смещение
        # внутри него — своё у каждой (11.1).
        period = settings_of_group(group, GlobalSettings.objects.get()).check_period.value

        for line in preview.lines:
            if line.error or line.name is None:
                continue
            existing = Domain.objects.filter(name=line.name).first()
            if existing is None:
                _create(line.name, line, target_id, _first_check(at, period, check_now=check_now))
                added += 1
            elif update_existing:
                _update(existing, line)
                updated += 1
            else:
                # Критерий 26: домен пропущен, его настройки не изменились.
                skipped += 1

    return ImportResult(
        added=added,
        updated=updated,
        skipped=skipped,
        failed_text=preview.failed_text,
        group_name=preview.group_name,
    )


def _target_group(group_id: int | None, new_group_name: str) -> Group | None:
    """
    Группа, в которую пойдут новые домены — ТЗ 10.4, «создать группу с введённым названием».

    Создание здесь, а не в обработчике: иначе отменённый посреди дела импорт оставлял бы
    группу, о которой никто не просил.
    """
    stripped = new_group_name.strip()
    if stripped:
        group, _ = Group.objects.get_or_create(name=stripped)
        return group
    return Group.objects.filter(pk=group_id).first() if group_id else None


def _line(number: int, text: str) -> ImportLine:
    parts = [part.strip() for part in text.split(FIELD_SEPARATOR)]
    name, reason = normalized_name(parts[0])
    entries = parse_allowlist(parts[1].replace(" ", "\n") if len(parts) > 1 else "")
    comment = parts[2] if len(parts) > 2 else ""

    error = reason
    if error is None and not entries.can_save:
        first = entries.errors[0]
        error = f"разрешённая запись «{first.text}»: {first.error}"

    existing = (
        Domain.objects.select_related("group").filter(name=name).first()
        if name and error is None
        else None
    )

    return ImportLine(
        number=number,
        text=text,
        name=name if error is None else None,
        converted_from=parts[0] if name and parts[0].lower() != name else None,
        comment=comment,
        allowlist=entries,
        exists=existing is not None,
        exists_in=_where(existing),
        error=error,
    )


def _where(existing: Domain | None) -> str:
    if existing is None:
        return ""
    return existing.group.name if existing.group else "Без группы"


def _failed_text(lines: list[ImportLine]) -> str:
    return "\n".join(line.text for line in lines if line.error)


def _create(name: str, line: ImportLine, group_id: int | None, next_check_at: datetime) -> None:
    domain = Domain.objects.create(
        name=name, group_id=group_id, comment=line.comment, next_check_at=next_check_at
    )
    _write_allowlist(domain, line)


def _update(domain: Domain, line: ImportLine) -> None:
    """Обновление трогает только то, что пришло в строке: комментарий и список."""
    if line.comment:
        Domain.objects.filter(pk=domain.pk).update(comment=line.comment)
    if line.allowlist.entries:
        AllowlistEntry.objects.filter(domain=domain).delete()
        _write_allowlist(domain, line)


def _write_allowlist(domain: Domain, line: ImportLine) -> None:
    AllowlistEntry.objects.bulk_create(
        [
            AllowlistEntry(domain=domain, entry=entry.network, raw=entry.raw)
            for entry in line.allowlist.entries
        ]
    )


def _first_check(at: datetime, period: timedelta, *, check_now: bool) -> datetime:
    """
    Когда домен проверится впервые — решение Q-E2.

    Флаг включён — сразу; выключен — со случайным смещением внутри периода, чтобы двести
    импортированных доменов не ушли в резолв одной пачкой (11.1). Смещение берётся на каждую
    строку своё: общее на весь импорт свело бы разнос на нет.
    """
    if check_now:
        return at
    return at + timedelta(seconds=random.randint(0, int(period.total_seconds())))  # noqa: S311
