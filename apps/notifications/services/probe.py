"""
Проверка канала доставки кнопкой «Отправить тестовое» — макет, блок «Каналы доставки».

**Отправляется напрямую, минуя очередь и окно слияния.** Причина та же, что у уведомления об
аварии, только с другой стороны: смысл кнопки — немедленный ответ на вопрос «дойдёт ли». Строка
в очереди ждала бы окно слияния и следующий круг доставки, а человек за это время успел бы
решить, что ничего не работает.

По той же причине результат возвращается с **текстом ошибки от канала**, а не общим «не
отправлено». Разбор одной настоящей неудачи занял целый вечер, а ответ Telegram называл причину
одним предложением: `Bad Request: chat not found`. Этого предложения не хватало на экране.

Название модуля не `test_message`: файл с таким именем `pytest` собрал бы как набор тестов.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.catalog.models import GlobalSettings
from core.ports.notification_channel import NotificationChannel, Sent


@dataclass(frozen=True, slots=True)
class ProbeSent:
    recipient: str
    #: Проверяли значение из поля, а сохранено другое. Напомнить о сохранении обязательно: иначе
    #: человек уйдёт с экрана, уверенный, что настроил канал.
    unsaved: bool


@dataclass(frozen=True, slots=True)
class ProbeRefused:
    """
    Отправить не удалось. ``reason`` показывается человеку как есть.

    ``from_channel`` отличает отказ канала от нашей собственной непонятости: в первом случае
    текст пришёл от Telegram и его стоит читать буквально, во втором — это мы не дали каналу
    даже попробовать.
    """

    reason: str
    from_channel: bool


type ProbeResult = ProbeSent | ProbeRefused

#: Текст тестового сообщения. Нарочно говорит, откуда он и что означает: сообщение приходит
#: дежурному, который мог не знать, что кто-то нажал кнопку.
PROBE_TEXT = (
    "<b>Проверка канала</b>\n"
    "Это тестовое сообщение из DNS monitoring. Настройка доставки работает: "
    "уведомления о расхождениях будут приходить сюда."
)


def send_probe(*, channel: NotificationChannel, typed_recipient: str = "") -> ProbeResult:
    """
    Отправить тестовое сообщение. Исключений не бросает.

    Проверяется то, что человек видит на экране: кнопка стоит рядом с полем, и «проверить»
    естественно означает «проверить вот это значение», а не сохранённое полчаса назад. Пустое
    поле — берём сохранённое: так кнопка работает и когда её нажали, ничего не меняя.
    """
    if not channel.is_configured:
        return ProbeRefused(
            reason=(
                "Канал не настроен: не задана переменная окружения TELEGRAM_BOT_TOKEN. "
                "Токен задаётся только в окружении (ТЗ 8.3)."
            ),
            from_channel=False,
        )

    saved = GlobalSettings.objects.get().telegram_chat_id
    recipient = typed_recipient.strip() or saved
    if not recipient:
        return ProbeRefused(
            reason="Не задан идентификатор чата: отправлять некуда.",
            from_channel=False,
        )

    result = channel.send(recipient=recipient, text=PROBE_TEXT)
    if isinstance(result, Sent):
        return ProbeSent(recipient=recipient, unsaved=recipient != saved)
    return ProbeRefused(reason=result.error, from_channel=True)


@dataclass(frozen=True, slots=True)
class ChannelDisconnected:
    pass


def disconnect_telegram() -> ChannelDisconnected:
    """
    Кнопка «Отключить» — макет, блок «Каналы доставки».

    Стирается **только адресат**. Токен из окружения не трогается и трогаться не может: по 8.3 он
    свойство установки, а не продукта, и интерфейс им не распоряжается. Поэтому «отключить» здесь
    означает «забыть, куда писать», и сказать об этом человеку обязательно — иначе он решит, что
    отозвал доступ, а доступ остался.

    Событий и записей в журнал не порождается: 3.1 перечисляет действия с участниками, а это
    настройка канала. Факт правки виден по самому экрану.
    """
    GlobalSettings.objects.filter(pk=1).update(telegram_chat_id="")
    return ChannelDisconnected()
