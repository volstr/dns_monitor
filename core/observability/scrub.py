"""Что вырезается из события перед отправкой — ТЗ 11.14; критерий 74.

Функция чистая: на входе словарь события, на выходе словарь события. Отсюда два следствия,
ради которых так и сделано. Её можно проверить тестом без сети и без клиента Sentry — а
проверять обязательно, потому что цена ошибки здесь несимметрична: не отправленная ошибка
стоит одного неудобства, отправленный токен бота стоит смены токена.

**Список вырезаемого закрытый, а правило — по имени ключа.** Перечислять места, где секрет
может оказаться, бессмысленно: он приходит то в `extra`, то в заголовке, то в строке
подключения внутри сообщения об ошибке БД. Поэтому режется любой ключ, чьё имя похоже на
секрет, на любой глубине.

Обратное правило тоже из 11.14 и так же важно: **белые списки, имена доменов и IP-адреса не
вырезаются**. Без них ошибка нечитаема, а секретом они не являются.
"""

from __future__ import annotations

import re
from typing import Any

#: Вместо значения. По-русски и однозначно: пустая строка неотличима от настоящей пустоты.
SCRUBBED = "[вырезано]"

#: Пароль внутри строки подключения: `postgres://user:пароль@host/db`, `redis://:пароль@host`.
#: Правило по имени ключа такое не ловит — строка подключения приходит внутри **текста**
#: исключения драйвера, где имени у неё нет.
_URL_PASSWORD: re.Pattern[str] = re.compile(r"(?P<head>[a-z][a-z0-9+.-]*://[^\s/@:]*:)[^\s/@]+@")

#: Токен бота Telegram. Тот же случай: он оказывается в тексте ошибки HTTP-запроса, потому что
#: стоит прямо в адресе api.telegram.org/bot{token}/….
_BOT_TOKEN: re.Pattern[str] = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")

#: Ключ, в имени которого встретилось любое из этих слов, не отправляется. 11.14 называет
#: токен бота, `SMTP_PASSWORD`, `SESSION_SECRET`, строки подключения к БД и Redis, хеши
#: паролей, токены приглашений и значения сессионных cookie.
SECRET_MARKERS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "secret",
    "session",
    "cookie",
    "authorization",
    "auth",
    "credential",
    "api_key",
    "apikey",
    "dsn",
    "database_url",
    "redis_url",
    "_url",
    "hash",
    "salt",
)

#: Адрес участника не отправляется: «участник обозначается идентификатором, а не почтовым
#: адресом» (11.14). Имя тоже: по нему человек опознаётся не хуже.
USER_KEPT: tuple[str, ...] = ("id",)

#: Тело запроса к этим обработчикам не отправляется целиком: там пароль в открытом виде.
#: Сверяется по началу пути, чтобы не зависеть от завершающей косой черты.
BODY_FREE_PATHS: tuple[str, ...] = ("/login", "/password", "/invite", "/2fa")

#: Глубже событие Sentry не вкладывается. Предел нужен, чтобы циклическая структура в `extra`
#: не увела обход в бесконечность: событие приходит из чужого кода, доверять его форме нельзя.
MAX_DEPTH = 12


def is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def redact(text: str) -> str:
    """Вырезать секреты, которые пришли внутри текста, а не отдельным полем.

    Имя домена и адрес при этом остаются: режется ровно пароль в строке подключения и токен
    бота, потому что без остального текст ошибки перестаёт что-либо значить (11.14).
    """
    without_password = _URL_PASSWORD.sub(rf"\g<head>{SCRUBBED}@", text)
    return _BOT_TOKEN.sub(SCRUBBED, without_password)


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """Вычистить событие целиком — ТЗ 11.14, критерий 74."""
    cleaned = _clean(event, depth=0)
    if not isinstance(cleaned, dict):  # pragma: no cover — событие всегда словарь
        return event

    user = cleaned.get("user")
    if isinstance(user, dict):
        cleaned["user"] = {key: user[key] for key in USER_KEPT if key in user}

    request = cleaned.get("request")
    if isinstance(request, dict) and _body_is_credentials(request):
        # «Тела запросов к обработчикам входа и смены пароля **целиком**» (11.14). Именно
        # целиком: рядом с паролем в том же теле лежит логин, а логин участника — это его
        # почтовый адрес, который 11.14 отправлять запрещает отдельным пунктом.
        request.pop("data", None)

    return cleaned


def _body_is_credentials(request: dict[str, Any]) -> bool:
    """Тело запроса с учётными данными — по обработчику или по самому телу.

    Проверять только адрес обработчика оказалось недостаточно: в событие от Django поле `url`
    попадает не всегда, и тогда правило по адресу молча не срабатывало — тело запроса к входу
    уходило целиком, вместе с почтовым адресом участника. Поэтому второй признак смотрит на
    само тело: поле с именем секрета означает форму учётных данных независимо от того, по
    какому адресу её отправили.
    """
    if any(part in str(request.get("url", "")) for part in BODY_FREE_PATHS):
        return True
    data = request.get("data")
    return isinstance(data, dict) and any(is_secret(str(key)) for key in data)


def _clean(value: Any, *, depth: int) -> Any:
    """Обход события. Значение ключа-секрета заменяется, остальное проходит как есть."""
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            key: SCRUBBED if is_secret(str(key)) else _clean(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_clean(item, depth=depth + 1) for item in value)
    if isinstance(value, str):
        return redact(value)
    return value
