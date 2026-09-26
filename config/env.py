"""Переменные окружения установки — ТЗ 11.10.

``parse_env`` разбирает источник и возвращает результат с полным перечнем ошибок, ничего не бросая.
``load_env`` вызывается при старте процесса (настройки Django, ``manage.py``) и превращает ошибки
в исключение: приложение не должно стартовать с неверной конфигурацией.

Тексты ошибок не содержат значений переменных: среди них пароли и строки подключения.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

SESSION_SECRET_MIN_LENGTH: Final = 50
REDIS_DB_MAX: Final = 15

_SENTRY_DSN: Final = re.compile(r"https?://[^:@/\s]+(?::[^@/\s]*)?@[^/\s]+/(?:[^/\s]+/)*\d+")
_TELEGRAM_BOT_TOKEN: Final = re.compile(r"\d+:[A-Za-z0-9_-]{30,}")
_INTEGER: Final = re.compile(r"-?\d+")

type _Check = Callable[[str], str | None]


class SmtpTls(StrEnum):
    STARTTLS = "starttls"
    TLS = "tls"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Env:
    database_url: str
    redis_url: str
    redis_db_state: int
    redis_db_cache: int
    app_base_url: str
    #: Часовой пояс отображения (ТЗ 4.0). Хранение всегда в UTC, меняется только показ.
    app_time_zone: str
    session_secret: str
    auth_2fa_enabled: bool
    dns_timeout_ms: int
    resolve_concurrency: int
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_tls: SmtpTls
    sentry_dsn: str | None
    sentry_environment: str
    sentry_traces_sample_rate: float
    # Сверх таблицы 11.10: подключение канала Telegram по 8.3, см. Q-A2.
    telegram_bot_token: str | None


@dataclass(frozen=True, slots=True)
class EnvError:
    variable: str
    reason: str


@dataclass(frozen=True, slots=True)
class EnvParseResult:
    """``env`` задан тогда и только тогда, когда ``errors`` пуст."""

    env: Env | None
    errors: tuple[EnvError, ...]


class EnvironmentConfigError(ImproperlyConfigured):
    """Конфигурация окружения неверна. Бросается только из ``load_env``."""

    def __init__(self, errors: tuple[EnvError, ...]) -> None:
        self.errors = errors
        lines = "\n".join(f"  {error.variable}: {error.reason}" for error in errors)
        super().__init__(f"Приложение не запущено: ошибка в переменных окружения.\n{lines}")


def load_env(source: Mapping[str, str] | None = None) -> Env:
    result = parse_env(os.environ if source is None else source)
    if result.env is None:
        raise EnvironmentConfigError(result.errors)
    return result.env


def parse_env(source: Mapping[str, str]) -> EnvParseResult:
    read = _Reader(source)

    database_url = read.required("DATABASE_URL", _database_url_problem)
    redis_url = read.required("REDIS_URL", _redis_url_problem)
    redis_db_state = read.integer("REDIS_DB_STATE", default=0, minimum=0, maximum=REDIS_DB_MAX)
    redis_db_cache = read.integer("REDIS_DB_CACHE", default=1, minimum=0, maximum=REDIS_DB_MAX)
    if redis_db_state == redis_db_cache and not read.failed("REDIS_DB_STATE", "REDIS_DB_CACHE"):
        read.fail(
            "REDIS_DB_CACHE",
            "должна отличаться от REDIS_DB_STATE: у кэша и рабочего состояния нет общего "
            "пространства ключей",
        )
    app_base_url = read.required("APP_BASE_URL", _app_base_url_problem)
    app_time_zone = read.optional("APP_TIME_ZONE", _time_zone_problem) or "UTC"
    session_secret = read.required("SESSION_SECRET", _session_secret_problem)
    auth_2fa_enabled = read.boolean("AUTH_2FA_ENABLED", default=False)
    dns_timeout_ms = read.integer("DNS_TIMEOUT_MS", default=3000, minimum=1, maximum=60_000)
    resolve_concurrency = read.integer(
        "RESOLVE_CONCURRENCY", default=20, minimum=1, maximum=1000
    )

    smtp_host = read.optional("SMTP_HOST")
    smtp_port = read.integer("SMTP_PORT", default=587, minimum=1, maximum=65_535)
    smtp_user = read.optional("SMTP_USER")
    smtp_password = read.optional("SMTP_PASSWORD")
    smtp_from = read.optional("SMTP_FROM", _email_problem)
    smtp_tls = read.choice("SMTP_TLS", default=SmtpTls.STARTTLS)
    if smtp_host is not None:
        if smtp_user is not None and smtp_password is None:
            read.fail("SMTP_PASSWORD", "обязательна, когда заданы SMTP_HOST и SMTP_USER")
        if smtp_password is not None and smtp_user is None:
            read.fail("SMTP_USER", "обязательна, когда заданы SMTP_HOST и SMTP_PASSWORD")
        if smtp_from is None:
            read.fail("SMTP_FROM", "обязательна, когда задан SMTP_HOST")

    sentry_dsn = read.optional("SENTRY_DSN", _sentry_dsn_problem)
    sentry_environment = read.text("SENTRY_ENVIRONMENT", default="production")
    sentry_traces_sample_rate = read.fraction("SENTRY_TRACES_SAMPLE_RATE", default=0.0)

    telegram_bot_token = read.optional("TELEGRAM_BOT_TOKEN", _telegram_bot_token_problem)

    if read.errors:
        return EnvParseResult(env=None, errors=tuple(read.errors))

    env = Env(
        database_url=database_url,
        redis_url=redis_url,
        redis_db_state=redis_db_state,
        redis_db_cache=redis_db_cache,
        app_base_url=app_base_url.rstrip("/"),
        app_time_zone=app_time_zone,
        session_secret=session_secret,
        auth_2fa_enabled=auth_2fa_enabled,
        dns_timeout_ms=dns_timeout_ms,
        resolve_concurrency=resolve_concurrency,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        smtp_from=smtp_from,
        smtp_tls=smtp_tls,
        sentry_dsn=sentry_dsn,
        sentry_environment=sentry_environment,
        sentry_traces_sample_rate=sentry_traces_sample_rate,
        telegram_bot_token=telegram_bot_token,
    )
    return EnvParseResult(env=env, errors=())


def django_database(database_url: str) -> dict[str, object]:
    """``DATABASES["default"]`` из проверенного ``DATABASE_URL``."""
    parts = urlsplit(database_url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or ""),
        "OPTIONS": dict(parse_qsl(parts.query)),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


class _Reader:
    """Читает переменные и копит ошибки, а не падает на первой."""

    def __init__(self, source: Mapping[str, str]) -> None:
        self._source = source
        self.errors: list[EnvError] = []

    def fail(self, variable: str, reason: str) -> None:
        self.errors.append(EnvError(variable=variable, reason=reason))

    def failed(self, *variables: str) -> bool:
        return any(error.variable in variables for error in self.errors)

    def optional(self, variable: str, check: _Check | None = None) -> str | None:
        value = self._source.get(variable, "").strip() or None
        if value is not None and check is not None:
            reason = check(value)
            if reason is not None:
                self.fail(variable, reason)
        return value

    def required(self, variable: str, check: _Check) -> str:
        value = self.optional(variable, check)
        if value is None:
            self.fail(variable, "обязательная переменная не задана")
            return ""
        return value

    def text(self, variable: str, *, default: str) -> str:
        return self.optional(variable) or default

    def integer(self, variable: str, *, default: int, minimum: int, maximum: int) -> int:
        raw = self.optional(variable)
        if raw is None:
            return default
        if _INTEGER.fullmatch(raw) is None:
            self.fail(variable, "ожидается целое число")
            return default
        value = int(raw)
        if not minimum <= value <= maximum:
            self.fail(variable, f"допустимы значения от {minimum} до {maximum}")
            return default
        return value

    def fraction(self, variable: str, *, default: float) -> float:
        raw = self.optional(variable)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            value = float("nan")
        if not 0.0 <= value <= 1.0:
            self.fail(variable, "ожидается число от 0 до 1")
            return default
        return value

    def boolean(self, variable: str, *, default: bool) -> bool:
        raw = self.optional(variable)
        if raw is None:
            return default
        if raw in {"true", "false"}:
            return raw == "true"
        self.fail(variable, "ожидается true или false")
        return default

    def choice[E: StrEnum](self, variable: str, *, default: E) -> E:
        raw = self.optional(variable)
        if raw is None:
            return default
        choices = type(default)
        try:
            return choices(raw)
        except ValueError:
            allowed = ", ".join(member.value for member in choices)
            self.fail(variable, f"допустимые значения: {allowed}")
            return default


def _has_valid_port(parts: SplitResult) -> bool:
    try:
        _ = parts.port
    except ValueError:
        return False
    return True


def _database_url_problem(value: str) -> str | None:
    parts = urlsplit(value)
    if (
        parts.scheme not in {"postgres", "postgresql"}
        or not parts.hostname
        or not parts.path.strip("/")
        or not _has_valid_port(parts)
    ):
        return "ожидается адрес вида postgresql://пользователь:пароль@хост:порт/база"
    return None


def _redis_url_problem(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme not in {"redis", "rediss"} or not parts.hostname or not _has_valid_port(parts):
        return "ожидается адрес вида redis://хост:порт"
    if parts.path not in {"", "/"} or "db" in dict(parse_qsl(parts.query)):
        return "номер базы задаётся переменными REDIS_DB_STATE и REDIS_DB_CACHE, а не в адресе"
    return None


def _app_base_url_problem(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or not _has_valid_port(parts):
        return "ожидается адрес вида https://хост или https://хост:порт"
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        return "адрес установки задаётся без пути, параметров и якоря"
    return None


def _time_zone_problem(value: str) -> str | None:
    """Часовой пояс отображения — ТЗ 4.0.

    Имя проверяется сразу: опечатка вроде «Europe/Moskva» иначе всплыла бы уже в рантайме,
    при первой же отрисовке страницы.
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return "неизвестный часовой пояс; ожидается имя вида Europe/Moscow или UTC"
    return None


def _session_secret_problem(value: str) -> str | None:
    if len(value) < SESSION_SECRET_MIN_LENGTH:
        return f"должна быть не короче {SESSION_SECRET_MIN_LENGTH} символов"
    return None


def _email_problem(value: str) -> str | None:
    if "@" not in value:
        return "ожидается почтовый адрес"
    return None


def _sentry_dsn_problem(value: str) -> str | None:
    if _SENTRY_DSN.fullmatch(value) is None:
        return "ожидается DSN вида https://ключ@хост/номер_проекта"
    return None


def _telegram_bot_token_problem(value: str) -> str | None:
    if _TELEGRAM_BOT_TOKEN.fullmatch(value) is None:
        return "ожидается токен бота вида 123456789:AA… из @BotFather"
    return None
