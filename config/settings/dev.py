"""Режим разработчика: ``docker-compose-dev.yml``."""

from .base import *  # noqa: F403
from .base import APP_HOST, INSTALLED_APPS, LOGGING, TEMPLATES

DEBUG = True
ALLOWED_HOSTS = [APP_HOST, "localhost", "127.0.0.1"]

# Журнал разработчик читает глазами в терминале, а не собирает по полям.
LOGGING["handlers"]["console"]["formatter"] = "plain"

# Админка Django — только здесь. В `prod` её нет вовсе: это второй путь к тем же таблицам,
# минующий сервисы, а с ними и предохранители 3.1, журнал и разбор белого списка. Подробности
# и перечень обходимого — в `config/admin.py`. Маршрут `admin/` появляется по наличию
# приложения, поэтому в боевом режиме его нет и добавлять нечего.
INSTALLED_APPS = [*INSTALLED_APPS, "django.contrib.admin"]

# Админке нужен `user` в контексте шаблона. В общий список настроек он не добавлен намеренно:
# в продукте вошедший участник приходит через `apps.accounts.access.navigation`, и второй
# источник того же значения только запутал бы.
TEMPLATES[0]["OPTIONS"]["context_processors"] = [
    *TEMPLATES[0]["OPTIONS"]["context_processors"],
    "django.contrib.auth.context_processors.auth",
]
