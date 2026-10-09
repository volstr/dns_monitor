"""WSGI-приложение для gunicorn."""

import os

from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    raise ImproperlyConfigured(
        'Приложение не запущено: не задана DJANGO_SETTINGS_MODULE '
        '(config.settings.dev или config.settings.prod).'
    )

application = get_wsgi_application()
