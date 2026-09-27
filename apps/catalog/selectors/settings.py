"""
Разрешение настроек по цепочке наследования — ТЗ 5.

Цепочка ровно двухступенчатая: общие настройки → группа. Домен не переопределяет ничего из
расписания и уведомлений (ТЗ 4.3, 12), поэтому здесь он участвует только своей группой.

Происхождение значения возвращается вместе со значением: оно нужно интерфейсу (ТЗ 5, 11.8) и
не должно вычисляться в шаблоне.

**Порядок действий важен.** Наследование решается на сырых значениях столбцов, и только потом
результат переводится в доменный тип. Иначе «не напоминать» (ноль) превращается в ``None`` ещё
до решения и становится неотличимо от «по умолчанию» (NULL) — два разных состояния, которые
нельзя сливать ни в схеме, ни здесь.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from core.types.settings import EffectiveSetting, SettingOrigin


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    """Пять наследуемых параметров из таблицы раздела 5, каждый со своим происхождением."""

    check_period: EffectiveSetting[timedelta]
    freshness_threshold: EffectiveSetting[timedelta]
    notification_mode: EffectiveSetting[NotificationMode]
    notify_back_to_normal: EffectiveSetting[bool]
    # None означает «не напоминать» (ТЗ 8.5). В базе это ноль; NULL там означает совсем другое —
    # «по умолчанию», и до сюда это состояние не доживает: оно разрешается наследованием.
    reminder_interval: EffectiveSetting[timedelta | None]


def effective_settings(domain_id: int) -> EffectiveSettings:
    """Разрешённые настройки домена. Домен без группы берёт всё из общих настроек (ТЗ 5)."""
    domain = Domain.objects.select_related("group").get(pk=domain_id)
    return settings_of_group(domain.group)


def settings_of_group(
    group: Group | None, global_settings: GlobalSettings | None = None
) -> EffectiveSettings:
    """
    Разрешённые настройки группы. ``None`` в ``group`` — случай «без группы».

    ``global_settings`` передаётся списком доменов: строка общих настроек там уже прочитана, и
    без этого параметра каждая группа на странице добавляла бы ещё один одинаковый запрос.
    """
    if global_settings is None:
        global_settings = GlobalSettings.objects.get()

    return EffectiveSettings(
        check_period=_map(
            _inherit(
                group.check_period_seconds if group else None,
                global_settings.check_period_seconds,
            ),
            _to_timedelta,
        ),
        freshness_threshold=_map(
            _inherit(
                group.freshness_threshold_seconds if group else None,
                global_settings.freshness_threshold_seconds,
            ),
            _to_timedelta,
        ),
        notification_mode=_map(
            _inherit(
                (group.notification_mode if group else None) or None,
                global_settings.notification_mode,
            ),
            NotificationMode,
        ),
        notify_back_to_normal=_inherit(
            group.notify_back_to_normal if group else None,
            global_settings.notify_back_to_normal,
        ),
        reminder_interval=_map(
            _inherit(
                group.reminder_interval_seconds if group else None,
                global_settings.reminder_interval_seconds,
            ),
            _to_reminder,
        ),
    )


def _inherit[T](group_value: T | None, global_value: T) -> EffectiveSetting[T]:
    """
    Значение группы, если задано; иначе общее (таблица раздела 5).

    ``SettingOrigin.OWN`` здесь не появляется: «своё» — это взгляд экрана группы на собственное
    значение, а для домена то же значение выглядит как «от группы».
    """
    if group_value is None:
        return EffectiveSetting(value=global_value, origin=SettingOrigin.GLOBAL)
    return EffectiveSetting(value=group_value, origin=SettingOrigin.GROUP)


def _map[T, R](setting: EffectiveSetting[T], convert: Callable[[T], R]) -> EffectiveSetting[R]:
    """Перевод разрешённого значения в доменный тип с сохранением происхождения."""
    return EffectiveSetting(value=convert(setting.value), origin=setting.origin)


def _to_timedelta(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


def _to_reminder(seconds: int) -> timedelta | None:
    """Ноль — это «не напоминать» (ТЗ 8.5), и наружу он уходит как ``None``."""
    return timedelta(seconds=seconds) if seconds > 0 else None
