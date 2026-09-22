"""Боевые часы — единственное место во всём проекте с ``timezone.now()``."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from core.ports.clock import Clock


class SystemClock(Clock):
    def now(self) -> datetime:
        return timezone.now()
