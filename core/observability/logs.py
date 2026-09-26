"""Структурные логи — plan.md этап 8; контекст события по ТЗ 11.14.

Запись журнала — строка JSON, а не человеческая фраза. Причина прикладная: три процесса
(`web`, `checker`, `notifier`) пишут в общий поток, а разбирать их приходится по полям —
«покажи всё по домену `pay-gw.contoso.ru`» и «покажи всё из одного обхода». Регулярным
выражением по свободному тексту это делается плохо и ломается от каждой правки формулировки.

**Идентификатор обхода** живёт здесь же и по той же причине, что названа в 11.14: «чтобы все
ошибки одного цикла проверок нашлись вместе». Он лежит в `contextvars`, поэтому попадает и в
запись журнала, и в метку события Sentry, и его не надо протаскивать параметром через каждый
вызов. Поток из пула наследует значение на момент своего запуска — ровно то, что нужно: обход
один, потоки внутри него общие.

Формат выбирается настройками: `json` в бою, `plain` у разработчика, где журнал читают глазами.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from contextvars import ContextVar
from typing import Any

#: Поля, которые `logging` кладёт в запись сам. Всё, чего здесь нет, пришло из `extra` и
#: считается полем предметной области: домен, группа, компонент, номер уведомления.
_BUILTIN: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | frozenset({"message", "asctime", "taskName"})

_run_id: ContextVar[str] = ContextVar("run_id", default="")


def new_run() -> str:
    """Начать обход и получить его идентификатор — ТЗ 11.14, «контекст события»."""
    value = uuid.uuid4().hex[:12]
    _run_id.set(value)
    return value


def current_run() -> str:
    """Идентификатор текущего обхода. Пусто вне обхода — например, в обработчике запроса."""
    return _run_id.get()


class RunIdFilter(logging.Filter):
    """Добавляет идентификатор обхода в каждую запись. Не фильтрует — только дополняет."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run = current_run()
        return True


class JsonFormatter(logging.Formatter):
    """Одна запись — одна строка JSON.

    Время в UTC и по ISO 8601: журнал читают вперемешку с журналами других систем, а часовой
    пояс приложения — настройка **показа** (ТЗ 4.0), к хранению и к логам отношения не имеющая.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _BUILTIN and not key.startswith("_"):
                payload[key] = _plain(value)

        # `ensure_ascii=False`: имена доменов и тексты отказов по-русски должны остаться
        # читаемыми, а не превратиться в `\uXXXX` на всю строку.
        return json.dumps(payload, ensure_ascii=False, default=str)


def _plain(value: object) -> object:
    """Значение, которое переживёт JSON. Остальное уйдёт через `default=str`."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return str(value)
