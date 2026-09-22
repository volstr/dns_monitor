"""Тестовые часы: время двигается явно.

Нужны критериям 6–9 и 17, где внутри теста проходят сутки: ``sleep`` там недопустим, а момент
времени передаётся в доменный код явно именно ради этого.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.ports.clock import Clock


class FakeClock(Clock):
    def __init__(self, moment: datetime) -> None:
        self._moment = _aware(moment)

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = _aware(moment)

    def advance(self, delta: timedelta) -> datetime:
        """Сдвинуть часы вперёд и вернуть новый момент."""
        self._moment += delta
        return self._moment


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        message = "Момент времени обязан быть с часовым поясом: хранение в UTC (ТЗ 4.0)"
        raise ValueError(message)
    return moment
