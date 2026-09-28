"""
Обзор — ТЗ 11.9, решения Q-C12, Q-E5.

11.9 требует именно списка, а не счётчика: «Счётчик „Не решены: 1“ не заменяет списка: он не
говорит ни какой домен, ни как давно». Поэтому здесь список открытых инцидентов, отсортированный
по возрасту по убыванию, с нерешёнными адресами, возрастом, временем последнего сообщения и
переходом к карточке.

Пустого места при отсутствии расхождений тоже быть не должно: 11.9 требует явной строки с датой
последнего закрытого инцидента.

**«Круга проверок» здесь нет** (Q-C12). В модели у каждого домена свой `next_check_at` со
случайным смещением, общего круга не существует, и обещать его в интерфейсе — врать. Вместо него
последняя и ближайшая проверки: это минимум и максимум по одному столбцу.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Max, Min

from apps.catalog.models import Domain, GlobalSettings, Group
from apps.catalog.selectors.settings import settings_of_group
from apps.monitoring.models import Event, EventType, Incident
from apps.monitoring.selectors.addresses import registry_of_domains
from core.text import humanize_duration, plural
from core.types.checking import AddressStatus

#: Окно карточки «Адреса изменились» — подпись в прототипе говорит «за последние сутки».
CHANGED_WINDOW = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class OpenDiscrepancy:
    """Строка блока «Открытые расхождения» — ТЗ 11.9."""

    domain_id: int
    domain_name: str
    addresses: tuple[str, ...]
    opened_at: datetime
    #: «держится 3 ч 12 мин» — формулировка 11.9.
    age_text: str
    last_message_at: datetime | None


@dataclass(frozen=True, slots=True)
class Stat:
    """
    Карточка верхней сводки — разметка прототипа, строки 738–743.

    Три части: подпись, число и пояснение мелким шрифтом под ним. Пояснение обязательно: без
    него «Не решены: 1» — ровно тот счётчик, который 11.9 называет недостаточным.

    ``tone`` — модификатор из палитры прототипа (`s-breach`, `s-change`, `s-fail`); пусто у
    нейтральной карточки. Ноль обесцвечивается намеренно: «не решены: 0» не должно кричать
    красным, иначе спокойное состояние выглядит тревожным.
    """

    label: str
    value: int
    note: str
    tone: str


@dataclass(frozen=True, slots=True)
class Overview:
    """Экран «Обзор»."""

    open_items: tuple[OpenDiscrepancy, ...]
    #: Когда закрылся последний инцидент. Нужен строке «Открытых расхождений нет» (11.9).
    last_closed_at: datetime | None
    #: Сколько инцидентов уже исправлено, но формально ещё открыто: нерешённых адресов у них
    #: не осталось, а запись закроет следующая проверка (Q-13.2).
    #: Нужен честной строке вместо «расхождений нет»: расхождения правда нет, но запись жива.
    awaiting_close: int
    #: Четыре карточки верхней сводки. Готовы целиком: шаблон ничего не считает.
    stats: tuple[Stat, ...]
    last_check_at: datetime | None
    next_check_at: datetime | None


def overview(*, at: datetime) -> Overview:
    """Собрать обзор. Один проход по открытым инцидентам, их заведомо немного."""
    global_settings = GlobalSettings.objects.get()
    incidents = list(
        Incident.objects.filter(closed_at__isnull=True)
        .select_related("domain", "domain__group")
        .order_by("opened_at")
    )
    schedule = Domain.objects.aggregate(
        last=Max("last_successful_check_at"), following=Min("next_check_at")
    )

    # Открытая запись инцидента и расхождение **сейчас** — не одно и то же. Администратор мог
    # только что разрешить адрес: вхождение считается на лету, и расхождения уже нет, а запись
    # закроет следующая проверка (Q-13.2). Блок 11.9 называется «Открытые
    # расхождения», и показывать в нём запись, у которой не осталось ни одного нерешённого
    # адреса, — значит показывать расхождение, не умея назвать ни одного адреса.
    items = _items(incidents, at, global_settings)
    current = tuple(item for item in items if item.addresses)

    return Overview(
        open_items=current,
        last_closed_at=(
            Incident.objects.filter(closed_at__isnull=False).aggregate(last=Max("closed_at"))[
                "last"
            ]
            if not items
            else None
        ),
        awaiting_close=len(items) - len(current),
        stats=_stats(current, at, global_settings),
        last_check_at=schedule["last"],
        next_check_at=schedule["following"],
    )


def _stats(
    current: tuple[OpenDiscrepancy, ...], at: datetime, global_settings: GlobalSettings
) -> tuple[Stat, ...]:
    """
    Четыре карточки сводки — разметка прототипа, строки 738–743.

    Карточки про **состояние**, а не про опись: сколько под наблюдением, сколько не решено,
    у скольких изменились адреса, сколько не резолвится. Раньше здесь стояли домены, группы и
    базовый период — величины, которые не меняются неделями и ни о чём не предупреждают.
    """
    watched = Domain.objects.filter(watch_enabled=True).count()
    groups = Group.objects.count()
    changed_since = at - CHANGED_WINDOW
    changed = (
        Event.objects.filter(
            occurred_at__gte=changed_since,
            event_type__in=(EventType.ADDRESS_APPEARED, EventType.ADDRESS_GONE),
        )
        .values("domain_id")
        .distinct()
        .count()
    )
    failing = Domain.objects.filter(
        consecutive_resolve_failures__gte=global_settings.resolve_failure_threshold
    )
    attempts = failing.aggregate(worst=Max("consecutive_resolve_failures"))["worst"] or 0

    return (
        Stat(
            label="Под наблюдением",
            value=watched,
            note=f"в {groups} {plural(groups, 'группе', 'группах', 'группах')}",
            tone="",
        ),
        Stat(
            label="Не решены",
            value=len(current),
            note=_unresolved_note(current),
            tone="s-breach" if current else "",
        ),
        Stat(
            label="Адреса изменились",
            value=changed,
            note="за последние сутки",
            tone="s-change" if changed else "",
        ),
        Stat(
            label="Не резолвится",
            value=failing.count(),
            note=(
                f"{attempts} {plural(attempts, 'попытка', 'попытки', 'попыток')} подряд"
                if attempts
                else "все домены отвечают"
            ),
            tone="s-fail" if attempts else "",
        ),
    )


def _unresolved_note(current: tuple[OpenDiscrepancy, ...]) -> str:
    """
    Под числом — имя домена, как в прототипе. Счётчик без имени 11.9 называет недостаточным.

    Расхождения отсортированы по возрасту, поэтому назван самый старый: если разбирать, то с него.
    """
    if not current:
        return "расхождений нет"
    first = current[0].domain_name
    if len(current) == 1:
        return first
    return f"{first} и ещё {len(current) - 1}"


def _items(
    incidents: list[Incident], at: datetime, global_settings: GlobalSettings
) -> tuple[OpenDiscrepancy, ...]:
    if not incidents:
        return ()

    thresholds: dict[int, timedelta] = {}
    resolved: dict[int | None, timedelta] = {}
    for incident in incidents:
        group_id = incident.domain.group_id
        if group_id not in resolved:
            resolved[group_id] = settings_of_group(
                incident.domain.group, global_settings
            ).freshness_threshold.value
        thresholds[incident.domain_id] = resolved[group_id]

    registries = registry_of_domains(
        list(thresholds), at, thresholds, skip_ipv6=not global_settings.check_ipv6
    )

    return tuple(
        OpenDiscrepancy(
            domain_id=incident.domain_id,
            domain_name=incident.domain.name,
            addresses=tuple(
                str(row.address)
                for row in registries.get(incident.domain_id, ())
                if row.status == AddressStatus.UNMATCHED
            ),
            opened_at=incident.opened_at,
            age_text=f"держится {humanize_duration(at - incident.opened_at)}",
            last_message_at=incident.last_message_at,
        )
        for incident in incidents
    )
