"""Полезная нагрузка события — ТЗ 4.5.

Структура уходит в JSON, поэтому описана ``TypedDict``, а не свободным словарём. Состав
пополнится на этапе 3, когда появятся сами события: пока названы только поля, которые ТЗ
называет прямо — «какие адреса затронуты» (4.5) и число зафиксированных адресов в
``WATCH_STARTED`` (7.4).
"""

from __future__ import annotations

from typing import TypedDict


class EventPayload(TypedDict, total=False):
    addresses: list[str]
    address_count: int
    unresolved_count: int
