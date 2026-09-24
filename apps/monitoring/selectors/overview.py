"""Обзор — ТЗ 11.9, решения Q-C12, Q-E5.

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
from apps.monitoring.models import Incident
from apps.monitoring.selectors.addresses import registry_of_domains
from core.text import humanize_duration
from core.types.checking import AddressStatus


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
class Overview:
    """Экран «Обзор»."""

    open_items: tuple[OpenDiscrepancy, ...]
    #: Когда закрылся последний инцидент. Нужен строке «Открытых расхождений нет» (11.9).
    last_closed_at: datetime | None
    domains_total: int
    domains_watched: int
    groups_total: int
    last_check_at: datetime | None
    next_check_at: datetime | None
    base_period: str


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

    return Overview(
        open_items=_items(incidents, at, global_settings),
        last_closed_at=(
            Incident.objects.filter(closed_at__isnull=False).aggregate(last=Max("closed_at"))[
                "last"
            ]
            if not incidents
            else None
        ),
        domains_total=Domain.objects.count(),
        domains_watched=Domain.objects.filter(watch_enabled=True).count(),
        groups_total=Group.objects.count(),
        last_check_at=schedule["last"],
        next_check_at=schedule["following"],
        base_period=humanize_duration(timedelta(seconds=global_settings.check_period_seconds)),
    )


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

    registries = registry_of_domains(list(thresholds), at, thresholds)

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
