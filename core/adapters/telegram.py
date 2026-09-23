"""Канал доставки Telegram — ТЗ 8.3, 8.4.

Единственный обязательный канал первой версии. Токен приходит из окружения (Q-A2): по 8.3 это
**подключение**, а не адресат, и из интерфейса не меняется.

HTTP берётся из стандартной библиотеки: добавлять `requests` или `httpx` значит трогать таблицу
стека ради одного POST. Сам вызов вынесен в `Transport`, поэтому адаптер проверяется подставным
транспортом, а не `mock.patch` по путям импорта (`CLAUDE.md`).

Разметка — ограниченный HTML (8.4 разрешает HTML или MarkdownV2). HTML выбран потому, что
экранировать нужно три символа вместо восемнадцати, и ошибка экранирования не превращает
сообщение в кашу из служебных знаков.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from core.ports.notification_channel import NotificationChannel, NotSent, SendResult, Sent

API_URL = "https://api.telegram.org"
TIMEOUT_SECONDS = 10
#: Коды, при которых повторять бессмысленно: чат не найден, бот заблокирован, текст неверен.
PERMANENT_CODES = frozenset({400, 403, 404})


class Transport(Protocol):
    def post(self, url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Отправить JSON и вернуть код ответа с разобранным телом."""
        ...


class UrllibTransport(Transport):
    def post(self, url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(  # noqa: S310 — схема фиксирована константой API_URL
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                return int(response.status), json.loads(response.read())
        except urllib.error.HTTPError as error:
            # Telegram отвечает телом с описанием и на ошибках — оно информативнее кода.
            try:
                body = json.loads(error.read())
            except ValueError:
                body = {"description": error.reason}
            return int(error.code), body


def escape(text: str) -> str:
    """Экранирование для ограниченного HTML (8.4)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramChannel(NotificationChannel):
    def __init__(self, token: str | None, transport: Transport | None = None) -> None:
        self._token = token
        self._transport = transport or UrllibTransport()

    @property
    def is_configured(self) -> bool:
        """Пустой токен — нормальный режим: канал показывается как «Не настроен» (8.3)."""
        return bool(self._token)

    def send(self, *, recipient: str, text: str, reply_to: str | None = None) -> SendResult:
        if not self._token:
            return NotSent(error="TELEGRAM_BOT_TOKEN не задан", retryable=False)

        payload: dict[str, Any] = {
            "chat_id": recipient,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to

        try:
            status, body = self._transport.post(
                f"{API_URL}/bot{self._token}/sendMessage", payload
            )
        except OSError as error:
            # Сеть недоступна — повторять имеет смысл.
            return NotSent(error=f"сеть недоступна: {error}", retryable=True)

        if status == 200 and body.get("ok"):
            message_id = body.get("result", {}).get("message_id")
            return Sent(channel_message_id=str(message_id))

        description = str(body.get("description") or f"HTTP {status}")
        return NotSent(error=description, retryable=status not in PERMANENT_CODES)
