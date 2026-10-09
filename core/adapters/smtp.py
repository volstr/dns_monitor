"""
Канал доставки «Почта» — ТЗ 8.3, 8.4.

**Разграничение 8.3.** Адрес сервера, порт, учётные данные и адрес отправителя приходят из
окружения (11.10) и в интерфейсе не меняются: администратор приложения не должен получать
возможность направить исходящую почту через свой сервер (критерий 79). Адресат — перечень
почтовых адресов из общих настроек — приходит в `send` строкой.

`smtplib` вызывается не напрямую, а через `Transport`, как HTTP у Telegram: адаптер обязан
проверяться подставным транспортом, а `mock.patch` по путям импорта в проекте запрещён
(`CLAUDE.md`).

**Письмо составляется здесь, а текст — нет.** В `send` приходит готовая разметка из `message.py`
(8.4), та же, что уходит в Telegram: ограниченный HTML. Письмо получает её телом и вдобавок
текстовую часть — почтовые клиенты разные, и `multipart/alternative` для них штатный способ.

**Чего адаптер не решает.** Кто получает письма и при каких событиях — вопрос маршрутизации
(Q-D11), и он открыт: `dispatch` пока ставит в очередь только строки Telegram. Канал готов
раньше своего потребителя намеренно — ровно так 8.3 и описывает расширяемость.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

from core.ports.notification_channel import NotificationChannel, NotSent, SendResult, Sent

#: Режимы шифрования из `SMTP_TLS` (11.10).
TLS_STARTTLS = 'starttls'
TLS_IMPLICIT = 'tls'
TLS_NONE = 'none'

TIMEOUT_SECONDS = 10

#: Имя отправителя в заголовке From. Дежурный видит его в списке писем раньше темы.
SENDER_NAME = 'DNS monitoring'

#: Тема письма берётся из первой строки сообщения — это заголовок события по 8.4 («Адрес вне
#: разрешённых», «Домен не резолвится»). Отдельная тема означала бы второй источник названий
#: событий рядом с утверждённой таблицей Q-C11.
SUBJECT_FALLBACK = 'Уведомление DNS monitoring'

_TAG = re.compile(r'<[^>]+>')
_BREAK = re.compile(r'<br\s*/?>', re.IGNORECASE)


class Transport(Protocol):
    def send(self, message: EmailMessage) -> None:
        """Отправить письмо. Ошибка доставки выражается исключением, а не возвращаемым значением."""
        ...


class SmtplibTransport(Transport):
    """
    Боевой транспорт. Соединение открывается на каждое письмо и закрывается после.

    Долгоживущее соединение здесь было бы преждевременной оптимизацией: писем немного, а
    разорванный сервером сокет пришлось бы переоткрывать самим — с той же ценой, но ещё и с
    состоянием, которое переживает отправку.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        tls: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._tls = tls

    def send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self._tls == TLS_IMPLICIT:
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=TIMEOUT_SECONDS, context=context
            ) as server:
                self._deliver(server, message)
            return

        with smtplib.SMTP(self._host, self._port, timeout=TIMEOUT_SECONDS) as server:
            if self._tls == TLS_STARTTLS:
                server.starttls(context=context)
            self._deliver(server, message)

    def _deliver(self, server: smtplib.SMTP, message: EmailMessage) -> None:
        if self._user and self._password:
            server.login(self._user, self._password)
        server.send_message(message)


class SmtpChannel(NotificationChannel):
    def __init__(
        self,
        *,
        host: str | None,
        sender: str | None = None,
        port: int = 587,
        user: str | None = None,
        password: str | None = None,
        tls: str = TLS_STARTTLS,
        transport: Transport | None = None,
    ) -> None:
        self._host = host
        self._sender = sender
        self._transport = transport
        self._settings = (port, user, password, tls)

    @property
    def is_configured(self) -> bool:
        """
        «Настроен» — это заданные адрес сервера и отправитель (11.10 проверяет их связку).

        Пустой `SMTP_HOST` — нормальный режим работы, а не ошибка конфигурации: без него
        приглашение письмом просто недоступно (критерий 75), а приложение стартует (критерий 78).
        """
        return bool(self._host and self._sender)

    def send(self, *, recipient: str, text: str, reply_to: str | None = None) -> SendResult:
        if not self.is_configured:
            return NotSent(error='SMTP_HOST или SMTP_FROM не задан', retryable=False)

        recipients = _recipients(recipient)
        if not recipients:
            return NotSent(error='не задан ни один адрес получателя', retryable=False)

        message = _build(
            sender=str(self._sender), recipients=recipients, text=text, reply_to=reply_to
        )
        try:
            self._connection().send(message)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as error:
            # Пароль не подойдёт со второй попытки, и несуществующий адрес не появится:
            # повторять бессмысленно, пять повторов лишь оттянут запись в журнал.
            return NotSent(error=_reason(error), retryable=False)
        except (OSError, smtplib.SMTPException) as error:
            # Сервер недоступен, разорвал соединение, ответил «попробуйте позже» — это как раз
            # то, ради чего существуют повторы (критерий 78).
            return NotSent(error=_reason(error), retryable=True)

        return Sent(channel_message_id=str(message['Message-ID']))

    def _connection(self) -> Transport:
        if self._transport is not None:
            return self._transport
        port, user, password, tls = self._settings
        return SmtplibTransport(
            host=str(self._host), port=port, user=user, password=password, tls=tls
        )


def _recipients(recipient: str) -> list[str]:
    """Адресат почты — перечень адресов по строке в строке (8.3, Q-D11)."""
    return [line.strip() for line in recipient.splitlines() if line.strip()]


def _build(*, sender: str, recipients: list[str], text: str, reply_to: str | None) -> EmailMessage:
    message = EmailMessage()
    message['From'] = formataddr((SENDER_NAME, sender))
    message['To'] = ', '.join(recipients)
    message['Subject'] = _subject(text)
    message['Message-ID'] = make_msgid()
    if reply_to:
        # 7.3: возврат в норму приходит ответом на исходное сообщение. У почты ветку задают
        # два заголовка сразу: In-Reply-To понимают все клиенты, References — те, что строят
        # дерево переписки.
        message['In-Reply-To'] = reply_to
        message['References'] = reply_to

    message.set_content(_as_plain(text))
    message.add_alternative(text, subtype='html')
    return message


def _subject(text: str) -> str:
    """Первая строка сообщения — заголовок события по 8.4."""
    for line in _as_plain(text).splitlines():
        if line.strip():
            return line.strip()
    return SUBJECT_FALLBACK


def _as_plain(text: str) -> str:
    """
    Текстовая часть письма из той же разметки.

    Это не второй составитель сообщения: строки, порядок и содержание берутся из `message.py`
    целиком, здесь снимается только разметка. Иначе появился бы второй источник текста, и они
    разошлись бы при первой же правке 8.4.
    """
    return _TAG.sub('', _BREAK.sub('\n', text)).replace('&lt;', '<').replace('&gt;', '>').replace(
        '&amp;', '&'
    )


def _reason(error: BaseException) -> str:
    """Причина словами: она попадёт в журнал доставки и на экран (8.3)."""
    text = str(error).strip()
    return text or error.__class__.__name__
