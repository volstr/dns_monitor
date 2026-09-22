"""Сборка портов и один тик по настройкам установки — ТЗ 6.1, 11.1.

Здесь живёт композиция: команда `run_checker` должна разбирать аргументы и звать сервис, а не
собирать зависимости сама (`CLAUDE.md`).

Список DNS-серверов и предел параллелизма читаются **на каждом тике**, а не при старте процесса:
администратор меняет их в интерфейсе без перезапуска (11.10), и перечитывание стоит одного
запроса к уже загруженной строке настроек.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.monitoring.services.tick import TickReport, tick
from core.adapters.dns_resolver import DnsResolver
from core.ports.clock import Clock
from core.ports.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TickSkipped:
    """Тик не состоялся: проверять нечем или некому. Причина уходит в структурный лог."""

    reason: str


def run_one_tick(*, clock: Clock, state_store: StateStore) -> TickReport | TickSkipped:
    """Один обход. Возвращает отчёт либо причину, по которой обход не состоялся."""
    global_settings = GlobalSettings.objects.get()
    servers = list(global_settings.dns_servers)
    if not servers:
        # На свежей установке список пуст (4.1 значения по умолчанию не задаёт). Падать каждые
        # пять секунд бессмысленно: жалуемся понятно и ждём, пока администратор его заполнит.
        reason = "список DNS-серверов пуст: проверки невозможны, заполните его в настройках"
        logger.warning(reason)
        return TickSkipped(reason=reason)

    resolver = DnsResolver(servers=servers, timeout_ms=settings.ENV.dns_timeout_ms)
    return tick(
        at=clock.now(),
        resolver=resolver,
        state_store=state_store,
        concurrency=settings.ENV.resolve_concurrency,
    )


def describe(report: TickReport | TickSkipped, at: datetime) -> str:
    """Строка для структурного лога: по ней видно, что тик вообще происходил."""
    if isinstance(report, TickSkipped):
        return f"тик пропущен в {at:%H:%M:%S}: {report.reason}"
    return (
        f"тик в {at:%H:%M:%S}: взято {report.claimed}, проверено {report.checked}, "
        f"пропущено {report.skipped}, упало {report.failed}"
    )
