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
from apps.monitoring.services.health import check_health, record_checks_stopped
from apps.monitoring.services.retention import purge_due
from apps.monitoring.services.tick import TickReport, tick
from apps.notifications.services.outage import send_outage_alert
from core.adapters.dns_resolver import DnsResolver
from core.adapters.telegram import TelegramChannel
from core.observability.report import Failure, report
from core.ports.clock import Clock
from core.ports.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TickSkipped:
    """Тик не состоялся: проверять нечем или некому. Причина уходит в структурный лог."""

    reason: str
    #: Была ли при этом жива инфраструктура. По нему процесс понимает, писать ли в журнал факт
    #: остановки при следующем тике (11.13).
    healthy: bool = True


def run_one_tick(
    *, clock: Clock, state_store: StateStore, previously_healthy: bool = True
) -> TickReport | TickSkipped:
    """Один обход. Возвращает отчёт либо причину, по которой обход не состоялся.

    ``previously_healthy`` держит процесс-вызыватель: факт остановки проверок пишется в журнал
    один раз на аварию, а не каждые пять секунд. Перезапуск процесса посреди аварии даст ещё одну
    запись — это верно по смыслу, старт при неработающем Redis стоит отметить.
    """
    health = check_health(state_store=state_store)
    if not health.healthy:
        # 11.13: проверки останавливаются, и остановка обязана быть заметной.
        reason = f"инфраструктура недоступна: {health.failed_component}"
        logger.error(reason, extra={"component": health.failed_component})
        if previously_healthy:
            # 11.14, критерий 69: авария инфраструктуры — единственное, что уходит **в оба
            # канала**. Дежурным, потому что сервис перестал следить за доменами; в Sentry,
            # потому что разработчикам нужна причина. Группировка — по отказавшему компоненту.
            report(
                Failure(
                    cause=f"infrastructure:{health.failed_component}",
                    message=reason,
                    tags={"component": health.failed_component or "неизвестен"},
                )
            )
            record_checks_stopped(at=clock.now(), report=health)
            # Критерий 65: сообщение уходит в канал напрямую, минуя очередь и окно слияния —
            # компонент, сообщающий об отказе, не может зависеть от отказавшего (11.13).
            send_outage_alert(
                channel=TelegramChannel(settings.ENV.telegram_bot_token),
                component=health.failed_component or "неизвестен",
                detail=health.detail,
            )
        return TickSkipped(reason=reason, healthy=False)

    # Уборка по 11.3 живёт здесь, а не отдельным процессом и не заданием по расписанию:
    # `run_checker` — единственная долгоживущая команда, которая и так идёт по кругу, а своего
    # планировщика в проекте нет и не будет. Чаще раза в час она сама себя не запускает.
    #
    # Стоит **до** проверки списка DNS-серверов: срок хранения не перестаёт истекать оттого,
    # что проверять домены пока нечем.
    _purge(clock.now())

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


def _purge(at: datetime) -> None:
    """Уборка по срокам хранения. Молчит, когда убирать нечего: раз в час это обычное дело."""
    purged = purge_due(at=at)
    if purged is None or not purged.total:
        return
    logger.info(
        "уборка по срокам хранения",
        extra={
            "events": purged.events,
            "audit": purged.audit_records,
            "registry": purged.registry_entries,
            "buckets": purged.hourly_buckets + purged.daily_buckets,
            "notifications": purged.notifications,
        },
    )


def describe(report: TickReport | TickSkipped, at: datetime) -> str:
    """Строка для структурного лога: по ней видно, что тик вообще происходил."""
    if isinstance(report, TickSkipped):
        return f"тик пропущен в {at:%H:%M:%S}: {report.reason}"
    return (
        f"тик в {at:%H:%M:%S}: взято {report.claimed}, проверено {report.checked}, "
        f"пропущено {report.skipped}, упало {report.failed}"
    )
