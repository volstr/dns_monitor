"""Базовые типы DNS — ТЗ 4.0, 4.3.

Лежат в ``core``, потому что их знают и порты, и доменный код, и модели. Обратной зависимости
нет: ``core`` про ``apps`` ничего не знает.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class RecordType(StrEnum):
    """Типы запрашиваемых записей — ТЗ 4.3.

    CNAME по Q-D12 не добавляет ни событий, ни записей реестра: включённый флаг означает лишь
    «показывать цель», которая и так приходит в ответе на A-запрос.
    """

    A = "a"
    AAAA = "aaaa"
    CNAME = "cname"
