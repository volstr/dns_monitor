"""
Процесс доставки уведомлений — ТЗ 8.3.

Отдельный процесс от проверок: недоступность канала не должна занимать потоки резолвера
(plan.md, этап 5). Команда разбирает аргументы и зовёт сервис, логики не содержит (`CLAUDE.md`).

Очередь живёт в PostgreSQL, повторы считает она же: экспоненциальная задержка — арифметика над
`next_attempt_at`, конечность попыток — условие запроса (Q-D24).
"""

import logging
import signal
import threading
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.notifications.services.runner import describe, run_one_delivery
from core.adapters.redis_state import RedisStateStore
from core.adapters.system_clock import SystemClock

logger = logging.getLogger(__name__)

#: Проход по очереди раз в 5 секунд. Окно слияния по умолчанию 60 секунд, так что задержка
#: доставки от этого шага пренебрежима.
POLL_INTERVAL_SECONDS = 5.0


class Command(BaseCommand):
    help = 'Доставка уведомлений из очереди исходящих (ТЗ 8.3)'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--once',
            action='store_true',
            help='выполнить один проход и выйти — для отладки и ручного прогона',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        clock = SystemClock()
        state_store = RedisStateStore.from_url(
            settings.ENV.redis_url, db=settings.ENV.redis_db_state
        )

        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            logger.info('run_notifier: получен сигнал, останавливаюсь')
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        while not stop.is_set():
            at = clock.now()
            result = run_one_delivery(clock=clock, state_store=state_store)
            logger.info(describe(result, at))
            if options['once']:
                return
            stop.wait(POLL_INTERVAL_SECONDS)
