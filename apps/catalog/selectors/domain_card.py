"""
Карточка домена на чтение — ТЗ 9.1, 6.3, 6.4, 11.8; решения Q-C13, Q-13.5, Q-C11.

Экран собирается одним вызовом и отдаёт шаблону готовые строки: подписи, причины совпадения,
возраст адресов и происхождение каждой унаследованной настройки. В разметке не остаётся ни
одного ветвления по доменным правилам (`CLAUDE.md`).

**Чего здесь нет намеренно.** Линейка подсети (11.4) и график следа реестра (9.2) — виджеты
этапа 7, это два из трёх мест, где вообще разрешён клиентский код. Здесь готовятся те же данные
в текстовом виде: какие адреса попадают в запись и когда каждый отдавался. Правка домена —
белый список с построчной валидацией (10.2), типы записей, комментарий, сохранение и удаление —
следующая часть; здесь всё только показывается.

**Вхождение адреса в запись не вычисляется заново.** Оно уже пришло из базы вместе со статусом
(`>>=` по индексу GiST), и адреса раскладываются по записям сопоставлением текста записи.
Разбирать CIDR второй раз в Python значило бы завести вторую реализацию вхождения.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast

from django.utils.timezone import localtime

from apps.catalog.models import (
    RECORD_TYPE_CHOICES,
    AllowlistEntry,
    Domain,
    GlobalSettings,
    NotificationMode,
)
from apps.catalog.selectors.health import checks_are_possible
from apps.catalog.selectors.settings import EffectiveSettings, settings_of_group
from apps.monitoring.models import Event, EventType
from apps.monitoring.selectors.addresses import address_statuses
from apps.monitoring.selectors.journal import CHANGE_TYPES, FAILURE_TYPES
from apps.monitoring.selectors.trace import trace_of_domain
from core.text import humanize_duration, plural
from core.types.checking import AddressStatus, AddressStatusView
from core.types.dns import IPNetwork
from core.types.settings import SettingOrigin
from core.types.widgets import RulerData, RulerMark, TraceData

#: Q-13.5: запись, под которую столько не приходило ни одного адреса, подсвечивается. Константа,
#: а не настройка: в 4.1 такого параметра нет, а в общих настройках уже шестнадцать полей.
STALE_ENTRY_AFTER = timedelta(days=30)

#: Сколько записей журнала показывает панель «История» на карточке.
EVENT_LIMIT = 20

#: Статус адреса → подпись и класс `.state`. Слова согласованы с Q-13.8.
STATUS_LABELS = {
    AddressStatus.MATCHED: ('Решён', 'ok'),
    AddressStatus.UNMATCHED: ('Не решён', 'breach'),
    AddressStatus.STALE: ('Не актуален', 'stale'),
    AddressStatus.NOT_CHECKED: ('Не проверяется', ''),
}

#: Тип события → класс `.state`. Названия берутся из самой модели: они утверждены в Q-C11.
EVENT_TONES = {
    EventType.ADDRESS_APPEARED: 'change',
    EventType.ADDRESS_GONE: 'change',
    EventType.NOT_ALLOWED: 'breach',
    EventType.BACK_TO_NORMAL: 'ok',
    EventType.RESOLVE_FAILED: 'fail',
    EventType.RESOLVE_RECOVERED: 'ok',
    EventType.WATCH_STARTED: 'ok',
}


class RegistryFilter(StrEnum):
    """Сегменты фильтра реестра из макета."""

    ALL = 'all'
    MATCHED = 'matched'
    UNMATCHED = 'unmatched'
    STALE = 'stale'


class HistoryFilter(StrEnum):
    """Сегменты фильтра истории из макета. Названия групп — те же, что в журнале (Q-C11)."""

    ALL = 'all'
    CHANGES = 'changes'
    NOT_ALLOWED = 'not_allowed'
    RESOLVE_ERRORS = 'resolve_errors'


#: Какие статусы показывает сегмент. `NOT_CHECKED` не попадает ни в один: он не «решён» и не
#: «не решён», потому что вхождение не вычислялось вовсе (Q-D25). Класть его к решённым значило
#: бы утверждать, что адрес прошёл проверку, которой не было. Такие адреса видны в «Все».
REGISTRY_STATUSES = {
    RegistryFilter.MATCHED: (AddressStatus.MATCHED,),
    RegistryFilter.UNMATCHED: (AddressStatus.UNMATCHED,),
    RegistryFilter.STALE: (AddressStatus.STALE,),
}

HISTORY_TYPES = {
    HistoryFilter.CHANGES: CHANGE_TYPES,
    HistoryFilter.NOT_ALLOWED: (EventType.NOT_ALLOWED,),
    HistoryFilter.RESOLVE_ERRORS: FAILURE_TYPES,
}


@dataclass(frozen=True, slots=True)
class SettingLine:
    """Строка блока «Взято из группы» — ТЗ 5, 11.8, Q-C13."""

    label: str
    value: str
    origin: SettingOrigin


@dataclass(frozen=True, slots=True)
class RegistryLine:
    """Строка реестра адресов — ТЗ 9.1."""

    address: str
    #: «входит в 10.20.0.0/22», «точное совпадение с 10.20.3.7» или «нет совпадений» (6.3).
    match_text: str
    status_label: str
    status_tone: str
    chip_css: str
    first_seen_at: datetime
    last_seen_at: datetime
    #: «отдавался в последней проверке», «не отдаётся 10 дней» — подпись под статусом.
    age_text: str


@dataclass(frozen=True, slots=True)
class AllowlistLine:
    """Запись белого списка вместе с тем, что в неё попадает."""

    raw: str
    is_network: bool
    #: «10.20.0.0 — 10.20.3.255 · 1024 адреса» у подсети; у точной записи пусто.
    range_text: str
    inside: tuple[str, ...]
    #: Q-13.5: сколько дней под запись не приходило ни одного адреса. `None` — приходило недавно
    #: либо не приходило никогда, и тогда говорить о «устаревании» нечего.
    stale_days: int | None
    #: Под запись не попал ни один адрес, **и домен при этом успешно отвечал** (Q-D38). Отдельно
    #: от `stale_days`: та говорит «перестало приходить», эта — «не приходило ни разу». Второе
    #: подозрительнее: чаще всего это опечатка в подсети или запись, скопированная с чужого
    #: домена. У непроверенного домена ложным не бывает — там просто нечему было попасть.
    never_matched: bool
    #: Данные линейки подсети (11.4) для виджета. `None` у записи без маски: шкалу рисовать не
    #: из чего, совпадение там проверяется точным сравнением.
    ruler: RulerData | None


@dataclass(frozen=True, slots=True)
class EventLine:
    """Строка панели «История» — ТЗ 7.1, названия из Q-C11."""

    occurred_at: datetime
    label: str
    tone: str
    addresses: tuple[str, ...]
    author_name: str


@dataclass(frozen=True, slots=True)
class DomainCard:
    """Всё, что показывает карточка. Обработчику разрешён один вызов селектора."""

    id: int
    name: str
    group_name: str
    group_id: int | None
    comment: str
    watch_enabled: bool
    record_types: tuple[str, ...]
    #: Все типы записей с подписями — для переключателей формы (4.3). Экран получает всё, что ему
    #: нужно, одним вызовом, и шаблону не приходится знать про модель.
    record_type_choices: tuple[tuple[str, str], ...]
    #: Белый список текстом для поля ввода — по записи в строке, в порядке записей (10.2).
    allowlist_text: str
    #: «Группа «Платёжный контур» · проверка раз в 5 минут · последний ответ в 14:38».
    headline: str
    summary_label: str
    summary_tone: str
    settings: tuple[SettingLine, ...]
    allowlist: tuple[AllowlistLine, ...]
    unmatched: tuple[RegistryLine, ...]
    registry: tuple[RegistryLine, ...]
    events: tuple[EventLine, ...]
    registry_filter: RegistryFilter
    history_filter: HistoryFilter
    #: Данные графика следа (9.2) для виджета. Уходят в шаблон одним JSON рядом с элементом.
    trace: TraceData
    #: Почему реестр пуст. Пусто — объяснять нечего: проверки идут или уже прошли.
    empty_reason: str
    #: Ловушка 8.2: пустой список в режиме «При выходе из разрешённых».
    silent_trap: bool


def domain_card(
    *,
    domain_id: int,
    at: datetime,
    registry: RegistryFilter = RegistryFilter.ALL,
    history: HistoryFilter = HistoryFilter.ALL,
) -> DomainCard | None:
    """
    Карточка домена. ``None`` — домена нет, и это ответ 404, а не ошибка.

    Фильтры касаются только списков. Сводка в заголовке панели и блок нерешённых считаются по
    всему реестру: «1 из 3 адресов не решён» обязано означать одно и то же независимо от того,
    какой сегмент выбран.
    """
    domain = Domain.objects.select_related('group').filter(pk=domain_id).first()
    if domain is None:
        return None

    settings_row = GlobalSettings.objects.get()
    effective = settings_of_group(domain.group, settings_row)
    # Q-D27: при снятом флаге адресов IPv6 не существует — ни в реестре, ни на экране.
    skip_ipv6 = not settings_row.check_ipv6
    statuses = address_statuses(
        domain.pk, at, effective.freshness_threshold.value, skip_ipv6=skip_ipv6
    )
    entries = tuple(AllowlistEntry.objects.filter(domain=domain).order_by('entry'))
    ordered = _sorted(statuses)
    lines = tuple(_registry_line(view, at) for view in ordered)

    return DomainCard(
        id=domain.pk,
        name=domain.name,
        group_name=domain.group.name if domain.group else '',
        group_id=domain.group_id,
        comment=domain.comment,
        watch_enabled=domain.watch_enabled,
        record_types=tuple(domain.record_types),
        record_type_choices=tuple((value, str(label)) for value, label in RECORD_TYPE_CHOICES),
        allowlist_text='\n'.join(entry.raw for entry in entries),
        headline=_headline(domain, effective),
        summary_label=_summary_label(statuses),
        summary_tone=_summary_tone(statuses),
        settings=_settings(effective),
        allowlist=_allowlist(
            entries, statuses, at, answered=domain.last_successful_check_at is not None
        ),
        unmatched=tuple(
            line
            for line, view in zip(lines, ordered, strict=True)
            if view.status == AddressStatus.UNMATCHED
        ),
        registry=_filtered_registry(lines, ordered, registry),
        events=_events(domain, history),
        registry_filter=registry,
        history_filter=history,
        trace=trace_of_domain(
            domain_id=domain.pk,
            at=at,
            statuses={str(view.address): str(view.status) for view in statuses},
        ),
        empty_reason=_empty_reason(domain, statuses),
        silent_trap=not entries
        and effective.notification_mode.value == NotificationMode.NOT_ALLOWED,
    )


def _empty_reason(domain: Domain, statuses: tuple[AddressStatusView, ...]) -> str:
    """Почему в реестре пусто. Пустая таблица сама по себе ничего не объясняет."""
    if statuses:
        return ''
    if not checks_are_possible():
        return (
            'Проверки не идут: в общих настройках пуст список DNS-серверов. Пока он пуст, '
            'спрашивать некого, и реестр останется пустым.'
        )
    if not domain.watch_enabled:
        return 'Наблюдение приостановлено, поэтому домен не проверяется.'
    if domain.next_check_at is None:
        return 'Время проверки не назначено. Нажмите «Проверить сейчас».'
    return 'Домен ещё не проверялся. Первая проверка пройдёт в ближайшие секунды.'


def _filtered_registry(
    lines: tuple[RegistryLine, ...],
    ordered: tuple[AddressStatusView, ...],
    chosen: RegistryFilter,
) -> tuple[RegistryLine, ...]:
    statuses = REGISTRY_STATUSES.get(chosen)
    if statuses is None:
        return lines
    return tuple(
        line
        for line, view in zip(lines, ordered, strict=True)
        if view.status in statuses
    )


def _sorted(statuses: tuple[AddressStatusView, ...]) -> tuple[AddressStatusView, ...]:
    """Сначала актуальные и нерешённые, потом всё остальное — как в прототипе."""
    order = {
        AddressStatus.UNMATCHED: 0,
        AddressStatus.MATCHED: 1,
        AddressStatus.NOT_CHECKED: 1,
        AddressStatus.STALE: 2,
    }
    return tuple(sorted(statuses, key=lambda view: (order[view.status], str(view.address))))


def _headline(domain: Domain, effective: EffectiveSettings) -> str:
    """Подзаголовок карточки. Формулировка прототипа, значения — настоящие."""
    group = f'Группа «{domain.group.name}»' if domain.group else 'Без группы'
    period = f'проверка раз в {humanize_duration(effective.check_period.value)}'
    if domain.last_successful_check_at is None:
        return f'{group} · {period} · ответов ещё не было'
    # localtime: ТЗ 4.0 требует показывать в часовом поясе установки, а форматирование в
    # Python идёт мимо перевода, который Django делает в шаблоне.
    answered_at = localtime(domain.last_successful_check_at)
    return f'{group} · {period} · последний ответ в {answered_at:%H:%M}'


def _summary_label(statuses: tuple[AddressStatusView, ...]) -> str:
    """«1 из 3 адресов не решён» — заголовок панели разрешённых адресов."""
    fresh = [view for view in statuses if view.status != AddressStatus.STALE]
    if not fresh:
        return 'Актуальных адресов нет'
    unmatched = sum(1 for view in fresh if view.status == AddressStatus.UNMATCHED)
    word = plural(len(fresh), 'адреса', 'адресов', 'адресов')
    if unmatched:
        return f'{unmatched} из {len(fresh)} {word} не решён'
    if fresh[0].status == AddressStatus.NOT_CHECKED:
        return 'Разрешённые адреса не заданы — сравнивать не с чем'
    return f'Все {len(fresh)} {word} решены' if len(fresh) > 1 else 'Адрес решён'


def _summary_tone(statuses: tuple[AddressStatusView, ...]) -> str:
    fresh = [view for view in statuses if view.status != AddressStatus.STALE]
    if any(view.status == AddressStatus.UNMATCHED for view in fresh):
        return 'breach'
    if fresh and fresh[0].status == AddressStatus.NOT_CHECKED:
        # Не «решено» и не «сломано», а «требует внимания»: домен под наблюдением, а сравнивать
        # его адреса не с чем (уточнение Q-D15). Зелёным это красить нельзя, серым — мало.
        return 'change'
    return 'ok' if fresh else ''


def _settings(effective: EffectiveSettings) -> tuple[SettingLine, ...]:
    """Блок «Взято из группы». Происхождение показывается у каждой строки (Q-C13)."""
    reminder = effective.reminder_interval.value
    return (
        SettingLine(
            label='Проверка',
            value=f'раз в {humanize_duration(effective.check_period.value)}',
            origin=effective.check_period.origin,
        ),
        SettingLine(
            label='Считать пропавшим',
            value=f'через {humanize_duration(effective.freshness_threshold.value)}',
            origin=effective.freshness_threshold.origin,
        ),
        SettingLine(
            label='Уведомлять',
            value=str(NotificationMode(effective.notification_mode.value).label),
            origin=effective.notification_mode.origin,
        ),
        SettingLine(
            label='Сообщать о возврате в норму',
            value='да' if effective.notify_back_to_normal.value else 'нет',
            origin=effective.notify_back_to_normal.origin,
        ),
        SettingLine(
            label='Напоминать',
            value=f'раз в {humanize_duration(reminder)}' if reminder else 'не напоминать',
            origin=effective.reminder_interval.origin,
        ),
    )


def _allowlist(
    entries: tuple[AllowlistEntry, ...],
    statuses: tuple[AddressStatusView, ...],
    at: datetime,
    *,
    answered: bool,
) -> tuple[AllowlistLine, ...]:
    """Записи списка вместе с адресами, которые в них попали."""
    return tuple(_allowlist_line(entry, statuses, at, answered=answered) for entry in entries)


def _allowlist_line(
    entry: AllowlistEntry,
    statuses: tuple[AddressStatusView, ...],
    at: datetime,
    *,
    answered: bool,
) -> AllowlistLine:
    inside = tuple(
        view for view in statuses if view.match is not None and view.match.entry == entry.raw
    )
    network = _network_of(entry)
    is_network = network.prefixlen < network.max_prefixlen
    last_seen = max((view.last_seen_at for view in inside), default=None)

    return AllowlistLine(
        raw=entry.raw,
        is_network=is_network,
        range_text=_range_text(entry) if is_network else '',
        inside=tuple(str(view.address) for view in inside),
        stale_days=_stale_days(last_seen, at),
        never_matched=answered and not inside,
        ruler=_ruler(entry, inside) if is_network else None,
    )


def _ruler(entry: AllowlistEntry, inside: tuple[AddressStatusView, ...]) -> RulerData:
    """
    Данные линейки подсети — ТЗ 11.4.

    Позиции считаются здесь, а не в браузере: адрес IPv6 — это 128 бит, и в числе JavaScript он
    не помещается. Доля диапазона вычисляется целочисленно и уходит готовой.
    """
    network = _network_of(entry)
    start = int(network.network_address)
    span = network.num_addresses - 1

    return RulerData(
        entry=entry.raw,
        first=str(network[0]),
        middle=str(network[span // 2]),
        last=str(network[-1]),
        size=network.num_addresses,
        marks=[
            RulerMark(
                address=str(view.address),
                position=(int(view.address) - start) / span if span else 0.0,
                status=str(view.status),
            )
            for view in inside
        ],
    )


def _network_of(entry: AllowlistEntry) -> IPNetwork:
    """
    Колонка `NOT NULL`, поэтому `None` в данных не бывает.

    Необязательность приходит из объявления поля, которое в общем случае допускает `NULL`, а не
    из схемы — ровно как в селекторе статусов адресов.
    """
    return cast('IPNetwork', entry.entry)


def _range_text(entry: AllowlistEntry) -> str:
    """«10.20.0.0 — 10.20.3.255 · 1024 адреса» — подпись линейки из прототипа."""
    network = _network_of(entry)
    size = network.num_addresses
    word = plural(size, 'адрес', 'адреса', 'адресов')
    return f'{network[0]} — {network[-1]} · {size} {word}'


def _stale_days(last_seen: datetime | None, at: datetime) -> int | None:
    """
    Q-13.5: запись, под которую давно ничего не приходит, подсвечивается.

    ``None`` и когда адреса приходят, и когда их не было никогда: «не отдаётся 30 дней» про
    запись, под которую вообще ни разу ничего не пришло, было бы неправдой.
    """
    if last_seen is None:
        return None
    idle = at - last_seen
    return idle.days if idle >= STALE_ENTRY_AFTER else None


def _registry_line(view: AddressStatusView, at: datetime) -> RegistryLine:
    label, tone = STATUS_LABELS[view.status]
    return RegistryLine(
        address=str(view.address),
        match_text=_match_text(view),
        status_label=label,
        status_tone=tone,
        chip_css=_chip_css(view.status),
        first_seen_at=view.first_seen_at,
        last_seen_at=view.last_seen_at,
        age_text=_age_text(view, at),
    )


def _match_text(view: AddressStatusView) -> str:
    """
    ТЗ 6.3 и критерий 46: «входит в» только у подсети.

    У записи без маски — «точное совпадение с …», и формулировка «входит в» там запрещена.
    """
    if view.status == AddressStatus.NOT_CHECKED:
        return 'список не задан'
    if view.match is None:
        return 'нет совпадений'
    if view.match.is_network:
        return f'входит в {view.match.entry}'
    return f'точное совпадение с {view.match.entry}'


def _chip_css(status: AddressStatus) -> str:
    if status == AddressStatus.STALE:
        return 'gone'
    return 'out' if status == AddressStatus.UNMATCHED else ''


def _age_text(view: AddressStatusView, at: datetime) -> str:
    """Подпись под статусом: когда адрес отдавался в последний раз."""
    if view.status == AddressStatus.STALE:
        days = (at - view.last_seen_at).days
        return f"не отдаётся {days} {plural(days, 'день', 'дня', 'дней')}"
    if view.first_seen_at == view.last_seen_at:
        return 'появился впервые за всё время'
    return f'отдавался {humanize_duration(at - view.last_seen_at)} назад'


def _events(domain: Domain, chosen: HistoryFilter) -> tuple[EventLine, ...]:
    """Панель «История» — события домена, новые сверху (7.1)."""
    selected = Event.objects.filter(domain=domain)
    types = HISTORY_TYPES.get(chosen)
    if types is not None:
        selected = selected.filter(event_type__in=[item.value for item in types])

    events = (
        selected.order_by('-occurred_at')[:EVENT_LIMIT]
        .values('event_type', 'occurred_at', 'payload', 'author_name')
    )
    return tuple(
        EventLine(
            occurred_at=row['occurred_at'],
            label=str(EventType(row['event_type']).label),
            tone=EVENT_TONES[EventType(row['event_type'])],
            addresses=tuple(str(item) for item in (row['payload'] or {}).get('addresses', ())),
            author_name=row['author_name'],
        )
        for row in events
    )
