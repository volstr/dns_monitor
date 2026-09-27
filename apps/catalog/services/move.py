"""
Перенос домена в другую группу — ТЗ 10.4, критерий 28.

Перенос трогает ровно одно поле — `group`. Всё остальное принадлежит домену и по 10.4 остаётся
как было: разрешённые адреса, комментарий, реестр, история, открытый инцидент.

Единственное исключение — `next_check_at`, и оно вынужденное. Расписание живёт в этом поле, а
период пришёл от прежней группы: домен, перенесённый из часового периода в минутный, иначе ждал
бы ещё час, и минутный период включился бы только со второй проверки. Поэтому ближайшая проверка
подтягивается к новому периоду — но **не отодвигается**: перенос в группу с редким периодом не
должен отменять уже назначенную проверку.

Создание группы прямо из поля выбора (10.4, «при отсутствии совпадений предлагается создать
группу с введённым названием») живёт здесь же: новая группа не задаёт ничего своего, все пять
наследуемых параметров остаются «по умолчанию» (раздел 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from apps.catalog.models import GROUP_NAME_MAX_LENGTH, Domain, GlobalSettings, Group
from apps.catalog.selectors.settings import settings_of_group


@dataclass(frozen=True, slots=True)
class DomainMoved:
    domain_id: int
    domain_name: str
    group_name: str


@dataclass(frozen=True, slots=True)
class MoveRefused:
    reason: str


@dataclass(frozen=True, slots=True)
class GroupCreated:
    group_id: int
    name: str


@dataclass(frozen=True, slots=True)
class GroupNotCreated:
    reason: str


def move_domain(
    *, domain_id: int, group_id: int | None, new_group_name: str = "", at: datetime
) -> DomainMoved | MoveRefused:
    """Перенести домен — ТЗ 10.4."""
    domain = Domain.objects.select_related("group").filter(pk=domain_id).first()
    if domain is None:
        return MoveRefused(reason="Домен не найден.")

    target_id = group_id
    if new_group_name.strip():
        # Группа с введённым названием (10.4). Существующая с тем же именем переиспользуется —
        # двух одноимённых групп быть не может, и отказывать тут не за что.
        created = create_group(new_group_name)
        if isinstance(created, GroupNotCreated):
            return MoveRefused(reason=created.reason)
        target_id = created.group_id

    group = Group.objects.filter(pk=target_id).first() if target_id else None
    if target_id and group is None:
        return MoveRefused(reason="Группа не найдена — возможно, её удалили.")

    if domain.group_id == (group.pk if group else None):
        return MoveRefused(reason="Домен уже в этой группе.")

    period = settings_of_group(group, GlobalSettings.objects.get()).check_period.value
    with transaction.atomic():
        domain.group = group
        domain.next_check_at = _next_check(domain.next_check_at, at + period)
        domain.save(update_fields=["group", "next_check_at"])

    return DomainMoved(
        domain_id=domain.pk,
        domain_name=domain.name,
        group_name=group.name if group else "Без группы",
    )


def create_group(name: str) -> GroupCreated | GroupNotCreated:
    """
    Создать группу с введённым названием — ТЗ 10.4.

    Ничего, кроме имени, не задаётся: у новой группы все наследуемые параметры остаются
    «по умолчанию», то есть берутся из общих настроек (раздел 5).
    """
    stripped = name.strip()
    if not stripped:
        return GroupNotCreated(reason="Название группы обязательно.")
    if len(stripped) > GROUP_NAME_MAX_LENGTH:
        return GroupNotCreated(reason=f"Название длиннее {GROUP_NAME_MAX_LENGTH} символов.")

    existing = Group.objects.filter(name=stripped).first()
    if existing is not None:
        # Не отказ: человек хотел группу с таким именем, она уже есть — этого достаточно.
        return GroupCreated(group_id=existing.pk, name=existing.name)

    group = Group.objects.create(name=stripped)
    return GroupCreated(group_id=group.pk, name=group.name)


def _next_check(scheduled: datetime | None, limit: datetime) -> datetime:
    """Ближайшая проверка подтягивается к новому периоду, но не отодвигается."""
    if scheduled is None:
        return limit
    return min(scheduled, limit)
