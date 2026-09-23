"""Очередь исходящих уведомлений — ТЗ 8.3, plan.md этап 5.

Очередь живёт **таблицей в PostgreSQL**, а не в Redis: по 11.11 Redis не источник истины, а по 8.3
результат доставки обязан попадать в журнал и быть виден рядом с событием. Экспоненциальная
задержка здесь — арифметика над `next_attempt_at`, а конечное число попыток — условие в запросе,
а не механизм брокера (Q-D24).

Одно уведомление может покрывать много событий: при слиянии (8.6) двенадцать событий уходят одним
сводным сообщением. Поэтому связь ведёт **событие → уведомление**, как и записано в 4.5.
"""

from django.db import models

ENUM_MAX_LENGTH = 32
RECIPIENT_MAX_LENGTH = 320
CHANNEL_MESSAGE_ID_MAX_LENGTH = 64
ERROR_MAX_LENGTH = 500
#: Предел сообщения Telegram (8.4). Текст складывается при постановке в очередь, чтобы журнал
#: показывал именно то, что ушло, а повтор отправлял то же самое.
TEXT_MAX_LENGTH = 4096


class ChannelKind(models.TextChoices):
    """Виды каналов доставки — ТЗ 8.3. В первой версии работает только Telegram."""

    TELEGRAM = "telegram", "Telegram"
    EMAIL = "email", "Почта"
    WEBHOOK = "webhook", "Вебхук"


class DeliveryStatus(models.TextChoices):
    """Результат доставки — виден рядом с событием (8.3) и уходит в выгрузку журнала (11.7)."""

    PENDING = "pending", "Ожидает отправки"
    SENT = "sent", "Доставлено"
    #: Повторы исчерпаны. Дальше не берётся — конечное число попыток из 8.3.
    FAILED = "failed", "Не доставлено"


class Notification(models.Model):
    """Строка очереди исходящих.

    Поля повторов названы в plan.md дословно: попытка, момент следующей попытки, последняя ошибка.
    """

    channel = models.CharField(max_length=ENUM_MAX_LENGTH, choices=ChannelKind.choices)
    #: Кому отправлено — адресат по 8.3. Хранится снимком: настройка могла измениться с тех пор.
    recipient = models.CharField(max_length=RECIPIENT_MAX_LENGTH)
    text = models.CharField(max_length=TEXT_MAX_LENGTH)
    status = models.CharField(
        max_length=ENUM_MAX_LENGTH, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )

    created_at = models.DateTimeField()
    attempt = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error = models.CharField(max_length=ERROR_MAX_LENGTH, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True, default=None)

    #: Идентификатор сообщения в канале: по нему отвечают в ветку при закрытии инцидента и при
    #: напоминании (7.3, 8.5).
    channel_message_id = models.CharField(
        max_length=CHANNEL_MESSAGE_ID_MAX_LENGTH, blank=True, default=""
    )
    #: Ответ в ветку. Пусто — сообщение самостоятельное. Правило Q-D7: продолжение существует
    #: только при наличии открывающего сообщения.
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )

    class Meta:
        indexes = [
            # Выборка очереди: что пора отправлять (8.3).
            models.Index(fields=["status", "next_attempt_at"], name="notification_due"),
        ]

    def __str__(self) -> str:
        return f"{self.channel} → {self.recipient} ({self.status})"
