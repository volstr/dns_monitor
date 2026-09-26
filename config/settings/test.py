"""Режим автотестов: ``pytest --ds=config.settings.test``."""

import dataclasses

from .base import *  # noqa: F403
from .base import APP_HOST, ENV

ALLOWED_HOSTS = ["testserver", APP_HOST]

# Логические базы Redis у тестов свои. Иначе они делят состояние с работающим приложением, и это
# ломает две вещи сразу: ключ кулдауна живёт тридцать секунд и переживает тестовую базу, из-за
# чего повторный прогон в ту же минуту падает на пустом месте; а сессии, которые заводят тесты,
# ложатся туда же, где лежит сессия человека в браузере.
TEST_REDIS_DB_STATE = 15
TEST_REDIS_DB_CACHE = 14

# Тик идёт последовательным путём — так plan.md и требует вызывать его из теста. Причина не в
# удобстве: рабочие потоки получают собственные соединения с БД и не видят незакоммиченных
# данных обычного django_db-теста. Путь через ThreadPoolExecutor покрыт отдельным тестом с
# настоящими транзакциями (tests/monitoring/test_tick.py).
ENV = dataclasses.replace(
    ENV,
    resolve_concurrency=1,
    redis_db_state=TEST_REDIS_DB_STATE,
    redis_db_cache=TEST_REDIS_DB_CACHE,
)
