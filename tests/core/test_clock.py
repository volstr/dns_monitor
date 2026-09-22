"""Порт часов — ТЗ 4.0, CLAUDE.md о единственном месте с `timezone.now()`."""

from datetime import UTC, datetime, timedelta

import pytest

from core.adapters.fake_clock import FakeClock
from core.adapters.system_clock import SystemClock

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)


def test_fake_clock_returns_the_moment_it_was_given() -> None:
    clock = FakeClock(MOMENT)

    assert clock.now() == MOMENT
    # Повторный вызов не сдвигает время сам по себе: момент меняется только явно.
    assert clock.now() == MOMENT


def test_fake_clock_advances_a_day_without_sleeping() -> None:
    # Критерии 6–9 и 17 прогоняют сутки внутри теста — это возможно только так.
    clock = FakeClock(MOMENT)

    moment = clock.advance(timedelta(days=1))

    assert moment == MOMENT + timedelta(days=1)
    assert clock.now() == MOMENT + timedelta(days=1)


def test_fake_clock_can_be_set_to_an_exact_moment() -> None:
    clock = FakeClock(MOMENT)

    clock.set(MOMENT + timedelta(hours=3))

    assert clock.now() == MOMENT + timedelta(hours=3)


def test_fake_clock_rejects_naive_time() -> None:
    # ТЗ 4.0: метки времени — тип с часовым поясом, хранение в UTC.
    with pytest.raises(ValueError, match="часовым поясом"):
        FakeClock(datetime(2026, 9, 22, 14, 38))


def test_system_clock_returns_aware_time() -> None:
    assert SystemClock().now().tzinfo is not None
