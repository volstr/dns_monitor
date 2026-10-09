"""
Процесс планировщика проверок — ТЗ 11.1.

Команда разбирает аргументы и зовёт сервис, логики не содержит (`CLAUDE.md`). Весь обход — это
`run_one_tick`, который вызывается и отсюда, и из тестов, только там момент времени задают явно.

Расписание живёт в `next_check_at`: периодических задач на домен нет, второго места нет.
"""

import logging
import signal
import threading
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.monitoring.services.runner import TickSkipped, describe, run_one_tick
from core.adapters.redis_state import RedisStateStore
from core.adapters.system_clock import SystemClock

logger = logging.getLogger(__name__)

#: Тик раз в 5 секунд (plan.md: 5-10 секунд). Домены разнесены во времени, поэтому частый
#: холостой тик дёшев: он либо забирает наступившие домены, либо не забирает ничего.
TICK_INTERVAL_SECONDS = 5.0


class Command(BaseCommand):
    help = 'Планировщик проверок доменов (ТЗ 11.1)'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--once',
            action='store_true',
            help='выполнить один тик и выйти — для отладки и ручного прогона',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        clock = SystemClock()
        state_store = RedisStateStore.from_url(
            settings.ENV.redis_url, db=settings.ENV.redis_db_state
        )

        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            logger.info('run_checker: получен сигнал, останавливаюсь')
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        # Факт остановки проверок пишется в журнал один раз на аварию, а не каждые пять секунд
        # (11.13), поэтому состояние инфраструктуры переносится между тиками.
        healthy = True
        while not stop.is_set():
            at = clock.now()
            report = run_one_tick(
                clock=clock, state_store=state_store, previously_healthy=healthy
            )
            healthy = report.healthy if isinstance(report, TickSkipped) else True
            logger.info(describe(report, at))
            if options['once']:
                return
            stop.wait(TICK_INTERVAL_SECONDS)
