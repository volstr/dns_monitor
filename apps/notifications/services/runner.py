"""Сборка канала и один проход по очереди — ТЗ 8.3, 11.10.

Композиция для команды `run_notifier`: сама команда разбирает аргументы и зовёт сервис, логики
не содержит (`CLAUDE.md`).

Токен бота читается из окружения (Q-A2): по 8.3 это **подключение**, а не адресат, и из
интерфейса не меняется. Пустое значение — канал «Не настроен», и это нормальный режим работы,
а не ошибка конфигурации (11.10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils.timezone import localtime

from apps.notifications.services.notifier import DeliveryRun, deliver_due
from apps.notifications.services.reminders import queue_due_reminders
from core.adapters.telegram import TelegramChannel
from core.ports.clock import Clock
from core.ports.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliverySkipped:
    """Проход не состоялся: отправлять нечем. Причина уходит в структурный лог."""

    reason: str


def run_one_delivery(
    *, clock: Clock, state_store: StateStore
) -> DeliveryRun | DeliverySkipped:
    """Один проход по очереди исходящих."""
    channel = TelegramChannel(settings.ENV.telegram_bot_token)
    if not channel.is_configured:
        reason = "TELEGRAM_BOT_TOKEN не задан: канал не настроен, отправлять нечем"
        logger.warning(reason)
        return DeliverySkipped(reason=reason)

    at = clock.now()
    # Напоминания ставятся в ту же очередь и тем же проходом: по 8.5 это обычные сообщения,
    # просто повторные, и по Q-D9 они так же проходят через окно слияния.
    queue_due_reminders(at=at, state_store=state_store)
    return deliver_due(at=at, channel=channel, state_store=state_store)


def describe(result: DeliveryRun | DeliverySkipped, at: datetime) -> str:
    """Строка структурного лога: по ней видно, что проход вообще происходил.

    Время местное, как и в метке `ts` рядом (Q-D34).
    """
    moment = localtime(at)
    if isinstance(result, DeliverySkipped):
        return f"доставка пропущена в {moment:%H:%M:%S}: {result.reason}"
    return (
        f"доставка в {moment:%H:%M:%S}: отправлено {result.sent}, "
        f"слито {result.merged}, отложено {result.failed}, брошено {result.gave_up}"
    )
