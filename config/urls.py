"""
Корневые маршруты.

Админка Django подключается **по наличию приложения**, а не по флагу `DEBUG`: она установлена
только в `config/settings/dev.py`, и в боевом режиме маршрута `admin/` не существует — его
нельзя включить ни переменной окружения, ни опечаткой в настройках. Почему так — `config/admin.py`.
"""

from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path

from apps.monitoring.views import Healthcheck

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/", Healthcheck.as_view(), name="healthcheck"),
]

if "django.contrib.admin" in settings.INSTALLED_APPS:
    from config.admin import site as dev_admin

    urlpatterns.append(path("admin/", dev_admin.urls))

urlpatterns += [
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
]
