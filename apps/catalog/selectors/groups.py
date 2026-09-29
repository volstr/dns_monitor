"""
Экраны групп — ТЗ 5, 11.3; решения Q-C1, Q-13.1.

Группа — единственное место, где задаются период и уведомления для набора доменов (раздел 5).
Разрешённые адреса сюда **не относятся**: по таблице раздела 5 они «только домен, наследования
нет». Прототип на экране «Группы» обещает обратное («общий список плюс список группы плюс список
домена»), но сам себе противоречит строкой ниже на экране настройки группы — расхождение записано
в Q-C1, делаем по ТЗ.

**Происхождение здесь читается иначе, чем на карточке домена.** Для домена значение группы
выглядит как «от группы»; для самой группы то же значение — «своё», а незаданное — «по
умолчанию». Поэтому экран группы не переиспользует `settings_of_group`, а смотрит на собственные
столбцы группы напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db.models import Count, Q

from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from core.text import humanize_duration

#: Значения переключателя периода из прототипа.
PERIOD_PRESETS = (60, 5 * 60, 15 * 60, 60 * 60, 6 * 60 * 60)
#: Значения переключателя порога актуальности из прототипа.
#: Варианты порога актуальности у группы. Верхняя граница поднята до 14 суток вместе с общим
#: умолчанием (Q-D47): пресет, который меньше умолчания, предлагать бессмысленно — выбрать из
#: списка значение мягче общего было бы нельзя.
THRESHOLD_PRESETS = (
    6 * 60 * 60,
    24 * 60 * 60,
    7 * 24 * 60 * 60,
    14 * 24 * 60 * 60,
    30 * 24 * 60 * 60,
)
#: Интервалы напоминаний. Ноль — «не напоминать» (8.5), это не отсутствие значения.
REMINDER_PRESETS = (0, 60 * 60, 6 * 60 * 60, 24 * 60 * 60)


@dataclass(frozen=True, slots=True)
class GroupCard:
    """Карточка в списке групп."""

    id: int
    name: str
    comment: str
    domain_count: int
    unresolved: int
    period: str
    period_is_own: bool
    mode: str
    mode_is_own: bool


@dataclass(frozen=True, slots=True)
class UngroupedCard:
    """Псевдогруппа «Без группы»: домены берут всё из общих настроек (раздел 5)."""

    domain_count: int
    period: str
    mode: str


@dataclass(frozen=True, slots=True)
class GroupListPage:
    cards: tuple[GroupCard, ...]
    ungrouped: UngroupedCard


@dataclass(frozen=True, slots=True)
class Choice:
    """Пункт выбора значения. Пустое ``value`` — «по умолчанию» (раздел 5)."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class GroupEditPage:
    """Экран настройки группы. Для новой группы ``id`` пуст."""

    id: int | None
    name: str
    comment: str
    domain_count: int
    periods: tuple[Choice, ...]
    thresholds: tuple[Choice, ...]
    reminders: tuple[Choice, ...]
    modes: tuple[Choice, ...]
    notify_back_to_normal: bool | None
    #: Подписи «по умолчанию — 5 мин»: человек должен видеть, что именно унаследует.
    default_period: str
    default_threshold: str
    default_reminder: str
    default_mode: str
    default_notify_back: bool


def group_list_page() -> GroupListPage:
    """Список групп. Число доменов и открытых расхождений — одним запросом на всё."""
    global_settings = GlobalSettings.objects.get()
    groups = (
        Group.objects.annotate(
            domains_total=Count("domains", distinct=True),
            unresolved=Count(
                "domains",
                # Существование инцидента проверяется отдельно и обязательно: у домена без
                # инцидентов внешнее соединение подставляет NULL, и одно лишь «не закрыт»
                # посчитало бы такой домен расхождением.
                filter=Q(
                    domains__incidents__isnull=False,
                    domains__incidents__closed_at__isnull=True,
                ),
                distinct=True,
            ),
        )
        .values(
            "id",
            "name",
            "comment",
            "check_period_seconds",
            "notification_mode",
            "domains_total",
            "unresolved",
        )
        .order_by("name")
    )

    return GroupListPage(
        cards=tuple(_card(group, global_settings) for group in groups),
        ungrouped=UngroupedCard(
            domain_count=Domain.objects.filter(group__isnull=True).count(),
            period=humanize_duration(timedelta(seconds=global_settings.check_period_seconds)),
            mode=str(NotificationMode(global_settings.notification_mode).label),
        ),
    )


def _card(row: dict[str, Any], global_settings: GlobalSettings) -> GroupCard:
    own_period = row["check_period_seconds"]
    own_mode = row["notification_mode"]

    return GroupCard(
        id=row["id"],
        name=row["name"],
        comment=row["comment"],
        domain_count=row["domains_total"],
        unresolved=row["unresolved"],
        period=humanize_duration(
            timedelta(seconds=own_period or global_settings.check_period_seconds)
        ),
        period_is_own=own_period is not None,
        mode=str(NotificationMode(own_mode or global_settings.notification_mode).label),
        mode_is_own=bool(own_mode),
    )


def group_edit_page(group_id: int | None = None) -> GroupEditPage | None:
    """Экран настройки группы. ``None`` в ответе — группы нет, это 404."""
    group = None
    if group_id is not None:
        group = Group.objects.filter(pk=group_id).first()
        if group is None:
            return None

    defaults = GlobalSettings.objects.get()
    domain_count = Domain.objects.filter(group=group).count() if group else 0

    return GroupEditPage(
        id=group.pk if group else None,
        name=group.name if group else "",
        comment=group.comment if group else "",
        domain_count=domain_count,
        periods=_duration_choices(
            PERIOD_PRESETS, group.check_period_seconds if group else None
        ),
        thresholds=_duration_choices(
            THRESHOLD_PRESETS, group.freshness_threshold_seconds if group else None
        ),
        reminders=_reminder_choices(group.reminder_interval_seconds if group else None),
        modes=_mode_choices(group.notification_mode if group else None),
        notify_back_to_normal=group.notify_back_to_normal if group else None,
        default_period=humanize_duration(timedelta(seconds=defaults.check_period_seconds)),
        default_threshold=humanize_duration(
            timedelta(seconds=defaults.freshness_threshold_seconds)
        ),
        default_reminder=_reminder_label(defaults.reminder_interval_seconds),
        default_mode=str(NotificationMode(defaults.notification_mode).label),
        default_notify_back=defaults.notify_back_to_normal,
    )


def _duration_choices(presets: tuple[int, ...], current: int | None) -> tuple[Choice, ...]:
    """Значения переключателя плюс «По умолчанию» первым пунктом (раздел 5)."""
    choices = [Choice(value="", label="По умолчанию", selected=current is None)]
    known = set(presets)
    if current is not None and current not in known:
        # «Своё» значение, введённое руками: оно обязано остаться видимым и выбранным.
        known.add(current)
    for seconds in sorted(known):
        choices.append(
            Choice(
                value=str(seconds),
                label=humanize_duration(timedelta(seconds=seconds)),
                selected=current == seconds,
            )
        )
    return tuple(choices)


def _reminder_choices(current: int | None) -> tuple[Choice, ...]:
    choices = [Choice(value="", label="По умолчанию", selected=current is None)]
    known = set(REMINDER_PRESETS)
    if current is not None:
        known.add(current)
    for seconds in sorted(known):
        choices.append(
            Choice(
                value=str(seconds),
                label=_reminder_label(seconds),
                selected=current == seconds,
            )
        )
    return tuple(choices)


def _reminder_label(seconds: int) -> str:
    """Ноль — «не напоминать» (8.5), а не «через ноль минут»."""
    if seconds == 0:
        return "не напоминать"
    return f"раз в {humanize_duration(timedelta(seconds=seconds))}"


def _mode_choices(current: str | None) -> tuple[Choice, ...]:
    choices = [Choice(value="", label="По умолчанию", selected=not current)]
    for mode in NotificationMode:
        choices.append(
            Choice(value=mode.value, label=str(mode.label), selected=current == mode.value)
        )
    return tuple(choices)
