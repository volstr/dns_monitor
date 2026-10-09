"""
Сводка переноса домена в другую группу — ТЗ 10.4, критерий 28.

«Перенос молча меняет расписание и режим уведомлений» — и весь смысл этого экрана в том, чтобы
он перестал быть молчаливым. Сводка считается здесь целиком: шаблон только выводит готовые
строки, вычислений в нём быть не должно (`CLAUDE.md`).

Строки «что изменится» собираются даже тогда, когда значение не поменялось: человеку важно
видеть, что период он не потеряет, а не догадываться об этом по отсутствию строки. Что именно
изменилось, помечает флаг `changed`, а не наличие строки.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from apps.catalog.models import Domain, GlobalSettings, Group, NotificationMode
from apps.catalog.selectors.settings import EffectiveSettings, settings_of_group
from core.text import humanize_duration

#: Что принадлежит домену, а не группе, и переносом не затрагивается (10.4).
KEEPS = (
    'Разрешённые адреса, комментарий и реестр адресов домена не меняются — они принадлежат '
    'домену, а не группе.'
)

#: Режим, который следит за появлением непрошеного адреса. Открытый инцидент — ровно про это.
TRACKS_UNRESOLVED = NotificationMode.NOT_ALLOWED


@dataclass(frozen=True, slots=True)
class MoveRow:
    """Одна строка сводки: было → станет."""

    label: str
    was: str
    becomes: str
    changed: bool


@dataclass(frozen=True, slots=True)
class GroupOption:
    """Вариант поля выбора группы. Число доменов показывается по 10.4."""

    value: str
    name: str
    domain_count: str
    selected: bool


@dataclass(frozen=True, slots=True)
class MovePage:
    """Экран переноса. ``target`` пуст, пока группа не выбрана."""

    domain_id: int
    domain_name: str
    current_id: int | None
    current_group: str
    #: Название группы, которую ещё только предстоит создать (10.4). Пусто — выбрана готовая.
    new_group_name: str
    target_id: int | None
    target_name: str
    rows: tuple[MoveRow, ...]
    keeps: str
    #: 10.4: отдельное предупреждение, если открытый инцидент перестанет быть слышен.
    incident_warning: str
    options: tuple[GroupOption, ...]

    @property
    def is_move(self) -> bool:
        """Выбрана та же группа — переносить нечего."""
        return bool(self.new_group_name) or self.target_id != self.current_id

    @property
    def changes(self) -> bool:
        return any(row.changed for row in self.rows)


def move_page(*, domain_id: int, target_id: int | None, new_group_name: str = '') -> MovePage:
    """
    Собрать сводку переноса — ТЗ 10.4, критерий 28.

    Названная в поле новая группа здесь не создаётся — экран только показывает. Считать её
    настройки не нужно и незачем: у новой группы все пять наследуемых параметров остаются «по
    умолчанию», то есть ровно те же, что и у случая «без группы» (раздел 5).
    """
    domain = Domain.objects.select_related('group').get(pk=domain_id)
    global_settings = GlobalSettings.objects.get()

    fresh = new_group_name.strip()
    target = None if fresh else Group.objects.filter(pk=target_id).first() if target_id else None
    # Группа могла быть удалена между открытием экрана и отправкой формы: «без группы» — тоже
    # допустимая цель переноса, и отличить её от исчезнувшей группы можно только по номеру.
    resolved_id = target.pk if target else None

    before = settings_of_group(domain.group, global_settings)
    after = settings_of_group(target, global_settings)

    return MovePage(
        domain_id=domain.pk,
        domain_name=domain.name,
        current_id=domain.group_id,
        current_group=domain.group.name if domain.group else 'Без группы',
        new_group_name=fresh,
        target_id=resolved_id,
        target_name=fresh or (target.name if target else 'Без группы'),
        rows=_rows(before, after),
        keeps=KEEPS,
        incident_warning=_incident_warning(domain, after),
        options=_options(resolved_id),
    )


def _rows(before: EffectiveSettings, after: EffectiveSettings) -> tuple[MoveRow, ...]:
    """Две строки из 10.4: период проверки и режим уведомлений."""
    was_period = humanize_duration(before.check_period.value)
    becomes_period = humanize_duration(after.check_period.value)
    was_mode = str(NotificationMode(before.notification_mode.value).label)
    becomes_mode = str(NotificationMode(after.notification_mode.value).label)

    return (
        MoveRow(
            label='Период проверки',
            was=was_period,
            becomes=becomes_period,
            changed=was_period != becomes_period,
        ),
        MoveRow(
            label='Когда уведомлять',
            was=was_mode,
            becomes=becomes_mode,
            changed=was_mode != becomes_mode,
        ),
    )


def _incident_warning(domain: Domain, after: EffectiveSettings) -> str:
    """10.4: открытый инцидент плюс режим, который такие события не отслеживает."""
    if after.notification_mode.value == TRACKS_UNRESOLVED:
        return ''
    if not domain.incidents.filter(closed_at__isnull=True).exists():
        return ''
    return (
        'Новая группа не следит за выходом из разрешённых. Домен перестанет присылать '
        'уведомление о непрошеном адресе — а сейчас у него как раз есть такой.'
    )


def _options(resolved_id: int | None) -> tuple[GroupOption, ...]:
    """Варианты поля с поиском. «Без группы» — такой же вариант, а не отсутствие выбора."""
    groups = Group.objects.annotate(size=Count('domains')).order_by('name')
    return (
        GroupOption(
            value='',
            name='Без группы',
            domain_count='—',
            selected=resolved_id is None,
        ),
        *(
            GroupOption(
                value=str(group.pk),
                name=group.name,
                domain_count=str(group.size),
                selected=group.pk == resolved_id,
            )
            for group in groups
        ),
    )
