"""
Список доменов — ТЗ 11.5, 8.2; решения Q-D15, Q-C14, Q-C22.

Экран отдаёт шаблону готовые значения: подписи, классы состояний и отметки истории считаются
здесь. В разметке не остаётся ни одного ветвления по доменным правилам — этого требует
`CLAUDE.md` («вычисления в шаблоне» в списке запрещённого).

**Число запросов не зависит от числа строк.** Их ровно пять на страницу: домены, общие настройки,
белые списки, реестры адресов и история проверок. Наследование настроек разрешается по группам,
которых на странице заведомо меньше, чем доменов, и результат запоминается.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group, NotificationMode
from apps.catalog.selectors.settings import EffectiveSettings, settings_of_group
from apps.monitoring.models import Incident
from apps.monitoring.selectors.addresses import RegistryRow, registry_of_domains
from apps.monitoring.selectors.history import EMPTY_HISTORY, DomainHistory, history_of_domains
from core.text import humanize_duration, plural
from core.types.checking import AddressStatus
from core.types.settings import SettingOrigin

#: Страница списка. Масштаб заявлен в тысячу доменов (`CLAUDE.md`), и постраничность обязательна.
PAGE_SIZE = 50

#: Сколько адресов показывается чипами. Колонка шириной 280 px (`.ips` в CSS прототипа), больше
#: шести чипов превращают строку таблицы в абзац. Остальные сворачиваются в «и ещё N».
ADDRESS_LIMIT = 6


class DomainFilter(StrEnum):
    """Сегменты фильтра из прототипа. «Без разрешённых» — требование 8.2."""

    ALL = "all"
    DISCREPANCIES = "discrepancies"
    NO_ALLOWLIST = "no_allowlist"
    DISABLED = "disabled"


#: Подписи сегментов. Здесь, а не в шаблоне: перечень сегментов и их порядок — свойство
#: фильтра, и шаблон не должен знать его наизусть.
FILTER_LABELS = {
    DomainFilter.ALL: "Все",
    DomainFilter.DISCREPANCIES: "Расхождения",
    DomainFilter.NO_ALLOWLIST: "Без разрешённых",
    DomainFilter.DISABLED: "Выключенные",
}


@dataclass(frozen=True, slots=True)
class Segment:
    """Одна кнопка фильтра вместе с тем, выбрана ли она. Шаблон ничего не сравнивает."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class AddressChip:
    """Адрес в колонке «Адреса». `css` и `glyph` — классы и значки прототипа."""

    address: str
    css: str
    glyph: str


@dataclass(frozen=True, slots=True)
class Badge:
    """Колонка «Проверка адресов»: состояние и пояснение под ним."""

    label: str
    #: Класс `.state`: ok / breach / fail. Пусто — приглушённый текст без цветной точки.
    tone: str
    detail: str


@dataclass(frozen=True, slots=True)
class DomainRow:
    """Строка списка. Всё готово к выводу: шаблон только подставляет."""

    id: int
    name: str
    group_name: str
    comment: str
    watch_enabled: bool
    addresses: tuple[AddressChip, ...]
    hidden_addresses: int
    matching: Badge
    check_period: str
    check_period_origin: SettingOrigin
    notification_mode: str
    notification_origin: SettingOrigin
    #: Ловушка 8.2: пустой список в режиме «При выходе из разрешённых». Уведомлений не будет
    #: никогда, и пользователь этого не ожидает, поэтому строка помечается (Q-D15).
    silent_trap: bool
    #: Список не задан вообще. Помечается независимо от режима: домен под наблюдением, с
    #: которым нечего сравнивать, требует внимания (Q-D15, уточнение 2026-09-26).
    needs_allowlist: bool
    history: DomainHistory


@dataclass(frozen=True, slots=True)
class GroupOption:
    """Пункт поля выбора группы с поиском (10.4). Число доменов показывает прототип."""

    id: int
    name: str
    domain_count: int


@dataclass(frozen=True, slots=True)
class DomainListPage:
    """Всё, что нужно экрану «Домены». Обработчику разрешён один вызов селектора."""

    rows: tuple[DomainRow, ...]
    groups: tuple[GroupOption, ...]
    chosen: DomainFilter
    #: Кнопки фильтра, готовые к отрисовке. Признак «выбрана» считается здесь: сравнение в
    #: шаблоне — вычисление, которых там быть не должно (`CLAUDE.md`).
    segments: tuple[Segment, ...]
    search: str
    group_id: int | None


def domain_list_page(
    *,
    at: datetime,
    search: str = "",
    group_id: int | None = None,
    chosen: DomainFilter = DomainFilter.ALL,
    limit: int = PAGE_SIZE,
) -> DomainListPage:
    """Страница списка доменов: строки, группы для фильтра и выбранные значения фильтра."""
    return DomainListPage(
        rows=domain_rows(at=at, search=search, group_id=group_id, chosen=chosen, limit=limit),
        groups=_group_options(),
        chosen=chosen,
        segments=tuple(
            Segment(value=value.value, label=label, selected=value == chosen)
            for value, label in FILTER_LABELS.items()
        ),
        search=search,
        group_id=group_id,
    )


def domain_rows(
    *,
    at: datetime,
    search: str = "",
    group_id: int | None = None,
    chosen: DomainFilter = DomainFilter.ALL,
    limit: int = PAGE_SIZE,
) -> tuple[DomainRow, ...]:
    """Строки списка доменов с применёнными фильтрами."""
    rows = list(_filtered(search=search, group_id=group_id, chosen=chosen)[:limit])
    if not rows:
        return ()

    domain_ids = [domain.pk for domain in rows]
    global_settings = GlobalSettings.objects.get()
    resolved = _resolved_settings(rows, global_settings)
    thresholds: dict[int, timedelta] = {
        domain.pk: resolved[domain.group_id].freshness_threshold.value for domain in rows
    }

    allowlists = _allowlists(domain_ids)
    # Q-D27: при снятом флаге адресов IPv6 не существует.
    registries = registry_of_domains(
        domain_ids, at, thresholds, skip_ipv6=not global_settings.check_ipv6
    )
    histories = history_of_domains(domain_ids)

    return tuple(
        _row(
            domain,
            effective=resolved[domain.group_id],
            entries=allowlists.get(domain.pk, ()),
            registry=registries.get(domain.pk, ()),
            history=histories.get(domain.pk, EMPTY_HISTORY),
        )
        for domain in rows
    )


def _group_options() -> tuple[GroupOption, ...]:
    """Группы для поля выбора с поиском (10.4). Порядок — по имени, как в прототипе."""
    groups = Group.objects.annotate(size=Count("domains")).order_by("name")
    return tuple(
        GroupOption(id=group.pk, name=group.name, domain_count=group.size) for group in groups
    )


def _filtered(
    *, search: str, group_id: int | None, chosen: DomainFilter
) -> QuerySet[Domain]:
    domains = Domain.objects.select_related("group").annotate(
        has_allowlist=Exists(AllowlistEntry.objects.filter(domain=OuterRef("pk"))),
        has_open_incident=Exists(
            Incident.objects.filter(domain=OuterRef("pk"), closed_at__isnull=True)
        ),
    )

    match chosen:
        case DomainFilter.DISCREPANCIES:
            # «Расхождение» — это открытый инцидент, как на обзоре (11.9), а не просто
            # несовпавший адрес: иначе фильтр и счётчик обзора показывали бы разное.
            domains = domains.filter(has_open_incident=True)
        case DomainFilter.NO_ALLOWLIST:
            # Q-D15: все домены с пустым списком, независимо от режима уведомлений. Пустой
            # список при «При изменении адресов» — законная настройка, и она тоже сюда попадает.
            domains = domains.filter(has_allowlist=False)
        case DomainFilter.DISABLED:
            domains = domains.filter(watch_enabled=False)
        case DomainFilter.ALL:
            pass

    if group_id is not None:
        domains = domains.filter(group_id=group_id)
    if search:
        condition, over_registry = _search_condition(search.strip())
        domains = domains.filter(condition)
        if over_registry:
            # Связь «к многим»: без этого домен с двумя подходящими адресами задвоится.
            domains = domains.distinct()

    return domains.order_by("name")


def _search_condition(text: str) -> tuple[Q, bool]:
    """
    Поиск «домен или IP» из прототипа. Второе значение — нужен ли `distinct`.

    Целый адрес ищется по реестру, всё остальное — по имени. Разбор адреса решает, что это:
    искать `10.20` как адрес бессмысленно, а как часть имени домена — осмысленно.
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return Q(name__icontains=text), False
    return Q(address_registry__address=text), True


def _resolved_settings(
    domains: list[Domain], global_settings: GlobalSettings
) -> dict[int | None, EffectiveSettings]:
    """Наследование решается по группам: их на странице меньше, чем доменов."""
    resolved: dict[int | None, EffectiveSettings] = {}
    for domain in domains:
        if domain.group_id not in resolved:
            resolved[domain.group_id] = settings_of_group(domain.group, global_settings)
    return resolved


def _allowlists(domain_ids: list[int]) -> dict[int, tuple[str, ...]]:
    """Записи белых списков одной выборкой: нужны и счёт, и текст единственной записи."""
    entries = AllowlistEntry.objects.filter(domain_id__in=domain_ids).values_list(
        "domain_id", "raw"
    )
    collected: dict[int, list[str]] = {}
    for domain_id, raw in entries:
        collected.setdefault(domain_id, []).append(raw)
    return {domain_id: tuple(raws) for domain_id, raws in collected.items()}


def _row(
    domain: Domain,
    *,
    effective: EffectiveSettings,
    entries: tuple[str, ...],
    registry: tuple[RegistryRow, ...],
    history: DomainHistory,
) -> DomainRow:
    mode = NotificationMode(effective.notification_mode.value)
    shown = registry[:ADDRESS_LIMIT]

    return DomainRow(
        id=domain.pk,
        name=domain.name,
        group_name=domain.group.name if domain.group else "",
        comment=domain.comment,
        watch_enabled=domain.watch_enabled,
        addresses=tuple(_chip(row, domain) for row in shown),
        hidden_addresses=len(registry) - len(shown),
        matching=_matching(domain, entries, registry, mode),
        check_period=humanize_duration(effective.check_period.value),
        check_period_origin=effective.check_period.origin,
        notification_mode=str(mode.label),
        notification_origin=effective.notification_mode.origin,
        silent_trap=not entries and mode == NotificationMode.NOT_ALLOWED,
        needs_allowlist=not entries,
        history=history,
    )


def _chip(row: RegistryRow, domain: Domain) -> AddressChip:
    """Чип адреса. Классы и значки — прототипа: `.chip`, `.chip.out`, `.chip.gone`, `.chip.new`."""
    address = str(row.address)
    if row.status == AddressStatus.STALE:
        return AddressChip(address=address, css="gone", glyph="")
    if row.status == AddressStatus.UNMATCHED:
        return AddressChip(address=address, css="out", glyph="✕")
    if _is_new(row, domain):
        return AddressChip(address=address, css="new", glyph="+")
    return AddressChip(address=address, css="", glyph="")


def _is_new(row: RegistryRow, domain: Domain) -> bool:
    """
    «Новый» — появился на последней успешной проверке.

    Q-C22: прототип показывает такой чип, но ТЗ не определяет, что считать новизной. Временное
    решение — первое обнаружение не раньше последней успешной проверки; оно не требует новых
    полей и совпадает с моментом, когда адрес был бы отмечен событием `ADDRESS_APPEARED`.
    """
    last_check = domain.last_successful_check_at
    return last_check is not None and row.first_seen_at >= last_check


def _matching(
    domain: Domain,
    entries: tuple[str, ...],
    registry: tuple[RegistryRow, ...],
    mode: NotificationMode,
) -> Badge:
    """Колонка «Проверка адресов» — состояние вхождения и пояснение под ним."""
    if domain.consecutive_resolve_failures > 0:
        return Badge(
            label="Нет ответа",
            tone="fail",
            detail=f"Неудачных проверок подряд: {domain.consecutive_resolve_failures}",
        )

    if not entries:
        # Сравнивать не с чем (Q-D25). Подпись прототипа «только изменения» уместна лишь тогда,
        # когда режим действительно следит за изменениями.
        detail = "только изменения" if mode == NotificationMode.ADDRESS_CHANGE else ""
        return Badge(label="Ничего не задано", tone="", detail=detail)

    detail = (
        entries[0]
        if len(entries) == 1
        else f"{len(entries)} {plural(len(entries), 'запись', 'записи', 'записей')} в списке"
    )
    unmatched = sum(1 for row in registry if row.status == AddressStatus.UNMATCHED)
    if unmatched:
        word = plural(unmatched, "не решён", "не решено", "не решено")
        return Badge(label=f"{unmatched} {word}", tone="breach", detail=detail)

    fresh = sum(1 for row in registry if row.status != AddressStatus.STALE)
    return Badge(label="Все решены" if fresh > 1 else "Решён", tone="ok", detail=detail)
