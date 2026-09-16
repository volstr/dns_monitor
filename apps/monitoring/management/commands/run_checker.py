"""Процесс планировщика проверок.

TODO(ТЗ 11.1, plan.md этап 4): тик раз в 5–10 секунд, выборка наступивших доменов с
``FOR UPDATE SKIP LOCKED``, резолв в ``ThreadPoolExecutor``. Пока команда только держит процесс
живым, чтобы сервис ``checker`` в компоузе поднимался и корректно останавливался.
"""

import logging
import signal
import threading
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Планировщик проверок доменов (ТЗ 11.1). Пока заглушка."

    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        logger.warning("run_checker: проверки не реализованы (TODO ТЗ 11.1), ожидаю остановки")
        stop.wait()
