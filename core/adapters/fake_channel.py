"""Тестовый канал доставки: записывает отправленное и умеет отказывать по сценарию.

Нужен критериям 14–20 и 38–43: там проверяется, **что** и **когда** уходит, а не как устроен
transport. Падение канала задаётся списком исходов, поэтому «первая попытка не прошла, вторая
прошла» — это два элемента списка, а не подмена по путям импорта.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.ports.notification_channel import NotificationChannel, NotSent, SendResult, Sent


@dataclass(frozen=True, slots=True)
class SentMessage:
    recipient: str
    text: str
    reply_to: str | None


class FakeChannel(NotificationChannel):
    def __init__(
        self, *, results: Sequence[SendResult] | None = None, configured: bool = True
    ) -> None:
        #: Что ушло — в порядке отправки.
        self.messages: list[SentMessage] = []
        self._results = list(results or [])
        self._configured = configured
        self._next_id = 1

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send(self, *, recipient: str, text: str, reply_to: str | None = None) -> SendResult:
        self.messages.append(SentMessage(recipient=recipient, text=text, reply_to=reply_to))
        if self._results:
            return self._results.pop(0)
        message_id = str(self._next_id)
        self._next_id += 1
        return Sent(channel_message_id=message_id)

    def fail_next(self, error: str = "канал недоступен", *, retryable: bool = True) -> None:
        """Назначить отказ на следующую отправку."""
        self._results.append(NotSent(error=error, retryable=retryable))
