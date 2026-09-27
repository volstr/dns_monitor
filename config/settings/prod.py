"""Боевой режим."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, ENV

DEBUG = False

if not ALLOWED_HOSTS:
    # Здесь, а не в `config/env.py`: переменная обязательна не сама по себе, а в этом режиме,
    # и требовать её от разработчика значило бы требовать выдуманное значение.
    #
    # Пустой список в бою — не мелочь. Django при `DEBUG=False` отвергнет тогда любой запрос,
    # и приложение будет выглядеть сломанным, не называя причины. Лучше не стартовать.
    message = (
        "Приложение не запущено: не задана переменная ALLOWED_HOSTS.\n"
        "  ALLOWED_HOSTS: обязательная переменная не задана. Перечислите имена, под которыми "
        "приложение отвечает, через запятую: dns-monitoring.example.com, 10.20.0.7"
    )
    raise ImproperlyConfigured(message)

if ENV.app_base_url.startswith("https://"):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
