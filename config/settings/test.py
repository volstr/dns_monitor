"""Режим автотестов: ``pytest --ds=config.settings.test``."""

import dataclasses

from .base import *  # noqa: F403
from .base import APP_HOST, ENV

ALLOWED_HOSTS = ["testserver", APP_HOST]

# Тик идёт последовательным путём — так plan.md и требует вызывать его из теста. Причина не в
# удобстве: рабочие потоки получают собственные соединения с БД и не видят незакоммиченных
# данных обычного django_db-теста. Путь через ThreadPoolExecutor покрыт отдельным тестом с
# настоящими транзакциями (tests/monitoring/test_tick.py).
ENV = dataclasses.replace(ENV, resolve_concurrency=1)
