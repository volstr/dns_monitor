"""Порт канала доставки и Telegram — ТЗ 8.3, 8.4, Q-A2."""

from typing import Any

import pytest

from core.adapters.fake_channel import FakeChannel
from core.adapters.telegram import TelegramChannel, Transport, escape
from core.ports.notification_channel import NotSent, Sent

CHAT = "-1001234567890"
TOKEN = "123456789:AA-fake-token-for-tests"


class StubTransport(Transport):
    """Подставной транспорт: зависимость передана, поэтому patch не нужен."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, payload))
        return self.status, self.body


class BrokenTransport(Transport):
    def post(self, url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        message = "соединение отказано"
        raise OSError(message)


def ok_body(message_id: int = 42) -> dict[str, Any]:
    return {"ok": True, "result": {"message_id": message_id}}


def test_8_3_fake_channel_records_what_was_sent() -> None:
    channel = FakeChannel()

    result = channel.send(recipient=CHAT, text="Адрес вне разрешённых")

    assert isinstance(result, Sent)
    assert channel.messages[0].recipient == CHAT
    assert channel.messages[0].text == "Адрес вне разрешённых"


def test_8_3_fake_channel_can_fail_on_schedule() -> None:
    # «Первая попытка не прошла, вторая прошла» — это список исходов, а не подмена импорта.
    channel = FakeChannel()
    channel.fail_next("429 Too Many Requests")

    first = channel.send(recipient=CHAT, text="раз")
    second = channel.send(recipient=CHAT, text="два")

    assert isinstance(first, NotSent)
    assert isinstance(second, Sent)


def test_8_3_telegram_sends_and_returns_the_message_id() -> None:
    transport = StubTransport(200, ok_body(777))
    channel = TelegramChannel(TOKEN, transport)

    result = channel.send(recipient=CHAT, text="Адрес вне разрешённых")

    assert result == Sent(channel_message_id="777")
    url, payload = transport.calls[0]
    assert url.endswith("/sendMessage")
    assert TOKEN in url
    assert payload["chat_id"] == CHAT
    assert payload["parse_mode"] == "HTML"


def test_7_3_reply_goes_into_the_thread() -> None:
    transport = StubTransport(200, ok_body())
    channel = TelegramChannel(TOKEN, transport)

    channel.send(recipient=CHAT, text="Снова в норме", reply_to="42")

    assert transport.calls[0][1]["reply_to_message_id"] == "42"


def test_7_3_message_without_a_thread_carries_no_reply_field() -> None:
    transport = StubTransport(200, ok_body())
    channel = TelegramChannel(TOKEN, transport)

    channel.send(recipient=CHAT, text="Адрес вне разрешённых")

    assert "reply_to_message_id" not in transport.calls[0][1]


def test_8_3_empty_token_means_not_configured() -> None:
    # Пустое значение — нормальный режим работы, а не ошибка конфигурации (11.10).
    channel = TelegramChannel(None)

    assert channel.is_configured is False
    result = channel.send(recipient=CHAT, text="что-нибудь")
    assert isinstance(result, NotSent)
    assert result.retryable is False


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (500, True), (400, False), (403, False)]
)
def test_8_3_permanent_failures_are_not_retried(status: int, retryable: bool) -> None:
    transport = StubTransport(status, {"ok": False, "description": "chat not found"})
    channel = TelegramChannel(TOKEN, transport)

    result = channel.send(recipient=CHAT, text="текст")

    assert isinstance(result, NotSent)
    assert result.retryable is retryable
    assert result.error == "chat not found"


def test_8_3_network_failure_is_retryable() -> None:
    channel = TelegramChannel(TOKEN, BrokenTransport())

    result = channel.send(recipient=CHAT, text="текст")

    assert isinstance(result, NotSent)
    assert result.retryable is True


def test_8_4_special_characters_are_escaped() -> None:
    expected = "&lt;b&gt;10.20.1.14&lt;/b&gt; &amp; 10.20.2.60"

    assert escape("<b>10.20.1.14</b> & 10.20.2.60") == expected
