"""
Кнопки «Отправить тестовое» и «Отключить» — макет, блок «Каналы доставки»; ТЗ 8.3.

Отправка идёт напрямую, минуя очередь: смысл кнопки — немедленный ответ. Поэтому проверяется и
то, что ответ канала доходит до человека дословно: общее «не отправлено» заставило бы разбираться
заново, а причина у Telegram названа одним предложением.
"""

import pytest

from apps.catalog.models import GlobalSettings
from apps.notifications.services.probe import (
    PROBE_TEXT,
    ProbeRefused,
    ProbeSent,
    disconnect_telegram,
    send_probe,
)
from core.adapters.fake_channel import FakeChannel
from core.ports.notification_channel import NotSent

pytestmark = pytest.mark.django_db

CHAT = "-1001234567890"


def test_8_3_probe_goes_to_the_saved_chat() -> None:
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)
    channel = FakeChannel()

    result = send_probe(channel=channel)

    assert isinstance(result, ProbeSent)
    assert result.recipient == CHAT
    assert result.unsaved is False
    assert channel.messages[0].recipient == CHAT
    assert channel.messages[0].text == PROBE_TEXT


def test_probe_checks_what_is_typed_not_what_is_saved() -> None:
    """Кнопка стоит рядом с полем: «проверить» означает «проверить вот это значение»."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="-100111")
    channel = FakeChannel()

    result = send_probe(channel=channel, typed_recipient="-100222")

    assert isinstance(result, ProbeSent)
    assert result.recipient == "-100222"
    # Проверили несохранённое — об этом нужно сказать, иначе человек уйдёт с экрана уверенным.
    assert result.unsaved is True


def test_probe_falls_back_to_the_saved_value() -> None:
    """Кнопку нажали, ничего не меняя: поле пустым не считается, берётся сохранённое."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)

    result = send_probe(channel=FakeChannel(), typed_recipient="   ")

    assert isinstance(result, ProbeSent)
    assert result.recipient == CHAT


def test_8_3_probe_without_a_token_names_the_variable() -> None:
    """Токен живёт только в окружении, и отказ обязан сказать, какой именно переменной нет."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)

    result = send_probe(channel=FakeChannel(configured=False))

    assert isinstance(result, ProbeRefused)
    assert "TELEGRAM_BOT_TOKEN" in result.reason
    assert result.from_channel is False


def test_probe_without_a_chat_says_there_is_nowhere_to_send() -> None:
    result = send_probe(channel=FakeChannel())

    assert isinstance(result, ProbeRefused)
    assert "некуда" in result.reason
    assert result.from_channel is False


def test_the_channel_error_reaches_the_person_verbatim() -> None:
    """Разбор настоящей неудачи занял вечер, а Telegram называл причину одним предложением."""
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)
    channel = FakeChannel(results=[NotSent(error="Bad Request: chat not found", retryable=False)])

    result = send_probe(channel=channel)

    assert isinstance(result, ProbeRefused)
    assert result.reason == "Bad Request: chat not found"
    assert result.from_channel is True


def test_8_3_disconnect_forgets_the_recipient_only() -> None:
    """
    «Отключить» стирает адресата. Токен не трогается и трогаться не может.

    По 8.3 токен — свойство установки, и интерфейс им не распоряжается. Поэтому «отключить»
    означает «забыть, куда писать», а не «отозвать доступ бота».
    """
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id=CHAT)

    disconnect_telegram()

    assert GlobalSettings.objects.get().telegram_chat_id == ""
