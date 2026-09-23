"""Актуальный набор, вхождение в белый список и статусы адресов — ТЗ 6.2, 6.3, 6.4.

Вхождение считается **базой**, а не Python: аннотация разворачивается в коррелированный подзапрос
`entry >>= address`, который ложится на индекс GiST `inet_ops`. Поэтому статусы всего реестра
домена — один запрос, а не запрос на адрес.

Сравнение семейств адресов отдельной проверки не требует: PostgreSQL возвращает для `inet` разных
семейств ложь, а не ошибку, и требование 6.3 «в пределах одного семейства» выполняется само
(критерий 5).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import cast

from django.db.models import Exists, OuterRef, Subquery

from apps.catalog.models import AllowlistEntry
from apps.monitoring.models import AddressRegistryEntry
from core.fields import CidrField
from core.types.checking import AddressStatus, AddressStatusView, AllowlistMatch
from core.types.dns import IPAddress, IPNetwork


def fresh_set(
    domain_id: int, at: datetime, freshness_threshold: timedelta
) -> frozenset[IPAddress]:
    """Актуальный набор — ТЗ 6.2: адреса, отдававшиеся хотя бы раз за порог актуальности.

    Формула раздела 6.2: `{ ip из реестра : ip.последнее_обнаружение >= T - W }`.
    """
    addresses = AddressRegistryEntry.objects.filter(
        domain_id=domain_id, last_seen_at__gte=at - freshness_threshold
    ).values_list("address", flat=True)
    # Колонка NOT NULL, поэтому None в выборке не появится: Optional приходит из объявления
    # поля, которое в общем случае допускает NULL, а не из данных.
    return frozenset(cast("Iterable[IPAddress]", addresses))


def address_statuses(
    domain_id: int, at: datetime, freshness_threshold: timedelta
) -> tuple[AddressStatusView, ...]:
    """Статусы всех адресов реестра домена — ТЗ 6.4. Один запрос на весь реестр."""
    matched = AllowlistEntry.objects.filter(
        domain=OuterRef("domain"), entry__net_contains_or_equals=OuterRef("address")
    )
    rows = (
        AddressRegistryEntry.objects.filter(domain_id=domain_id)
        .annotate(
            matched_raw=Subquery(matched.values("raw")[:1]),
            matched_entry=Subquery(matched.values("entry")[:1], output_field=CidrField()),
            # Есть ли у домена список вообще — тем же запросом, а не отдельным `exists()`.
            has_allowlist=Exists(AllowlistEntry.objects.filter(domain=OuterRef("domain"))),
        )
        .values("address", "last_seen_at", "matched_raw", "matched_entry", "has_allowlist")
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
            last_seen_at=row["last_seen_at"],
        )
        for row in rows
    )


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
