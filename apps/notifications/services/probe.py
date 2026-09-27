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

from dataclasses import dataclass, replace

from django.conf import settings

from apps.catalog.models import GlobalSettings
from apps.monitoring.models import EventType
from apps.notifications.services.message import MessageInput, compose
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

#: Заголовок тестового сообщения. Нужен обязательно: сообщение приходит дежурному, который не
#: знает, что кто-то нажал кнопку, и без пометки примет пример за настоящую тревогу.
PROBE_HEADER = (
    "<b>Проверка канала</b>\n"
    "Это тестовое сообщение из DNS monitoring: доставка работает. Ниже — пример того, как будет "
    "выглядеть настоящее уведомление.\n"
)

#: Пример для тестового сообщения — состав строк из 8.4, с вымышленным доменом.
#:
#: Собирается **тем же составителем**, что настоящие уведомления, и это главное в кнопке. Иначе
#: она проверяла бы только доступность чата, а не то, ради чего её нажимают: дойдёт ли разметка,
#: станет ли ссылка ссылкой, не режется ли список адресов. Разработчик заметил ровно это —
#: тестовое сообщение не походило на макет, потому что ссылки в нём не было вовсе (Q-D41).
PROBE_EXAMPLE = MessageInput(
    event_type=EventType.NOT_ALLOWED,
    domain_name="pay-gw.example",
    domain_url="",
    changed=("185.44.9.201",),
    current=("10.20.1.14", "10.20.2.61", "185.44.9.201"),
    group_name="Платёжный контур",
    domain_comment="Пример комментария домена: он попадает в сообщение по 8.4.",
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

    settings_row = GlobalSettings.objects.get()
    saved = settings_row.telegram_chat_id
    recipient = typed_recipient.strip() or saved
    if not recipient:
        return ProbeRefused(
            reason="Не задан идентификатор чата: отправлять некуда.",
            from_channel=False,
        )

    result = channel.send(recipient=recipient, text=_probe_text(settings_row))
    if isinstance(result, Sent):
        return ProbeSent(recipient=recipient, unsaved=recipient != saved)
    return ProbeRefused(reason=result.error, from_channel=True)


def _probe_text(settings_row: GlobalSettings) -> str:
    """
    Пример собирается с теми же переключателями, что настоящее уведомление (8.4, Q-C4).

    Если администратор отключил ссылку или комментарий, тестовое сообщение обязано прийти без
    них: иначе кнопка покажет не то, что придёт дежурному, и проверять её будет бессмысленно.

    Ссылка ведёт на список доменов, а не на вымышленный домен: неработающая ссылка в проверке
    связи — худшее, что можно прислать. Зато по ней сразу видно, открывается ли `APP_BASE_URL`
    из того места, где сидит дежурный.
    """
    example = replace(PROBE_EXAMPLE, domain_url=f"{settings.APP_BASE_URL}/domains/")
    body = compose(
        example,
        include_current=settings_row.message_include_full_set,
        include_comment=settings_row.message_include_comment,
        include_link=settings_row.message_include_link,
    )
    return f"{PROBE_HEADER}\n{body}"


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
