"""
Значение настройки вместе с происхождением — ТЗ 5.

Происхождение нужно интерфейсу: раздел 5 требует показывать «общая», «от группы» или «своё»,
а 11.8 повторяет это на карточке домена. Поэтому оно часть результата, а не вычисляется
в шаблоне.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SettingOrigin(StrEnum):
    """Откуда взялось разрешённое значение (ТЗ 5)."""

    GLOBAL = 'global'
    GROUP = 'group'
    OWN = 'own'


@dataclass(frozen=True, slots=True)
class EffectiveSetting[T]:
    value: T
    origin: SettingOrigin
