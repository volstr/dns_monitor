"""Общие настройки. Режимы — ``dev``, ``prod``, ``test``.

Переменные окружения читаются и проверяются при импорте: ошибка конфигурации останавливает старт
процесса, а не всплывает при первом обращении (ТЗ 11.10).
"""

from pathlib import Path
from urllib.parse import urlsplit

from config.env import django_database, load_env

ENV = load_env()

BASE_DIR = Path(__file__).resolve().parents[2]

APP_BASE_URL = ENV.app_base_url
APP_HOST = urlsplit(ENV.app_base_url).hostname or ""

SECRET_KEY = ENV.session_secret
DEBUG = False
ALLOWED_HOSTS = [APP_HOST]
CSRF_TRUSTED_ORIGINS = [ENV.app_base_url]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    # django.contrib.auth подключается на этапе 2 вместе с моделью участника: AUTH_USER_MODEL
    # нельзя сменить после первой миграции auth без пересоздания базы.
    "apps.accounts",
    "apps.catalog",
    "apps.monitoring",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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
            ],
        },
    },
]

DATABASES = {"default": django_database(ENV.database_url)}

# TODO(ТЗ 11.11, 11.12): подключение к Redis — две логические базы, обёртка, физически
# запрещающая ключ без TTL, и CACHES. Этап 4, вместе с пробой работоспособности и 503 (Q-E4).
# До тех пор приложение к Redis не обращается, поэтому CACHES намеренно не задан: пустой
# штатный локальный кэш лучше, чем настроенное подключение, которым никто не пользуется.

# ТЗ 4.0: все первичные и внешние ключи — bigint.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ru"
# ТЗ 4.0: хранение в UTC. Часовой пояс отображения установки — Q-A3, этап 6.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Структурные логи — этап 8.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

# TODO(ТЗ 11.14): инициализация sentry-sdk при заданном SENTRY_DSN — этап 8.
