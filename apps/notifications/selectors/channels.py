"""
Какие каналы доставки готовы принять событие — ТЗ 8.3, решение Q-D11 (вариант 1).

Событие уходит **во все настроенные каналы**. Вопрос «какие настроены» звучит просто, но у него
два ответа, и брать нужно оба сразу:

- **адресат задан** — это общие настройки: чат Telegram, перечень почтовых адресов, адрес
  вебхука. Пустое значение означает «Не настроен» и является нормальным режимом работы (8.3);
- **реализация готова** — свойство кода, а не настроек. У заглушки вебхука нет подключения, и
  поэтому её `is_configured` всегда `True`: спрашивать её бесполезно.

Если развозить события «по всем каналам, у которых есть адресат», заглушка вебхука на каждое
событие даст в журнале запись «не доставлено» — ровно тот подводный камень, который Q-D11
называет прямо. Поэтому условие — связка, и перечень готовых реализаций записан здесь явным
списком, а не выведен из чего-нибудь.

Здесь только **решение**, без самих адаптеров: строит их композиционный корень (`runner.py`),
и знать про `smtplib` или HTTP этому модулю незачем.
"""

from __future__ import annotations

from apps.catalog.models import GlobalSettings
from apps.notifications.models import ChannelKind

#: Каналы, у которых есть настоящая реализация. Вебхука здесь нет: `core/adapters/webhook.py` —
#: заглушка по 8.3, и включать её в маршрутизацию значит обещать доставку, которой не будет.
IMPLEMENTED = frozenset({ChannelKind.TELEGRAM, ChannelKind.EMAIL})

#: Где лежит адресат каждого канала — поля общих настроек (Q-D11: адресат один на вид канала).
RECIPIENT_FIELD = {
    ChannelKind.TELEGRAM: 'telegram_chat_id',
    ChannelKind.EMAIL: 'email_recipients',
    ChannelKind.WEBHOOK: 'webhook_url',
}


def ready_channels(global_settings: GlobalSettings) -> dict[ChannelKind, str]:
    """
    Готовые каналы и их адресаты, в порядке перечисления `ChannelKind`.

    Порядок фиксирован не для красоты: он задаёт порядок строк очереди у одного события, а по
    нему потом читается журнал. Случайный порядок означал бы, что две одинаковые установки
    показывают доставку по-разному.
    """
    ready: dict[ChannelKind, str] = {}
    for kind in ChannelKind:
        if kind not in IMPLEMENTED:
            continue
        recipient = _recipient(global_settings, kind)
        if recipient:
            ready[kind] = recipient
    return ready


def _recipient(global_settings: GlobalSettings, kind: ChannelKind) -> str:
    value = getattr(global_settings, RECIPIENT_FIELD[kind])
    if isinstance(value, list):
        # Перечень почтовых адресов хранится списком, а каналу уходит строкой: по адресу в
        # строке — так же, как его вводят на экране (8.3).
        return '\n'.join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()
