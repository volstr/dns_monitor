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
        'Приложение не запущено: не задана переменная ALLOWED_HOSTS.\n'
        '  ALLOWED_HOSTS: обязательная переменная не задана. Перечислите имена, под которыми '
        'приложение отвечает, через запятую: dns-monitoring.example.com, 10.20.0.7'
    )
    raise ImproperlyConfigured(message)

# Схему запроса приложение узнаёт от обратного прокси. Без этого Django считает по соединению,
# а к нему приходит обычный http от nginx: `request.is_secure()` всегда False, перенаправления
# получаются на http, а защищённые куки не ставятся, даже когда снаружи настоящий TLS.
#
# Доверять заголовку можно **только** потому, что выполнены оба условия (`docker-compose.yml`):
# nginx ставит `X-Forwarded-Proto` сам на каждый запрос, затирая присланный клиентом, и портов
# наружу у `web` нет — снаружи до gunicorn не дотянуться в обход прокси. Открыть порт web в
# бою значит сделать этот заголовок подделываемым.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

if ENV.app_base_url.startswith('https://'):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
