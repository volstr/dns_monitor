"""
Канал доставки «Почта» — ТЗ 8.3, 8.4; критерии 75, 78, 79.

`smtplib` подменяется подставным транспортом, а не `mock.patch` по путям импорта: понадобился
патч — значит зависимость не вынесена в порт (`CLAUDE.md`). Один тест ходит к настоящему
закрытому порту — он и есть критерий 78.

Разграничение 8.3 проверяется с двух сторон: адресат приходит в `send` строкой, а подключение
(сервер, порт, учётные данные) в канал — из окружения, и в интерфейс не попадает вовсе.
"""

import smtplib
from email.message import EmailMessage

import pytest

from core.adapters.smtp import SmtpChannel, Transport
from core.ports.notification_channel import NotSent, Sent

SENDER = "robot@contoso.ru"
HOST = "smtp.contoso.ru"
RECIPIENT = "duty@contoso.ru"
MESSAGE = (
    "<b>Адрес вне разрешённых</b>\n"
    "<code>pay-gw.contoso.ru</code>\n"
    'Адрес: <code>185.44.9.201</code>\n<a href="https://dns.example/domains/1/">Открыть домен</a>'
)


class Recorder(Transport):
    """Запоминает письмо вместо отправки."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(message)


def channel(transport: Transport, *, host: str | None = HOST) -> SmtpChannel:
    return SmtpChannel(host=host, sender=SENDER, transport=transport)


@pytest.mark.parametrize(
    ("host", "sender", "configured"),
    [
        (HOST, SENDER, True),
        (HOST, None, False),
        (None, SENDER, False),
        (None, None, False),
    ],
)
def test_11_10_mail_is_configured_only_with_host_and_sender(
    host: str | None, sender: str | None, *, configured: bool
) -> None:
    # Пустой SMTP_HOST — нормальный режим работы, а не ошибка конфигурации (критерий 75).
    assert SmtpChannel(host=host, sender=sender).is_configured is configured


def test_8_3_message_goes_to_every_listed_address() -> None:
    """Адресат почты — перечень адресов по строке в строке (8.3, Q-D11)."""
    recorder = Recorder()

    result = channel(recorder).send(
        recipient="duty@contoso.ru\nnoc@contoso.ru\n", text=MESSAGE
    )

    assert isinstance(result, Sent)
    assert recorder.messages[0]["To"] == "duty@contoso.ru, noc@contoso.ru"


def test_8_4_subject_is_the_event_heading() -> None:
    """
    Тема — первая строка сообщения, то есть заголовок события из утверждённой таблицы Q-C11.

    Своя тема означала бы второй источник названий событий рядом с таблицей, и они разошлись
    бы при первой же правке.
    """
    recorder = Recorder()

    channel(recorder).send(recipient=RECIPIENT, text=MESSAGE)

    assert recorder.messages[0]["Subject"] == "Адрес вне разрешённых"


def test_8_4_letter_carries_both_html_and_plain_text() -> None:
    """
    Почтовые клиенты разные, `multipart/alternative` для них штатный способ.

    Текстовая часть — та же разметка без тегов, а не второй составитель сообщения: строки и
    порядок берутся из `message.py` целиком.
    """
    recorder = Recorder()

    channel(recorder).send(recipient=RECIPIENT, text=MESSAGE)
    letter = recorder.messages[0]

    assert letter.get_content_type() == "multipart/alternative"
    plain = letter.get_body(preferencelist=("plain",))
    html = letter.get_body(preferencelist=("html",))
    assert plain is not None
    assert html is not None
    assert "Адрес вне разрешённых" in plain.get_content()
    assert "<b>" not in plain.get_content()
    assert "Открыть домен" in html.get_content()


def test_7_3_reply_goes_into_the_original_thread() -> None:
    """7.3: возврат в норму приходит ответом на исходное сообщение."""
    recorder = Recorder()

    channel(recorder).send(recipient=RECIPIENT, text=MESSAGE, reply_to="<opening@contoso.ru>")
    letter = recorder.messages[0]

    # Два заголовка сразу: In-Reply-To понимают все клиенты, References — строящие дерево.
    assert letter["In-Reply-To"] == "<opening@contoso.ru>"
    assert letter["References"] == "<opening@contoso.ru>"


def test_7_3_message_id_comes_back_for_threading() -> None:
    """Без идентификатора отправленного письма отвечать в ветку будет не на что."""
    recorder = Recorder()

    result = channel(recorder).send(recipient=RECIPIENT, text=MESSAGE)

    assert isinstance(result, Sent)
    assert result.channel_message_id == recorder.messages[0]["Message-ID"]


def test_8_3_unconfigured_channel_refuses_without_trying() -> None:
    recorder = Recorder()

    result = channel(recorder, host=None).send(recipient=RECIPIENT, text=MESSAGE)

    assert isinstance(result, NotSent)
    assert result.retryable is False
    assert recorder.messages == []


def test_8_3_empty_recipient_list_is_a_refusal_not_a_send() -> None:
    recorder = Recorder()

    result = channel(recorder).send(recipient="   \n  ", text=MESSAGE)

    assert isinstance(result, NotSent)
    assert result.retryable is False


def test_8_3_a_wrong_password_is_not_worth_retrying() -> None:
    """Пароль не подойдёт со второй попытки: пять повторов лишь оттянут запись в журнал."""
    recorder = Recorder(smtplib.SMTPAuthenticationError(535, b"authentication failed"))

    result = channel(recorder).send(recipient=RECIPIENT, text=MESSAGE)

    assert isinstance(result, NotSent)
    assert result.retryable is False


def test_78_an_unreachable_server_is_a_retryable_failure_not_a_crash() -> None:
    """
    Критерий 78, первая половина: недоступный сервер — штатный отказ, а не падение процесса.

    Порт настоящий и заведомо закрытый: подделка здесь ничего не доказала бы — проверяется
    именно то, что исключение `smtplib` не вылетает наружу и превращается в результат.
    Повторять имеет смысл, ради этого повторы и существуют; в Sentry уходит уже исчерпание
    повторов, и это проверяется на стороне доставки.
    """
    unreachable = SmtpChannel(host="127.0.0.1", sender=SENDER, port=1, tls="none")

    result = unreachable.send(recipient=RECIPIENT, text=MESSAGE)

    assert isinstance(result, NotSent)
    assert result.retryable is True
    assert result.error
