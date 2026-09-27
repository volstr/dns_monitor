"""
Заглушки каналов доставки — ТЗ 8.3, plan.md этап 5.

Проверяется главное: заглушки говорят на том же протоколе, что и Telegram, и отказывают
**результатом**, а не исключением, — иначе недоступный канал ронял бы процесс доставки.
"""

import pytest

from core.adapters.smtp import SmtpChannel
from core.adapters.webhook import WebhookChannel
from core.ports.notification_channel import NotificationChannel, NotSent

RECIPIENT = "duty@contoso.ru"
URL = "https://hooks.contoso.ru/dns"


def test_8_3_stubs_speak_the_same_protocol() -> None:
    channels: list[NotificationChannel] = [
        SmtpChannel(host="smtp.contoso.ru", sender="robot@contoso.ru"),
        WebhookChannel(),
    ]

    for channel in channels:
        result = channel.send(recipient=RECIPIENT, text="Адрес вне разрешённых")
        assert isinstance(result, NotSent)


def test_8_3_refusal_is_a_result_not_an_exception() -> None:
    # Отказ попадёт в журнал рядом с событием (8.3) и не уронит run_notifier.
    result = WebhookChannel().send(recipient=URL, text="что-нибудь")

    assert isinstance(result, NotSent)
    assert result.retryable is False
    assert "не реализован" in result.error


@pytest.mark.parametrize(
    ("host", "sender", "configured"),
    [
        ("smtp.contoso.ru", "robot@contoso.ru", True),
        ("smtp.contoso.ru", None, False),
        (None, "robot@contoso.ru", False),
        (None, None, False),
    ],
)
def test_11_10_mail_is_configured_only_with_host_and_sender(
    host: str | None, sender: str | None, *, configured: bool
) -> None:
    # Пустой SMTP_HOST — нормальный режим работы, а не ошибка конфигурации (критерий 75).
    assert SmtpChannel(host=host, sender=sender).is_configured is configured


def test_8_3_webhook_needs_no_connection_settings() -> None:
    # У вебхука нет секрета: есть только адресат, и он задаётся в интерфейсе.
    assert WebhookChannel().is_configured is True
    assert isinstance(WebhookChannel().send(recipient="", text="текст"), NotSent)
