"""
Создание и правка группы — ТЗ 5, 11.3; решение Q-13.1.

Незаданное значение группы — это `NULL`, то есть «по умолчанию», и оно отличается от нуля.
Особенно у интервала напоминаний: ноль там означает «не напоминать» (8.5), и сливать его с
«по умолчанию» нельзя ни в схеме, ни здесь.

Отказ — штатный исход с перечнем причин (`CLAUDE.md`). Предупреждения Q-13.1 сохранению не
мешают: это предупреждение, а не запрет.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.catalog.models import GlobalSettings, Group
from apps.catalog.services.intervals import warnings_for_group

#: ТЗ 11.1: минимальный период проверки — минута.
MIN_PERIOD_SECONDS = 60


@dataclass(frozen=True, slots=True)
class GroupSaved:
    group_id: int
    #: Предупреждения Q-13.1: сохранению не мешают, но показываются.
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroupNotSaved:
    reasons: tuple[str, ...]


def save_group(
    *,
    group_id: int | None,
    name: str,
    comment: str,
    check_period_seconds: int | None,
    freshness_threshold_seconds: int | None,
    notification_mode: str | None,
    notify_back_to_normal: bool | None,
    reminder_interval_seconds: int | None,
) -> GroupSaved | GroupNotSaved:
    """Сохранить группу. ``group_id`` пуст — создаётся новая."""
    reasons = _reasons(name, check_period_seconds, freshness_threshold_seconds)
    if reasons:
        return GroupNotSaved(reasons=reasons)

    fields = {
        "name": name.strip(),
        "comment": comment,
        "check_period_seconds": check_period_seconds,
        "freshness_threshold_seconds": freshness_threshold_seconds,
        "notification_mode": notification_mode or "",
        "notify_back_to_normal": notify_back_to_normal,
        "reminder_interval_seconds": reminder_interval_seconds,
    }

    try:
        with transaction.atomic():
            if group_id is None:
                group = Group.objects.create(**fields)
            else:
                Group.objects.filter(pk=group_id).update(**fields)
                group = Group.objects.get(pk=group_id)
    except IntegrityError:
        # Имя группы уникально. Гонку двух сохранений ловим здесь, а не проверкой заранее.
        return GroupNotSaved(reasons=(f"Группа с именем «{name.strip()}» уже есть.",))

    return GroupSaved(
        group_id=group.pk,
        warnings=warnings_for_group(group=group, global_settings=GlobalSettings.objects.get()),
    )


def _reasons(
    name: str, check_period_seconds: int | None, freshness_threshold_seconds: int | None
) -> tuple[str, ...]:
    collected = []
    if not name.strip():
        collected.append("У группы должно быть название.")
    if check_period_seconds is not None and check_period_seconds < MIN_PERIOD_SECONDS:
        collected.append("Период проверки не может быть меньше минуты (ТЗ 11.1).")
    if freshness_threshold_seconds is not None and freshness_threshold_seconds <= 0:
        collected.append("Порог актуальности должен быть больше нуля.")
    return tuple(collected)
