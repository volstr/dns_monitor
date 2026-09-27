"""Общие настройки. Режимы — ``dev``, ``prod``, ``test``.

Переменные окружения читаются и проверяются при импорте: ошибка конфигурации останавливает старт
процесса, а не всплывает при первом обращении (ТЗ 11.10).
"""

from pathlib import Path
from urllib.parse import urlsplit

from config.env import django_database, load_env
from core.observability.setup import configure_sentry

ENV = load_env()

BASE_DIR = Path(__file__).resolve().parents[2]

APP_BASE_URL = ENV.app_base_url
APP_HOST = urlsplit(ENV.app_base_url).hostname or ""

SECRET_KEY = ENV.session_secret
DEBUG = False
ALLOWED_HOSTS = [APP_HOST]
CSRF_TRUSTED_ORIGINS = [ENV.app_base_url]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    # `django.contrib.sessions` намеренно не подключён: его модель создала бы таблицу сессий,
    # которой неоткуда наполняться — сессии живут в Redis (Q-D20). Промежуточный слой и базовый
    # класс хранилища из этого пакета работают и без регистрации приложения.
    "apps.accounts",
    "apps.catalog",
    "apps.monitoring",
    "apps.notifications",
]

MIDDLEWARE = [
    # Первым: при аварии инфраструктуры ответ 503 отдаётся раньше любой другой работы (11.13).
    "config.middleware.InfrastructureHealthMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                # Видимость пунктов меню по роли (ТЗ 3, Q-D19). Доступ ограничивают декораторы
                # в том же модуле, а не этот процессор.
                "apps.accounts.access.navigation",
            ],
        },
    },
]

DATABASES = {"default": django_database(ENV.database_url)}

# Рабочее состояние (11.11) живёт в `REDIS_DB_STATE` и ходит туда через `RedisStateStore`:
# обёртка физически не позволяет записать ключ без срока жизни.
#
# TODO(ТЗ 11.12): кэша нет вовсе — ни `CACHES`, ни единого ключа `cache:`. Это не забытая
# строка настроек, а незаписанный слой: таблица 11.12 задаёт шесть видов ключей и **два разных
# правила обновления** (представления — по сроку, настройки и белый список — по версии в ключе),
# и второе из них без кэша не проверить. `CACHES` настраивается вместе с первым таким ключом:
# подключение, которым никто не пользуется, хуже его отсутствия.

# ТЗ 4.0: все первичные и внешние ключи — bigint.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ТЗ 3: почта служит логином. Сменить после первой миграции auth нельзя.
AUTH_USER_MODEL = "accounts.Member"

# ТЗ 3, Q-D20: сессии в Redis, в базе состояния. Штатная таблица сессий не используется.
SESSION_ENGINE = "apps.accounts.sessions"
# Срок сессии отсчитывается от бездействия (ТЗ 3), поэтому окно скользящее: каждая запись
# продлевает ключ. Сам срок — настройка продукта, а не константа настроек Django.
SESSION_SAVE_EVERY_REQUEST = True

LANGUAGE_CODE = "ru"
# ТЗ 4.0: «хранение в UTC, отображение в часовом поясе установки». USE_TZ оставляет хранение в
# UTC, а TIME_ZONE задаёт только показ. Пояс приходит из APP_TIME_ZONE (Q-A3), по умолчанию UTC.
TIME_ZONE = ENV.app_time_zone
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Структурные логи. `json` — боевой формат: три процесса пишут в общий поток, и разбирать их
# приходится по полям. `plain` остаётся для режима разработчика, где журнал читают глазами.
LOG_FORMAT = "json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # Идентификатор обхода — контекст события по 11.14: «чтобы все ошибки одного цикла
        # проверок нашлись вместе».
        "run": {"()": "core.observability.logs.RunIdFilter"},
    },
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        "json": {"()": "core.observability.logs.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["run"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Отправка ошибок в Sentry — ТЗ 11.14. Подключение необязательное: при пустом SENTRY_DSN
# функция ничего не делает и приложение работает как обычно (критерий 67).
SENTRY_ENABLED = configure_sentry(
    dsn=ENV.sentry_dsn,
    environment=ENV.sentry_environment,
    traces_sample_rate=ENV.sentry_traces_sample_rate,
    release=ENV.app_release,
)
