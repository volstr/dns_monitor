"""
Соотношение периода проверки и порога актуальности — ТЗ 7.2, решение Q-13.1.

ТЗ 7.2: «порог актуальности должен быть заметно больше периода проверки, иначе за него не
успевает пройти полный круг адресов». Комбинация «период 6 часов, порог 1 час» ломает логику:
половина набора ложно уйдёт в «Не актуален».

**Предупреждение, а не запрет** (решение Q-13.1). Сочетание складывается из двух строк — группы и
общих настроек, — а `CheckConstraint` живёт внутри одной, поэтому запрет существовал бы только в
форме и его обошли бы импорт и команды обслуживания. Автоподъём отпадает: он молча меняет
введённое человеком число.

Две ступени. Порог **не больше** периода — предупреждение жёсткое: круг адресов физически не
успевает пройти. Порог меньше утроенного периода — мягкая подсказка; тройка принята как
эвристика, в ТЗ её нет.

Считается на **действующих** значениях, то есть после наследования: иначе смена общего периода
тихо ломает группы, которые унаследовали период, но задали свой порог.
"""

from __future__ import annotations

from datetime import timedelta

from apps.catalog.models import GlobalSettings, Group
from core.text import humanize_duration

#: Во сколько раз порог должен превышать период, чтобы круг адресов успевал пройти.
COMFORTABLE_RATIO = 3


def freshness_warning(*, period_seconds: int, threshold_seconds: int, subject: str = '') -> str:
    """Предупреждение о соотношении или пустая строка, если всё в порядке."""
    period = humanize_duration(timedelta(seconds=period_seconds))
    threshold = humanize_duration(timedelta(seconds=threshold_seconds))
    where = f' у группы «{subject}»' if subject else ''

    if threshold_seconds <= period_seconds:
        return (
            f'Порог актуальности{where} ({threshold}) не больше периода проверки ({period}). '
            f'За порог не успевает пройти круг адресов, и часть набора будет ложно объявлена '
            f'неактуальной.'
        )
    if threshold_seconds < period_seconds * COMFORTABLE_RATIO:
        return (
            f'Порог актуальности{where} ({threshold}) близок к периоду проверки ({period}). '
            f'Обычно его берут хотя бы втрое больше.'
        )
    return ''


def warnings_for_group(
    *, group: Group | None, global_settings: GlobalSettings, subject: str = ''
) -> tuple[str, ...]:
    """Предупреждение для одной группы по её действующим значениям."""
    period = (group.check_period_seconds if group else None) or global_settings.check_period_seconds
    threshold = (
        group.freshness_threshold_seconds if group else None
    ) or global_settings.freshness_threshold_seconds

    warning = freshness_warning(
        period_seconds=period, threshold_seconds=threshold, subject=subject
    )
    return (warning,) if warning else ()


def warnings_after_global_change(global_settings: GlobalSettings) -> tuple[str, ...]:
    """
    Какие группы сломала правка общих настроек.

    Решение Q-13.1 требует пересчитать все группы и назвать сломавшиеся поимённо: человек,
    меняющий общий период, не обязан помнить, какая группа задала свой порог. Групп на установке
    десятки, запрос один.
    """
    collected: list[str] = []
    collected.extend(warnings_for_group(group=None, global_settings=global_settings))
    for group in Group.objects.order_by('name'):
        collected.extend(
            warnings_for_group(group=group, global_settings=global_settings, subject=group.name)
        )
    return tuple(collected)
