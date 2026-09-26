"""Актуальный набор, вхождение в белый список и статусы адресов — ТЗ 6.2, 6.3, 6.4.

Вхождение считается **базой**, а не Python: аннотация разворачивается в коррелированный подзапрос
`entry >>= address`, который ложится на индекс GiST `inet_ops`. Поэтому статусы всего реестра
домена — один запрос, а не запрос на адрес.

Сравнение семейств адресов отдельной проверки не требует: PostgreSQL возвращает для `inet` разных
семейств ложь, а не ошибку, и требование 6.3 «в пределах одного семейства» выполняется само
(критерий 5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from django.db.models import Exists, F, Func, IntegerField, OuterRef, QuerySet, Subquery

from apps.catalog.models import AllowlistEntry
from apps.monitoring.models import AddressRegistryEntry
from core.fields import CidrField
from core.types.checking import AddressStatus, AddressStatusView, AllowlistMatch
from core.types.dns import IPAddress, IPNetwork

#: Значение, которое возвращает `family()` для адреса IPv4.
IPV4 = 4


def without_ipv6(queryset: QuerySet[AddressRegistryEntry]) -> QuerySet[AddressRegistryEntry]:
    """Убрать из выборки адреса IPv6 — решение Q-D27.

    Снятый флаг «Проверять адреса IPv6» означает не «перестать обновлять», а «их не существует».
    Разница видна сразу: если просто перестать спрашивать AAAA, прежние адреса из реестра
    никуда не денутся, через порог актуальности станут неактуальными и породят пачку
    `ADDRESS_GONE` — ровно тот шум, ради отсутствия которого флаг и снимают.

    Семейство спрашивается у PostgreSQL функцией `family()`, а не разбором текста в Python:
    колонка типа `inet` знает его сама.
    """
    return queryset.annotate(
        family=Func(F("address"), function="family", output_field=IntegerField())
    ).filter(family=IPV4)


def fresh_set(
    domain_id: int,
    at: datetime,
    freshness_threshold: timedelta,
    *,
    skip_ipv6: bool = False,
) -> frozenset[IPAddress]:
    """Актуальный набор — ТЗ 6.2: адреса, отдававшиеся хотя бы раз за порог актуальности.

    Формула раздела 6.2: `{ ip из реестра : ip.последнее_обнаружение >= T - W }`.
    """
    selected = AddressRegistryEntry.objects.filter(
        domain_id=domain_id, last_seen_at__gte=at - freshness_threshold
    )
    if skip_ipv6:
        selected = without_ipv6(selected)
    addresses = selected.values_list("address", flat=True)
    # Колонка NOT NULL, поэтому None в выборке не появится: Optional приходит из объявления
    # поля, которое в общем случае допускает NULL, а не из данных.
    return frozenset(cast("Iterable[IPAddress]", addresses))


def address_statuses(
    domain_id: int,
    at: datetime,
    freshness_threshold: timedelta,
    *,
    skip_ipv6: bool = False,
) -> tuple[AddressStatusView, ...]:
    """Статусы всех адресов реестра домена — ТЗ 6.4. Один запрос на весь реестр."""
    matched = AllowlistEntry.objects.filter(
        domain=OuterRef("domain"), entry__net_contains_or_equals=OuterRef("address")
    )
    selected = AddressRegistryEntry.objects.filter(domain_id=domain_id)
    if skip_ipv6:
        selected = without_ipv6(selected)
    rows = (
        selected.annotate(
            matched_raw=Subquery(matched.values("raw")[:1]),
            matched_entry=Subquery(matched.values("entry")[:1], output_field=CidrField()),
            # Есть ли у домена список вообще — тем же запросом, а не отдельным `exists()`.
            has_allowlist=Exists(AllowlistEntry.objects.filter(domain=OuterRef("domain"))),
        )
        .values(
            "address",
            "first_seen_at",
            "last_seen_at",
            "matched_raw",
            "matched_entry",
            "has_allowlist",
        )
        .order_by("address")
    )

    border = at - freshness_threshold
    return tuple(
        AddressStatusView(
            address=row["address"],
            status=_status(
                is_fresh=row["last_seen_at"] >= border,
                has_match=row["matched_raw"] is not None,
                has_allowlist=row["has_allowlist"],
            ),
            match=_match(row["matched_raw"], row["matched_entry"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
        )
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class RegistryRow:
    """Строка реестра для списка доменов. От `AddressStatusView` отличается `first_seen_at`.

    Момент первого обнаружения нужен пометке «новый» в списке (Q-C22), а причина совпадения —
    наоборот, не нужна: в списке она не показывается, там только чипы адресов.
    """

    address: IPAddress
    status: AddressStatus
    first_seen_at: datetime
    last_seen_at: datetime


def registry_of_domains(
    domain_ids: Sequence[int],
    at: datetime,
    thresholds: Mapping[int, timedelta],
    *,
    skip_ipv6: bool = False,
) -> dict[int, tuple[RegistryRow, ...]]:
    """Реестры сразу нескольких доменов — один запрос на всю страницу списка.

    Порог актуальности у каждого домена свой: он наследуется от группы (ТЗ 5), поэтому граница
    `T - W` считается отдельно для каждого и приходит в `thresholds`.
    """
    if not domain_ids:
        return {}

    matched = AllowlistEntry.objects.filter(
        domain=OuterRef("domain"), entry__net_contains_or_equals=OuterRef("address")
    )
    selected = AddressRegistryEntry.objects.filter(domain_id__in=domain_ids)
    if skip_ipv6:
        selected = without_ipv6(selected)
    rows = (
        selected.annotate(
            has_match=Exists(matched),
            has_allowlist=Exists(AllowlistEntry.objects.filter(domain=OuterRef("domain"))),
        )
        .values(
            "domain_id",
            "address",
            "first_seen_at",
            "last_seen_at",
            "has_match",
            "has_allowlist",
        )
        .order_by("domain_id", "address")
    )

    collected: dict[int, list[RegistryRow]] = {}
    for row in rows:
        domain_id = row["domain_id"]
        border = at - thresholds[domain_id]
        collected.setdefault(domain_id, []).append(
            RegistryRow(
                address=row["address"],
                status=_status(
                    is_fresh=row["last_seen_at"] >= border,
                    has_match=row["has_match"],
                    has_allowlist=row["has_allowlist"],
                ),
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
            )
        )
    return {domain_id: tuple(rows) for domain_id, rows in collected.items()}


def _status(*, is_fresh: bool, has_match: bool, has_allowlist: bool) -> AddressStatus:
    """Приоритет сверху вниз — ТЗ 6.4, плюс пустой список по 8.2 (Q-D25)."""
    if not is_fresh:
        return AddressStatus.STALE
    if not has_allowlist:
        # Сравнивать не с чем. Иначе «не совпал» получил бы каждый адрес каждого домена без
        # списка, и 8.2 («не пришлёт уведомление никогда») было бы нарушено.
        return AddressStatus.NOT_CHECKED
    return AddressStatus.MATCHED if has_match else AddressStatus.UNMATCHED


def _match(raw: str | None, entry: IPNetwork | None) -> AllowlistMatch | None:
    """Причина совпадения остаётся видимой и у неактуального адреса: 6.4 меняет статус, а не факт.

    Это соответствует и прототипу, где у неактуального адреса подписано «точное совпадение с …».
    """
    if raw is None or entry is None:
        return None
    return AllowlistMatch(entry=raw, is_network=entry.prefixlen < entry.max_prefixlen)
