"""
Заглушка вебхука — ТЗ 8.3, plan.md этап 5.

Проверяется главное: заглушка говорит на том же протоколе, что и настоящие каналы, и отказывает
**результатом**, а не исключением, — иначе ненастроенный канал ронял бы процесс доставки.

Почта была здесь же, пока была заглушкой. Теперь она настоящая, и её проверки живут в
`test_smtp_channel.py`: у неё появился транспорт, которого у заглушки нет.
"""

from core.adapters.webhook import WebhookChannel
from core.ports.notification_channel import NotificationChannel, NotSent

RECIPIENT = "duty@contoso.ru"
URL = "https://hooks.contoso.ru/dns"


def test_8_3_stub_speaks_the_same_protocol() -> None:
    channel: NotificationChannel = WebhookChannel()

    result = channel.send(recipient=RECIPIENT, text="Адрес вне разрешённых")

    assert isinstance(result, NotSent)


def test_8_3_refusal_is_a_result_not_an_exception() -> None:
    # Отказ попадёт в журнал рядом с событием (8.3) и не уронит run_notifier.
    result = WebhookChannel().send(recipient=URL, text="что-нибудь")

    assert isinstance(result, NotSent)
    assert result.retryable is False
    assert "не реализован" in result.error


def test_8_3_webhook_needs_no_connection_settings() -> None:
    # У вебхука нет секрета: есть только адресат, и он задаётся в интерфейсе.
    assert WebhookChannel().is_configured is True
    assert isinstance(WebhookChannel().send(recipient="", text="текст"), NotSent)
