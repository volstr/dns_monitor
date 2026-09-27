"""
Счётчики в боковом меню — макет прототипа, ТЗ 11.9, решение Q-E5.

Прототип показывает у пунктов числа: открытые расхождения (красным), домены, группы, участники.
Q-E5 отнёс их сюда же, где считается обзор: значения те же, считать их дважды незачем.

Четыре счётчика — четыре дешёвых `COUNT`. По 11.12 им полагается ключ `cache:view:overview`
со сроком 15 секунд; кэш появится вместе с остальным кэшированием, и тогда это место станет
его единственным потребителем.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.accounts.models import Member
from apps.catalog.models import Domain, Group
from apps.monitoring.models import Incident


@dataclass(frozen=True, slots=True)
class NavigationCounts:
    """Числа у пунктов меню. Ноль означает «не показывать»: пустой значок хуже его отсутствия."""

    discrepancies: int
    domains: int
    groups: int
    members: int


def navigation_counts() -> NavigationCounts:
    return NavigationCounts(
        discrepancies=Incident.objects.filter(closed_at__isnull=True).count(),
        domains=Domain.objects.count(),
        groups=Group.objects.count(),
        members=Member.objects.filter(is_active=True).count(),
    )
