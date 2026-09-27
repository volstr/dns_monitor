"""
Уведомление об аварии инфраструктуры — ТЗ 11.13, критерий 65.

**Отправляется напрямую, минуя очередь и окно слияния.** Требование раздела сформулировано
прямо: «компонент, сообщающий об отказе, не может зависеть от отказавшего». Окно слияния живёт
в Redis, а Redis — как раз тот компонент, который мог отказать; задержка на окно вдобавок
оттянула бы сообщение, ради которого всё и затевалось.

Поэтому здесь нет ни строки очереди, ни накопителя: текст складывается и уходит в канал сразу.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import DatabaseError

from apps.catalog.models import GlobalSettings
from apps.notifications.services.message import compose_outage
from core.ports.notification_channel import NotificationChannel, Sent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AlertSent:
    channel_message_id: str


@dataclass(frozen=True, slots=True)
class AlertSkipped:
    reason: str


type AlertResult = AlertSent | AlertSkipped


def send_outage_alert(
    *, channel: NotificationChannel, component: str, detail: str | None = None
) -> AlertResult:
    """Сообщить дежурным, что проверки остановлены. Исключений не бросает."""
    if not channel.is_configured:
        return AlertSkipped(reason="канал не настроен")

    try:
        recipient = GlobalSettings.objects.get().telegram_chat_id
    except DatabaseError as error:
        # Отказал не Redis, а сама база: адресата прочитать неоткуда. Молчим, но заметно.
        logger.error("некому сообщить об аварии: база недоступна", extra={"error": str(error)})
        return AlertSkipped(reason="база недоступна")

    if not recipient:
        return AlertSkipped(reason="адресат не задан")

    result = channel.send(
        recipient=recipient, text=compose_outage(component=component, detail=detail)
    )
    if isinstance(result, Sent):
        return AlertSent(channel_message_id=result.channel_message_id)

    # Доставка не удалась — повторять здесь нечем и некогда: очередь недоступна по условию.
    logger.error("уведомление об аварии не доставлено", extra={"error": result.error})
    return AlertSkipped(reason=result.error)
