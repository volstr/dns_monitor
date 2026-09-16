"""Процесс доставки уведомлений.

TODO(ТЗ 8.3, plan.md этап 5): разбор очереди исходящих из PostgreSQL с экспоненциальной задержкой
и конечным числом попыток. Пока команда только держит процесс живым, чтобы сервис ``notifier``
в компоузе поднимался и корректно останавливался.
"""

import logging
import signal
import threading
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Доставка уведомлений (ТЗ 8.3). Пока заглушка."

    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        logger.warning("run_notifier: доставка не реализована (TODO ТЗ 8.3), ожидаю остановки")
        stop.wait()
