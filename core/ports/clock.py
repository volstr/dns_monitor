"""
Порт часов — единственный источник текущего момента.

``timezone.now()`` встречается ровно в одной реализации этого порта (``SystemClock``). Всё
остальное получает момент времени явно: без этого критерии 6–9 и 17 нельзя прогнать за секунды,
а они требуют суток модельного времени.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущий момент в UTC, всегда с часовым поясом (ТЗ 4.0)."""
        ...
